"""Owned Linux task container and fixed-destination TCP relays over Docker exec pipes."""

import asyncio
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Self, cast

from .task_store import TaskConflict
from .worker_process import settled

MODEL_PORT = 17631
WORK_DIR = "/work"
PYTHON = "/usr/local/bin/python3"
OPENCODE = "/usr/local/bin/opencode"
_LOOPBACK = "127.0.0.1"
_CHUNK = 65536
_RELAY_SECONDS = 600
_COMMAND_SECONDS = 20
_MAX_EXEC_BYTES = 16 * 1024 * 1024
_MAX_FORWARD_CLIENTS = 12
_MODEL_WORKERS = 4
_IMAGE = re.compile(r"sha256:[a-f0-9]{64}")
RELAY_HEADER = "X-Ember-Build-Relay"
_HTTP_LIMIT = 8192
_HTTP_REQUEST = re.compile(rb"(GET|HEAD) (/[!-~]*) HTTP/1\.[01]")
_HTTP_FIELD = re.compile(rb"([!#$%&'*+.^_`|~0-9A-Za-z-]+):[ \t]*([\x20-\x7e]*)")
_LOG = logging.getLogger(__name__)

# Container side only. docker exec has no write-half-close on stdout independent of process
# exit; frame data and EOF explicitly. Pipe EOF without an EOF frame aborts the socket.
_RELAY = f"""
import array, os, select, signal, socket, struct, sys, threading
signal.alarm({_RELAY_SECONDS})
mode, target = sys.argv[1:]
if mode == 'connect':
    peer = socket.create_connection(({_LOOPBACK!r}, int(target)), timeout=3)
    peer.settimeout(None)
else:
    with socket.socket(socket.AF_UNIX) as handoff:
        handoff.connect('\\0' + target)
        if 0 in select.select([0, handoff], [], [])[0]:
            raise SystemExit(0)
        kind = array.array('i')
        msg, control, flags, address = handoff.recvmsg(1, socket.CMSG_SPACE(kind.itemsize))
        if msg != b'F' or len(control) != 1 or control[0][:2] != (
            socket.SOL_SOCKET, socket.SCM_RIGHTS
        ):
            raise SystemExit(1)
        kind.frombytes(control[0][2])
        peer = socket.socket(fileno=kind[0])


finished = threading.Event()


def read_pipe(size):
    data = bytearray()
    while len(data) < size:
        part = os.read(0, size - len(data))
        if not part:
            break
        data.extend(part)
    return data


def receive():
    try:
        while True:
            header = read_pipe(4)
            if len(header) != 4:
                break
            size = struct.unpack('!I', header)[0]
            if size == 0:
                peer.shutdown(socket.SHUT_WR)
                finished.set()
                # Watch pipe EOF so cancellation aborts the response half too.
                os.read(0, 1)
                break
            if size > {_CHUNK}:
                break
            data = read_pipe(size)
            if len(data) != size:
                break
            peer.sendall(data)
    except OSError:
        pass
    finally:
        finished.set()
        try:
            peer.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


with peer:
    sys.stdout.buffer.write(b'R')
    sys.stdout.buffer.flush()
    incoming = threading.Thread(target=receive, daemon=True)
    incoming.start()
    while True:
        data = peer.recv({_CHUNK})
        sys.stdout.buffer.write(struct.pack('!I', len(data)) + data)
        sys.stdout.buffer.flush()
        if not data:
            break
    finished.wait()
"""

# Linux abstract Unix socket stays in this network namespace and needs no writable path.
# Hand off accepted descriptors; fixed worker/backlog count bounds concurrency and memory.
_MODEL_LISTENER = f"""
import array, os, select, socket, sys
with socket.socket() as tcp, socket.socket(socket.AF_UNIX) as handoff:
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(({_LOOPBACK!r}, {MODEL_PORT}))
    tcp.listen({_MODEL_WORKERS * 2})
    handoff.bind('\\0' + sys.argv[1])
    handoff.listen({_MODEL_WORKERS})
    os.write(1, b'R')
    while True:
        if 0 in select.select([0, handoff], [], [])[0]:
            break
        with handoff.accept()[0] as relay:
            ready = select.select([0, relay, tcp], [], [])[0]
            if 0 in ready:
                break
            if relay in ready:
                continue
            with tcp.accept()[0] as client:
                try:
                    relay.sendmsg([b'F'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                           array.array('i', [client.fileno()]))])
                except OSError:
                    continue
"""

