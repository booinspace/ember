"""Owned bounded worker pipes, including cancellation during process creation and cleanup."""

import asyncio
import os
import signal
import subprocess
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic, sleep
from typing import TYPE_CHECKING, cast

import psutil

from .task_store import TaskConflict

if TYPE_CHECKING:
    from .windows_job import WindowsJob

WIRE_BYTES = 262144


async def settled[T](operation: asyncio.Task[T]) -> T:
    """Retain an owned operation until it finishes, even under repeated cancellation."""
    while True:
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            if operation.cancelled():
                raise


def _handle(process: asyncio.subprocess.Process) -> int:
    transport = cast(asyncio.SubprocessTransport, vars(process)["_transport"])
    child: object = transport.get_extra_info("subprocess")
    handle: object = getattr(child, "_handle", None)
    if not isinstance(handle, int):
        raise TaskConflict("worker_ownership_unavailable")
    return handle


def _stop_group(identity: int) -> None:
    """Stop fresh-group members and observe retained identities through actual termination."""
    if os.name == "nt":
        raise TaskConflict("worker_posix_group_unavailable")
    members: list[psutil.Process] = []
    try:
        for candidate in psutil.process_iter():
            try:
                if os.getpgid(candidate.pid) == identity:
                    candidate.create_time()
                    members.append(candidate)
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue
    finally:
        try:
            os.killpg(identity, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = monotonic() + 3.0
    while members:
        running: list[psutil.Process] = []
        for member in members:
            try:
                if member.is_running() and member.status() not in {
                    psutil.STATUS_ZOMBIE,
                    psutil.STATUS_DEAD,
                }:
                    running.append(member)
            except psutil.NoSuchProcess:
                pass
        if not running:
            return
        if monotonic() >= deadline:
            raise TaskConflict("worker_descendants_not_stopped")
        members = running
        sleep(0.01)


async def _stop(process: asyncio.subprocess.Process, job: "WindowsJob | None") -> None:
    if process.stdin is not None:
        process.stdin.close()
    # Closing both directions also releases a worker blocked on untrusted output.
    transport = cast(asyncio.SubprocessTransport, vars(process)["_transport"])
    for descriptor in (0, 1, 2):
        pipe = transport.get_pipe_transport(descriptor)
        if pipe is not None:
            pipe.close()
    group_stopped = False
    try:
        try:
            async with asyncio.timeout(4):
                await process.wait()
        except TimeoutError:
            if job is not None:
                await asyncio.to_thread(job.terminate_and_wait, 3.0)
            elif os.name != "nt":
                group_stopped = True
                await asyncio.to_thread(_stop_group, process.pid)
            elif process.returncode is None:
                process.kill()
            async with asyncio.timeout(3):
                await process.wait()
    finally:
        if os.name != "nt" and not group_stopped:
            # The qualified launcher owns a bwrap namespace; reap any group children too.
            await asyncio.to_thread(_stop_group, process.pid)
        if job is not None:
            try:
                await asyncio.to_thread(job.terminate_and_wait, 3.0)
            finally:
                job.close()


@asynccontextmanager
async def owned_stdio(
    command: tuple[str, ...],
    *,
    cwd: Path | None = None,
    overrides: Mapping[str, str] | None = None,
) -> AsyncGenerator[asyncio.subprocess.Process]:
    """Run an installed launcher without a shell or inherited credentials; always reap it."""
    environment = {
        key: value
        for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA")
        if (value := os.environ.get(key)) is not None
    }
    environment.update({"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
    if overrides is not None:
        environment.update(overrides)
    if not Path(command[0]).is_absolute():
        raise TaskConflict("worker_absolute_executable_required")
    creation = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            cwd=cwd,
            limit=WIRE_BYTES,
            start_new_session=os.name != "nt",
            creationflags=(0x00000004 | subprocess.CREATE_NEW_PROCESS_GROUP)
            if os.name == "nt"
            else 0,
        )
    )
    process: asyncio.subprocess.Process | None = None
    job = None
    try:
        try:
            process = await asyncio.shield(creation)
        except asyncio.CancelledError:
            process = await settled(creation)
            raise
        assert process is not None
        if os.name == "nt":
            from .windows_job import WindowsJob

            job = WindowsJob(_handle(process))
            job.resume_suspended(_handle(process))
        yield process
    finally:
        if process is not None:
            await settled(asyncio.create_task(_stop(process, job)))
        owner = asyncio.current_task()
        if owner is not None and owner.cancelling():
            raise asyncio.CancelledError
