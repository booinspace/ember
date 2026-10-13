"""Owned local child processes and an OS-held launcher lease."""

import json
import os
import signal
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import file_digest
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from .config import ConfigurationError


class RuntimeLease:
    """Hold a profile lock; stale files never authorize adoption or PID-based termination."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.stream: BinaryIO = (root / "runtime.lock").open("a+b")
        try:
            if self.stream.seek(0, os.SEEK_END) == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            raise ConfigurationError("This runtime profile is already owned.") from error

    def close(self) -> None:
        """Release only this launcher's descriptor."""
        self.stream.close()


class OwnedProcess:
    """Retain the original child handle; never discover or kill a process from a saved PID.

    Windows children have a retained kill-on-close job and no console window.
    POSIX children have an independent process group.
    """

    def __init__(
        self,
        command: list[str],
        *,
        root: Path,
        component: str,
        instance: UUID,
        working_directory: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.component = component
        self.path = root / (component + "-process.json")
        executable = Path(command[0]).resolve()
        with executable.open("rb") as stream:
            fingerprint = file_digest(stream, "sha256").hexdigest()
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            cwd=working_directory,
            env={**os.environ, **environment} if environment is not None else None,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        self.job = None
        try:
            if os.name == "nt":
                from .windows_job import WindowsJob

                # Popen keeps the original native Windows process handle until it is reaped.
                native: object = getattr(self.process, "_handle", None)
                if not isinstance(native, int):
                    raise ConfigurationError("Original Windows child handle is unavailable.")
                self.job = WindowsJob(native)
            # Metadata is diagnostic only. The retained Popen handle is shutdown authority.
            self.path.write_text(
                json.dumps(
                    {
                        "instance_id": str(instance),
                        "component": component,
                        "pid": self.process.pid,
                        "started_at": datetime.now(UTC).isoformat(),
                        "executable_sha256": fingerprint,
                        "command": command,
                        "mode": "managed",
                    }
                ),
                encoding="utf-8",
            )
        except BaseException:
            self.stop()
            raise

    def check(self) -> None:
        """Fail early if this child exited before authenticated readiness."""
        code = self.process.poll()
        if code is not None:
            raise ConfigurationError(f"Owned {self.component} exited during startup (code {code}).")

    def wait(self, timeout: float) -> bool:
        """Wait for this exact child; a timeout does not imply that it has stopped."""
        try:
            self.process.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def stop(self) -> None:
        """Bound cleanup to this original process handle, then reap it."""
        if self.process.poll() is None:
            if os.name == "nt":
                self.process.terminate()
            else:
                os.killpg(self.process.pid, signal.SIGTERM)
            if not self.wait(5):
                if os.name == "nt":
                    self.process.kill()
                else:
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        if self.job is not None:
            self.job.close()
        # Keep the content-free record for crash diagnosis; it never confers ownership.
        self.process.wait()