_LAUNCH = """
import select, subprocess, sys
with subprocess.Popen(sys.argv[1:], stdin=subprocess.DEVNULL) as server:
    while server.poll() is None:
        if select.select([0], [], [], 0.1)[0]:
            # Stop this exact child, not its process group or detached build services.
            server.terminate()
            try:
                server.wait(2)
            except subprocess.TimeoutExpired:
                server.kill()
            break
raise SystemExit(server.returncode)
"""

_FILE = """
import os, stat, sys
mode, root, bound, relative = sys.argv[1:]
bound = int(bound)
parts = relative.split('/')
if any(part in ('', '.', '..') for part in parts):
    raise SystemExit(1)
flags = os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW
parent = os.open(root, flags)
try:
    for part in parts[:-1]:
        if mode == 'write':
            try:
                os.mkdir(part, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
        child = os.open(part, flags, dir_fd=parent)
        os.close(parent)
        parent = child
    if mode == 'write':
        data = sys.stdin.buffer.read(bound + 1)
        if len(data) != bound:
            raise SystemExit(1)
        fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
    else:
        inode = os.open(parts[-1], os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(inode)
            if not stat.S_ISREG(info.st_mode) or info.st_size > bound:
                raise SystemExit(1)
            with open(f'/proc/self/fd/{inode}', 'rb') as stream:
                data = stream.read(bound + 1)
            if len(data) > bound:
                raise SystemExit(1)
            sys.stdout.buffer.write(data)
        finally:
            os.close(inode)
finally:
    os.close(parent)
"""


def _port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise TaskConflict("build_invalid_port")
    return port


def _path(relative: str) -> str:
    if (
        "\\" in relative
        or "\x00" in relative
        or len(relative) > 4096
        or any(part in ("", ".", "..") for part in relative.split("/"))
    ):
        raise TaskConflict("build_invalid_path")
    return relative


async def _stop(
    process: asyncio.subprocess.Process, *, grace: float = 0, require_grace: bool = False
) -> None:
    if process.stdin is not None and not process.stdin.is_closing():
        # Drop queued input immediately: EOF also signals container-side supervisors.
        process.stdin.transport.abort()
    if grace and process.returncode is None:
        with suppress(TimeoutError):
            async with asyncio.timeout(grace):
                await process.wait()
    forced = process.returncode is None
    if forced:
        with suppress(ProcessLookupError):
            process.kill()
    # A cancelled reader may have paused the subprocess pipe and prevented wait() finishing.
    transport = cast(asyncio.SubprocessTransport, vars(process)["_transport"])
    for fd in (1, 2):
        pipe = transport.get_pipe_transport(fd)
        if pipe is not None:
            pipe.close()
    async with asyncio.timeout(3):
        await process.wait()
    if require_grace and forced:
        raise TaskConflict("build_opencode_stop_unverified")


async def _spawn(
    command: tuple[str, ...], *, interactive: bool, env: Mapping[str, str] | None = None
) -> asyncio.subprocess.Process:
    creation = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            limit=_CHUNK,
            env=env,
        )
    )
    try:
        return await asyncio.shield(creation)
    except asyncio.CancelledError:
        process = await settled(creation)
        await settled(asyncio.create_task(_stop(process)))
        raise
    except OSError as error:
        raise TaskConflict("build_docker_unavailable") from error


async def _finish[T](operation: asyncio.Task[T]) -> T:
    """Complete owned teardown before propagating cancellation."""
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        await settled(operation)
        raise


