"""OS-held store writer ownership; lockfile presence is never treated as ownership."""

import os
from pathlib import Path
from typing import BinaryIO

from .errors import StorageError, StoreOwnershipError


class StoreLock:
    """Hold one canonical writer owner until explicit close or process exit/crash."""

    def __init__(self, path: Path) -> None:
        self._file: BinaryIO | None = None
        try:
            stream = path.open("a+b")
            self._file = stream
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\x00")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if self._file is not None:
                self._file.close()
                self._file = None
            raise StoreOwnershipError() from error

    def close(self) -> None:
        """Release only this owner's descriptor; stale lockfiles do not block restart."""
        if self._file is not None:
            self._file.close()
            self._file = None


def validate_local_root(root: Path) -> Path:
    """Reject links/reparse points and remote Windows mounts for live canonical SQLite."""
    import stat

    candidate = root.absolute()
    for part in (candidate, *candidate.parents):
        if part.exists():
            info = part.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise StorageError()
    if os.name == "nt":
        import ctypes

        # GetDriveTypeW is a native untyped boundary; only its documented integer result is used.
        drive_type: object = ctypes.windll.kernel32.GetDriveTypeW(str(candidate.anchor))
        if not isinstance(drive_type, int) or drive_type in {0, 1, 4}:
            raise StorageError()
    return candidate.resolve()
