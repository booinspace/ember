"""Operator-selected task artifacts with managed paths and immutable content."""

import asyncio
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID

from .config import RuntimeModel
from .task_store import TaskConflict, TaskSnapshot


class TaskArtifact(RuntimeModel):
    """Content identity for a runtime-owned task artifact."""

    task_id: UUID
    media_type: str = "text/plain"
    size_bytes: int
    sha256: str


class TaskArtifacts:
    """Materialize only an immutable, already validated task summary."""

    def __init__(self, runtime_root: Path) -> None:
        self.root = runtime_root / "artifacts"

    @staticmethod
    def _content(snapshot: TaskSnapshot) -> bytes:
        if (
            snapshot.state != "succeeded"
            or snapshot.result is None
            or snapshot.operation == "recall"
        ):
            raise TaskConflict("task_artifact_unavailable")
        data = snapshot.result.encode("utf-8")
        if len(data) > 65536:
            raise TaskConflict("task_artifact_too_large")
        return data

    async def save(self, snapshot: TaskSnapshot) -> TaskArtifact:
        """Export a plain-text artifact using a runtime-derived file name."""
        data = self._content(snapshot)
        await asyncio.to_thread(self._save, snapshot.task_id, data)
        return TaskArtifact(
            task_id=snapshot.task_id,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

    def _save(self, task_id: UUID, data: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / (str(task_id) + ".txt")
        with NamedTemporaryFile(dir=self.root, delete=False) as temporary:
            path = Path(temporary.name)
            try:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            except BaseException:
                temporary.close()
                path.unlink(missing_ok=True)
                raise
        try:
            path.replace(target)
        finally:
            path.unlink(missing_ok=True)

    async def read(self, snapshot: TaskSnapshot) -> bytes:
        """Reject missing or modified content rather than serving it."""
        expected = self._content(snapshot)
        return await asyncio.to_thread(self._read, snapshot.task_id, expected)

    def _read(self, task_id: UUID, expected: bytes) -> bytes:
        path = self.root / (str(task_id) + ".txt")
        if path.is_symlink():
            raise TaskConflict("task_artifact_invalid")
        try:
            with path.open("rb") as stream:
                data = stream.read(len(expected) + 1)
        except OSError as error:
            raise TaskConflict("task_artifact_unavailable") from error
        if data != expected:
            raise TaskConflict("task_artifact_invalid")
        return data