async def _write_input(writer: asyncio.StreamWriter, data: bytes) -> None:
    try:
        for offset in range(0, len(data), _CHUNK):
            writer.write(data[offset : offset + _CHUNK])
            await writer.drain()
    finally:
        writer.close()


async def _bounded_command(
    command: tuple[str, ...],
    data: bytes | None,
    maximum: int,
    timeout: float,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[int, bytes]:
    async with asyncio.timeout(timeout):
        process = await _spawn(command, interactive=data is not None, env=env)
        assert process.stdout is not None
        send = None
        try:
            if data is not None:
                assert process.stdin is not None
                send = asyncio.create_task(_write_input(process.stdin, data))
            output = bytearray()
            while part := await process.stdout.read(min(_CHUNK, maximum + 1 - len(output))):
                output.extend(part)
                if len(output) > maximum:
                    raise TaskConflict("build_exec_output_limit")
            if send is not None:
                with suppress(BrokenPipeError, ConnectionResetError):
                    await send
            return await process.wait(), bytes(output)
        finally:
            if send is not None:
                send.cancel()
                await asyncio.gather(send, return_exceptions=True)
            await _finish(asyncio.create_task(_stop(process)))


async def _to_exec(reader: asyncio.StreamReader, pipe: asyncio.StreamWriter) -> None:
    while True:
        data = await reader.read(_CHUNK)
        pipe.write(len(data).to_bytes(4, "big") + data)
        await pipe.drain()
        if not data:
            return


async def _from_exec(pipe: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while True:
        size = int.from_bytes(await pipe.readexactly(4), "big")
        if size > _CHUNK:
            raise TaskConflict("build_relay_bad_frame")
        if not size:
            writer.write_eof()
            await writer.drain()
            return
        writer.write(await pipe.readexactly(size))
        await writer.drain()


async def _send_request(pipe: asyncio.StreamWriter, request: bytes) -> None:
    pipe.write(len(request).to_bytes(4, "big") + request + b"\x00" * 4)
    await pipe.drain()


async def _protected_request(reader: asyncio.StreamReader, key: bytes, port: int) -> bytes:
    async with asyncio.timeout(3):
        wire = await reader.readuntil(b"\r\n\r\n")
    if len(wire) > _HTTP_LIMIT:
        raise TaskConflict("build_relay_bad_request")
    request, *fields = wire[:-4].split(b"\r\n")
    match = _HTTP_REQUEST.fullmatch(request)
    if match is None or match[2].startswith(b"//") or b"\\" in match[2] or b"#" in match[2]:
        raise TaskConflict("build_relay_bad_request")
    required = RELAY_HEADER.lower().encode("ascii")
    seen = False
    for field in fields:
        header = _HTTP_FIELD.fullmatch(field)
        if header is None:
            raise TaskConflict("build_relay_bad_request")
        name, value = header[1].lower(), header[2]
        if name == required:
            if seen or not secrets.compare_digest(value, key):
                raise TaskConflict("build_relay_unauthorized")
            seen = True
        elif name in (b"content-length", b"transfer-encoding", b"upgrade", b"expect"):
            raise TaskConflict("build_relay_bad_request")
    if not seen:
        raise TaskConflict("build_relay_unauthorized")
    # One reconstructed request only. No caller headers, body, upgrades or pipelined bytes.
    host = f"127.0.0.1:{port}".encode("ascii")
    return (
        b" ".join(match.groups())
        + b" HTTP/1.1\r\nHost: "
        + host
        + (b"\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n")
    )


async def _relay(
    process: asyncio.subprocess.Process,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    request: bytes | None = None,
) -> None:
    assert process.stdin is not None and process.stdout is not None
    outgoing = (
        _to_exec(reader, process.stdin)
        if request is None
        else _send_request(process.stdin, request)
    )
    pipes = (
        asyncio.create_task(outgoing),
        asyncio.create_task(_from_exec(process.stdout, writer)),
    )
    try:
        await asyncio.gather(*pipes)
        # A drained pipe can still hold frames below high water. Wait until the container
        # exits after consuming the EOF frame before tearing down the Docker attachment.
        async with asyncio.timeout(3):
            if await process.stdout.read(1):
                raise TaskConflict("build_relay_bad_frame")
            writer.close()
            await writer.wait_closed()
    finally:
        for pipe in pipes:
            pipe.cancel()
        await asyncio.gather(*pipes, return_exceptions=True)


def _close_socket(writer: asyncio.StreamWriter) -> None:
    # Do not wait for buffered data to reach a stalled or cancelled peer.
    if not writer.is_closing():
        writer.transport.abort()


class BuildContainer:
    """One disposable task. Only trusted runtime code may call exec or create forwards.

    Model ingress must be a dedicated per-task loopback service with its own scoped bearer.
    Same-UID task processes can access that bearer; never put upstream credentials in the task.
    """

    model_port = MODEL_PORT

    def __init__(
        self, image_id: str, *, model_ingress_port: int, docker: Path | None = None
    ) -> None:
        if not _IMAGE.fullmatch(image_id):
            raise TaskConflict("build_image_id_required")
        if docker is not None and not docker.is_absolute():
            raise TaskConflict("build_absolute_docker_required")
        self._docker_path = docker
        self.image_id = image_id
        self.name = f"ember-build-{secrets.token_hex(16)}"
        self._model_ingress = _port(model_ingress_port)
        self._docker: tuple[str, ...] = ()
        self._docker_env: dict[str, str] | None = None
        self._docker_config: TemporaryDirectory[str] | None = None
        self._target: str | None = None
        self._may_exist = False
        self._close_task: asyncio.Task[None] | None = None
        self._model_server: asyncio.subprocess.Process | None = None
        self._model_workers: list[asyncio.Task[None]] = []
        self._model_stop: asyncio.Task[None] | None = None
        self._opencode: asyncio.subprocess.Process | None = None
        self._forwards: dict[int, tuple[asyncio.Server, int, bytes | None]] = {}
        self._clients: dict[asyncio.Task[None], tuple[int, asyncio.StreamWriter]] = {}

    async def __aenter__(self) -> Self:
        if self._docker or self._close_task is not None:
            raise TaskConflict("build_container_already_used")
        try:
            await self._start()
        except BaseException:
            await self.aclose()
            raise
        return self

    async def __aexit__(self, *exception: object) -> None:
        await self.aclose()

    async def _command(self, *args: str) -> tuple[int, bytes]:
        try:
            return await _bounded_command(
                self._docker + args, None, 4096, _COMMAND_SECONDS, env=self._docker_env
            )
        except TimeoutError as error:
            raise TaskConflict("build_docker_timeout") from error

    async def _start(self) -> None:
        docker = (
            str(self._docker_path)
            if self._docker_path
            else await asyncio.to_thread(shutil.which, "docker")
        )
        if not docker:
            raise TaskConflict("build_docker_unavailable")
        self._docker = (docker,)
        # Capture one local daemon endpoint; never follow a switched global context at cleanup.
        context = os.environ.get("DOCKER_CONTEXT")
        endpoint = os.environ.get("DOCKER_HOST") if not context else None
        if not endpoint:
            status, raw = await self._command(
                "context",
                "inspect",
                "--format",
                "{{.Endpoints.docker.Host}}",
                *((context,) if context else ()),
            )
            if status:
                raise TaskConflict("build_docker_unavailable")
            endpoint = raw.decode("utf-8", errors="replace").strip()
        if not endpoint.startswith(("unix:///", "npipe:////./pipe/")):
            raise TaskConflict("build_local_docker_required")
        # Only discovery sees caller's context/config. Subsequent commands use one local
        # endpoint and empty config (no proxy injection, credential helpers or plugins).
        config_task = asyncio.create_task(
            asyncio.to_thread(TemporaryDirectory, prefix="ember-docker-")
        )
        try:
            directory = await asyncio.shield(config_task)
        except asyncio.CancelledError:
            self._docker_config = await settled(config_task)
            raise
        self._docker_config = directory
        self._docker += ("--config", directory.name, "--host", endpoint)
        self._docker_env = {
            key: value
            for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR")
            if (value := os.environ.get(key)) is not None
        }
        status, raw = await self._command(
            "image",
            "inspect",
            "--format",
            '{{.Id}} {{.Os}} {{if index .Config "Volumes"}}volumes{{else}}empty{{end}}',
            self.image_id,
        )
        if status or raw.strip() != f"{self.image_id} linux empty".encode():
            raise TaskConflict("build_local_linux_image_without_volumes_required")
        self._may_exist = True
        status, raw = await _finish(
            asyncio.create_task(
                self._command(
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    self.name,
                    "--label",
                    f"ember.build.owner={self.name}",
                    "--pull=never",
                    "--network=none",
                    "--read-only",
                    "--init",
                    "--no-healthcheck",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges=true",
                    "--user=10001:10001",
                    "--pids-limit=128",
                    "--memory=768m",
                    "--memory-swap=768m",
                    "--cpus=1",
                    "--ulimit=nofile=1024:1024",
                    "--shm-size=16m",
                    "--log-driver=none",
                    "--tmpfs=/work:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700,size=128m,nr_inodes=8192",
                    "--tmpfs=/tmp:rw,nosuid,nodev,noexec,mode=1777,size=64m,nr_inodes=4096",
                    "--workdir",
                    WORK_DIR,
                    "--env",
                    "HOME=/tmp",
                    "--env",
                    "TMPDIR=/tmp",
                    # Docker's client config can inject proxy credentials even with --network=none.
                    *(
                        f"--env={key}="
                        for name in ("HTTP", "HTTPS", "FTP", "ALL", "NO")
                        for key in (f"{name}_PROXY", f"{name.lower()}_proxy")
                    ),
                    "--entrypoint",
                    PYTHON,
                    self.image_id,
                    "-I",
                    "-S",
                    "-c",
                    "import time; time.sleep(2100)",
                )
            )
        )
        identity = raw.strip()
        if status or not re.fullmatch(rb"[a-f0-9]{64}", identity):
            raise TaskConflict("build_container_start_failed")
        self._target = identity.decode("ascii")
        async with asyncio.timeout(_COMMAND_SECONDS):
            self._model_server = await self._exec_process(
                PYTHON, "-I", "-S", "-c", _MODEL_LISTENER, self.name
            )
            assert self._model_server.stdout is not None
            if await self._model_server.stdout.read(1) != b"R":
                raise TaskConflict("build_model_relay_start_failed")
        self._model_workers = [
            asyncio.create_task(self._model_worker(), name="ember-build-model")
            for _ in range(_MODEL_WORKERS)
        ]

    def _exec_args(self, args: tuple[str, ...], env: Mapping[str, str] | None) -> tuple[str, ...]:
        if self._target is None or self._close_task is not None:
            raise TaskConflict("build_container_inactive")
        environment: list[str] = []
        for key, value in (env or {}).items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in value:
                raise TaskConflict("build_invalid_environment")
            environment.append(f"--env={key}={value}")
        return (
            *self._docker,
            "exec",
            "-i",
            "--workdir",
            WORK_DIR,
            *environment,
            self._target,
            *args,
        )

    async def _exec_process(
        self, *args: str, env: Mapping[str, str] | None = None
    ) -> asyncio.subprocess.Process:
        return await _spawn(self._exec_args(args, env), interactive=True, env=self._docker_env)

    async def exec(
        self,
        *argv: str,
        stdin: bytes | None = None,
        max_output: int = _CHUNK,
        timeout: float = _COMMAND_SECONDS,
        env: Mapping[str, str] | None = None,
    ) -> bytes:
        """Run trusted argv in /work. Return bounded stdout, never Docker stderr or task logs."""
        if not argv or not 0 <= max_output <= _MAX_EXEC_BYTES or not 0 < timeout <= 60:
            raise TaskConflict("build_invalid_exec")
        if stdin is not None and len(stdin) > _MAX_EXEC_BYTES:
            raise TaskConflict("build_exec_input_limit")
        try:
            status, output = await _bounded_command(
                self._exec_args(argv, env), stdin, max_output, timeout, env=self._docker_env
            )
        except TimeoutError as error:
            raise TaskConflict("build_exec_timeout") from error
        if status:
            raise TaskConflict("build_exec_failed")
        return output

    async def write_file(self, relative_path: str, data: bytes) -> None:
        """Create a fresh regular file below /work, refusing existing names and links."""
        await self._write_file(WORK_DIR, relative_path, data)

    async def write_private_file(self, relative_path: str, data: bytes) -> None:
        """Stage a file below /tmp, outside the /work artifact root, before launching tasks."""
        await self._write_file("/tmp", relative_path, data)

    async def _write_file(self, root: str, relative_path: str, data: bytes) -> None:
        await self.exec(
            PYTHON,
            "-I",
            "-S",
            "-c",
            _FILE,
            "write",
            root,
            str(len(data)),
            _path(relative_path),
            stdin=data,
            max_output=0,
        )

    async def read_file(self, relative_path: str, *, max_bytes: int = _CHUNK) -> bytes:
        """Read bounded bytes from a regular file below /work, not following links."""
        return await self.exec(
            PYTHON,
            "-I",
            "-S",
            "-c",
            _FILE,
            "read",
            WORK_DIR,
            str(max_bytes),
            _path(relative_path),
            max_output=max_bytes,
        )

    async def launch_opencode(
        self, *args: str, env: Mapping[str, str] | None = None
    ) -> asyncio.subprocess.Process:
        """Launch attached OpenCode under a stdin-owned supervisor; caller must drain stdout."""
        if self._opencode is not None:
            raise TaskConflict("build_opencode_already_launched")
        self._opencode = await self._exec_process(
            PYTHON, "-I", "-S", "-c", _LAUNCH, OPENCODE, *args, env=env
        )
        return self._opencode

    async def stop_opencode(self) -> None:
        """Stop supervised OpenCode without dropping explicitly retained service forwards."""
        if self._opencode is not None:
            await _finish(asyncio.create_task(_stop(self._opencode, grace=3, require_grace=True)))

    async def _model_worker(self) -> None:
        while True:
            process: asyncio.subprocess.Process | None = None
            try:
                async with asyncio.timeout(_RELAY_SECONDS):
                    process = await self._exec_process(
                        PYTHON, "-I", "-S", "-c", _RELAY, "accept", self.name
                    )
                    assert process.stdout is not None
                    if await process.stdout.read(1) != b"R":
                        await asyncio.sleep(0.2)
                        continue
                    async with asyncio.timeout(3):
                        reader, writer = await asyncio.open_connection(
                            _LOOPBACK, self._model_ingress
                        )
                    try:
                        await _relay(process, reader, writer)
                    finally:
                        _close_socket(writer)
            except (OSError, TimeoutError, asyncio.IncompleteReadError, TaskConflict):
                await asyncio.sleep(0.2)
            finally:
                if process is not None:
                    await _stop(process)

    async def _stop_model(self) -> None:
        for worker in self._model_workers:
            worker.cancel()
        try:
            await asyncio.gather(*self._model_workers, return_exceptions=True)
        finally:
            if self._model_server is not None:
                await _stop(self._model_server, grace=1)

    async def stop_model_relay(self) -> None:
        """Permanently revoke task-to-host transport while host-to-task forwards stay available."""
        if self._model_stop is None:
            self._model_stop = asyncio.create_task(self._stop_model())
        await _finish(self._model_stop)

    async def forward_port(self, container_port: int, *, relay_key: str | None = None) -> int:
        """Forward a loopback port. Service callers must supply a host-only random relay key."""
        port = _port(container_port)
        if self._target is None or self._close_task is not None or port == MODEL_PORT:
            raise TaskConflict("build_forward_unavailable")
        if relay_key is not None and not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", relay_key):
            raise TaskConflict("build_invalid_relay_key")
        key = relay_key.encode("ascii") if relay_key is not None else None
        if port in self._forwards:
            _, host_port, current_key = self._forwards[port]
            if key != current_key:
                raise TaskConflict("build_forward_policy_conflict")
            return host_port
        if len(self._forwards) >= 2:
            raise TaskConflict("build_forward_limit")

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if port not in self._forwards or len(self._clients) >= _MAX_FORWARD_CLIENTS:
                _close_socket(writer)
                return
            client = asyncio.create_task(self._forward_client(port, reader, writer, key))
            self._clients[client] = port, writer
            client.add_done_callback(self._client_done)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((_LOOPBACK, 0))
            listener.setblocking(False)
            host_port = listener.getsockname()[1]
            server = await asyncio.start_server(
                accept, sock=listener.dup(), backlog=16, limit=_HTTP_LIMIT if key else _CHUNK
            )
        if self._close_task is not None or port in self._forwards or len(self._forwards) >= 2:
            server.close()
            await server.wait_closed()
            raise TaskConflict("build_forward_unavailable")
        self._forwards[port] = server, host_port, key
        return host_port

    async def _forward_client(
        self,
        port: int,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        key: bytes | None,
    ) -> None:
        process: asyncio.subprocess.Process | None = None
        try:
            async with asyncio.timeout(_RELAY_SECONDS):
                request = await _protected_request(reader, key, port) if key is not None else None
                process = await self._exec_process(
                    PYTHON, "-I", "-S", "-c", _RELAY, "connect", str(port)
                )
                assert process.stdout is not None
                async with asyncio.timeout(5):
                    if await process.stdout.read(1) != b"R":
                        return
                await _relay(process, reader, writer, request=request)
        except (
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TaskConflict,
        ):
            # A stalled or disconnected client only ends its own relay.
            pass
        finally:
            _close_socket(writer)
            if process is not None:
                await _stop(process)

    def _client_done(self, task: asyncio.Task[None]) -> None:
        self._clients.pop(task, None)
        if not task.cancelled() and task.exception() is not None:
            _LOG.warning("build_forward_cleanup_failed")

    async def close_forward(self, container_port: int) -> None:
        """Close one host listener and its active connections."""
        forward = self._forwards.pop(container_port, None)
        if forward is None:
            return
        server, _, _ = forward
        server.close()
        clients = [task for task, (port, _) in self._clients.items() if port == container_port]
        for client in clients:
            _close_socket(self._clients[client][1])
            client.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        async with asyncio.timeout(3):
            await server.wait_closed()

    async def _owned_id(self) -> str | None:
        if not self._may_exist:
            return None
        status, raw = await self._command(
            "container",
            "ls",
            "--all",
            "--no-trunc",
            "--quiet",
            "--filter",
            f"name=^/{self.name}$",
            "--filter",
            f"label=ember.build.owner={self.name}",
        )
        if not status and not raw.strip():
            return None
        if status or not re.fullmatch(rb"[a-f0-9]{64}", raw.strip()):
            raise TaskConflict("build_container_cleanup_failed")
        return raw.strip().decode("ascii")

    async def _close(self) -> None:
        try:
            await asyncio.gather(
                self.stop_model_relay(),
                *(self.close_forward(port) for port in tuple(self._forwards)),
            )
        finally:
            try:
                identity = self._target or await self._owned_id()
                if identity:
                    status, _ = await self._command("container", "rm", "--force", identity)
                    if status and await self._owned_id() is not None:
                        raise TaskConflict("build_container_cleanup_failed")
            finally:
                if self._opencode is not None:
                    await _stop(self._opencode)

    async def _close_owned(self) -> None:
        try:
            await self._close()
        finally:
            if self._docker_config is not None:
                await asyncio.to_thread(self._docker_config.cleanup)

    async def aclose(self) -> None:
        """Revoke all transports and force-remove only this task's random owned container."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned())
        await _finish(self._close_task)
