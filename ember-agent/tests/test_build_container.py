"""Isolated Docker transport checks using an explicitly provided local image."""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from ember_agent.runtime.build_container import (
    MODEL_PORT,
    PYTHON,
    RELAY_HEADER,
    BuildContainer,
    _protected_request,
)
from ember_agent.runtime.task_store import TaskConflict


def test_build_image_requires_full_id() -> None:
    with pytest.raises(TaskConflict, match="build_image_id_required"):
        BuildContainer("latest", model_ingress_port=8000)


def test_absolute_docker_and_local_endpoint_only(monkeypatch: pytest.MonkeyPatch) -> None:
    image = "sha256:" + "1" * 64
    with pytest.raises(TaskConflict, match="build_absolute_docker_required"):
        BuildContainer(image, model_ingress_port=8000, docker=Path("relative/docker"))
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "tcp://192.0.2.1:2375")

    async def verify() -> None:
        with pytest.raises(TaskConflict, match="build_local_docker_required"):
            async with BuildContainer(image, model_ingress_port=8000, docker=Path.cwd() / "docker"):
                pytest.fail("remote Docker endpoint accepted")

    asyncio.run(verify())


def test_protected_request_never_forwards_bearer_or_extra_bytes() -> None:
    async def verify() -> None:
        reader = asyncio.StreamReader()
        key = b"synthetic-only-token-not-sent-to-build"
        reader.feed_data(
            b"HEAD /item?q=1 HTTP/1.1\r\nHost: attacker\r\nX-Ember-Build-Relay: "
            + key
            + b"\r\n\r\nGET /second HTTP/1.1\r\n\r\n"
        )
        assert await _protected_request(reader, key, 19080) == (
            b"HEAD /item?q=1 HTTP/1.1\r\nHost: 127.0.0.1:19080\r\n"
            b"Accept-Encoding: identity\r\nConnection: close\r\n\r\n"
        )

    asyncio.run(verify())


@pytest.mark.integration
def test_build_container_relays_and_cleanup() -> None:
    image = os.environ.get("EMBER_TEST_BUILD_IMAGE_ID")
    if not image:
        pytest.skip("set EMBER_TEST_BUILD_IMAGE_ID to a local build fixture image ID")
    asyncio.run(_verify_relays(image))


async def _verify_relays(image: str) -> None:
    active = asyncio.Event()
    disconnected = asyncio.Event()

    async def reply_after_eof(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            first = await reader.read(1)
            if first == b"!":
                active.set()
                if not await reader.read():
                    disconnected.set()
                return
            writer.write(b"host:" + first + await reader.read())
            await writer.drain()
        finally:
            writer.close()

    async with await asyncio.start_server(reply_after_eof, "127.0.0.1", 0) as ingress:
        task = BuildContainer(image, model_ingress_port=ingress.sockets[0].getsockname()[1])
        async with asyncio.timeout(45), task:
            await task.write_file("a/content", b"foobar")
            assert await task.read_file("a/content") == b"foobar"
            with pytest.raises(TaskConflict, match="build_exec_failed"):
                await task.read_file("a/content", max_bytes=1)
            with pytest.raises(TaskConflict, match="build_exec_output_limit"):
                await task.exec(PYTHON, "-c", "print('too large')", max_output=1)
            with pytest.raises(TaskConflict, match="build_invalid_path"):
                await task.read_file("a/../content")
            assert (
                await task.exec(
                    PYTHON,
                    "-c",
                    """
import os, pathlib, socket, sys
assert os.getuid() == 10001
assert os.getcwd() == '/work'
assert socket.if_nametoindex('lo')
assert not pathlib.Path('/var/run/docker.sock').exists()
assert os.statvfs('/').f_flag & os.ST_RDONLY
try:
    socket.create_connection(('192.0.2.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('unexpected egress route')
assert 'CapEff:\\t0000000000000000' in pathlib.Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\\t1' in pathlib.Path('/proc/self/status').read_text()
s = socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=3)
s.sendall(b'a' * 262144)
s.shutdown(socket.SHUT_WR)
assert s.makefile('rb').read() == b'host:' + b'a' * 262144
""",
                    str(task.model_port),
                )
                == b""
            )

            await task.exec(
                PYTHON,
                "-c",
                """
import socket, subprocess, sys, time
server = subprocess.Popen([sys.executable, '-m', 'http.server', '18741', '--bind', '127.0.0.1'],
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True)
deadline = time.monotonic() + 3
while server.poll() is None and time.monotonic() < deadline:
    try:
        with socket.create_connection(('127.0.0.1', 18741), timeout=0.2):
            break
    except OSError:
        time.sleep(0.02)
else:
    raise AssertionError('fixture HTTP server did not start')
""",
            )
            key = "local-fixture-authorization-must-not-reach-container"
            with pytest.raises(TaskConflict, match="build_forward_unavailable"):
                await task.forward_port(MODEL_PORT, relay_key=key)
            host_port = await task.forward_port(18741, relay_key=key)
            with pytest.raises(TaskConflict, match="build_forward_policy_conflict"):
                await task.forward_port(18741)
            auth = f"{RELAY_HEADER}: {key}\r\n".encode("ascii")

            async def request(
                wire: bytes = b"GET /a/content HTTP/1.0\r\n" + auth + b"\r\n",
            ) -> bytes:
                reader, writer = await asyncio.open_connection("127.0.0.1", host_port)
                try:
                    writer.write(wire)
                    await writer.drain()
                    writer.write_eof()
                    return await reader.read()
                except ConnectionResetError:
                    return b""
                finally:
                    writer.close()

            assert (await request()).endswith(b"foobar")
            assert not await request(b"GET / HTTP/1.1\r\n\r\n")
            assert not await request(b"POST / HTTP/1.1\r\n" + auth + b"\r\n")
            assert not await request(b"GET / HTTP/1.1\r\n" + auth * 2 + b"\r\n")
            assert not await request(b"GET / HTTP/1.1\r\n" + auth + b"Content-Length: 1\r\n\r\nx")
            async with asyncio.TaskGroup() as jobs:
                jobs.create_task(
                    task.exec(
                        PYTHON,
                        "-c",
                        "import socket; s=socket.create_connection(('127.0.0.1', 17631)); "
                        "s.settimeout(5); s.sendall(b'!'); assert s.recv(1) == b''",
                    )
                )
                async with asyncio.timeout(5):
                    await active.wait()
                    await task.stop_model_relay()
                    await disconnected.wait()
            assert (await request()).endswith(b"foobar")
            await task.close_forward(18741)
            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.1", host_port)

    check = await asyncio.create_subprocess_exec(
        "docker",
        "container",
        "inspect",
        task.name,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert await check.wait() == 1
