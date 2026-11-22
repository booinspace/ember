"""Bounded export from a Linux build's private /work tmpfs to runtime-owned ZIPs."""

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO, TypedDict, cast
from uuid import UUID, uuid4
from zipfile import ZipFile, ZipInfo

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 256
MAX_PATH_BYTES = 240
MAX_DEPTH = 16
MAX_COLLECTION_BYTES = 4 * ((MAX_FILE_BYTES + 2) // 3) + MAX_ENTRIES * (MAX_PATH_BYTES + 16) + 2
MAX_ZIP_BYTES = 22 + MAX_FILE_BYTES + MAX_ENTRIES * (76 + 2 * MAX_PATH_BYTES)
_WINDOWS = sys.platform == "win32"
if sys.platform != "win32":
    _DIR_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
    _FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
else:
    _DIR_FLAGS = _FILE_FLAGS = 0  # Never used by the Windows storage path.

# Deliberately portable names, without Unicode/Windows aliases or JSON escape characters.
_COMPONENT = r"[A-Za-z0-9_@.+ -]+"
_RESERVED = r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]) *(?:\..*)?"

COLLECT_SCRIPT = (
    f"""
import base64
import json
import os
import re
import stat

MAX_FILE_BYTES = {MAX_FILE_BYTES}
MAX_ENTRIES = {MAX_ENTRIES}
MAX_PATH_BYTES = {MAX_PATH_BYTES}
MAX_DEPTH = {MAX_DEPTH}
COMPONENT = re.compile({_COMPONENT!r})
RESERVED = re.compile({_RESERVED!r}, re.IGNORECASE)
"""
    + """
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
READ_FLAGS = os.O_RDONLY | os.O_NONBLOCK


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_ctime_ns, info.st_mtime_ns)


def collect(root):
    records = []
    entries = total = 0
    device = os.fstat(root).st_dev

    def walk(directory, prefix, depth):
        nonlocal entries, total
        before = signature(os.fstat(directory))
        with os.scandir(directory) as scan:
            for entry in scan:
                entries += 1
                name = entry.name
                path = prefix + name
                if (entries > MAX_ENTRIES or depth > MAX_DEPTH or len(path) > MAX_PATH_BYTES
                    or not COMPONENT.fullmatch(name) or name.endswith((".", " "))
                    or RESERVED.fullmatch(name)):
                    raise ValueError
                # Pin the exact inode without opening a link, FIFO, socket, or device for I/O.
                inode = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    info = os.fstat(inode)
                    if info.st_dev != device:
                        raise ValueError
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(".", DIR_FLAGS, dir_fd=inode)
                        try:
                            walk(child, path + "/", depth + 1)
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        if info.st_size > MAX_FILE_BYTES - total:
                            raise ValueError
                        # Reopen the pinned regular inode, never its mutable workspace name.
                        fd = os.open(f"/proc/self/fd/{inode}", READ_FLAGS)
                        with os.fdopen(fd, "rb") as stream:
                            if signature(info) != signature(os.fstat(fd)):
                                raise ValueError
                            content = stream.read(info.st_size + 1)
                            if len(content) != info.st_size:
                                raise ValueError
                        total += len(content)
                        records.append([path, base64.b64encode(content).decode("ascii")])
                    else:
                        raise ValueError
                    if signature(info) != signature(os.fstat(inode)):
                        raise ValueError
                finally:
                    os.close(inode)
        if before != signature(os.fstat(directory)):
            raise ValueError

    walk(root, "", 1)
    return records


try:
    root = os.open("/work", DIR_FLAGS)
    try:
        records = collect(root)
    finally:
        os.close(root)
except (OSError, ValueError):
    raise SystemExit("build_artifact_collection_failed") from None
# Output is bounded by total base64 bytes, fixed record overhead, and ASCII path limits.
print(json.dumps(records, separators=(",", ":")), end="")
"""
)


class BuildArtifactMetadata(TypedDict):
    """Identity and file inventory of a host-generated ZIP."""

    sha256: str
    size_bytes: int
    names: list[str]


def _validate_name(name: str, nodes: dict[str, tuple[str, bool]]) -> None:
    parts = name.split("/")
    if len(name) > MAX_PATH_BYTES or len(parts) > MAX_DEPTH:
        raise ValueError("invalid_build_artifact_path")
    for index, component in enumerate(parts, start=1):
        if (
            not re.fullmatch(_COMPONENT, component)
            or component.endswith((".", " "))
            or re.fullmatch(_RESERVED, component, re.IGNORECASE)
        ):
            raise ValueError("invalid_build_artifact_path")
        prefix = "/".join(parts[:index])
        key = prefix.lower()
        directory = index < len(parts)
        if key in nodes and (not directory or nodes[key] != (prefix, directory)):
            raise ValueError("conflicting_build_artifact_paths")
        nodes[key] = (prefix, directory)
        if len(nodes) > MAX_ENTRIES:
            raise ValueError("too_many_build_artifact_entries")


def _decode(raw: bytes) -> dict[str, bytes]:
    # Valid paths/base64 cannot contain these delimiters. Bound parser allocations too.
    if (
        len(raw) > MAX_COLLECTION_BYTES
        or raw.count(b"[") > MAX_ENTRIES + 1
        or raw.count(b",") > 2 * MAX_ENTRIES
        or b"{" in raw
    ):
        raise ValueError("invalid_build_artifact_collection")
    records: object = json.loads(raw.decode("ascii"))
    if not isinstance(records, list):
        raise ValueError("invalid_build_artifact_collection")
    entries = cast(list[object], records)
    if len(entries) > MAX_ENTRIES:
        raise ValueError("invalid_build_artifact_collection")
    files: dict[str, bytes] = {}
    nodes: dict[str, tuple[str, bool]] = {}
    remaining = MAX_FILE_BYTES
    for record in entries:
        if not isinstance(record, list):
            raise ValueError("invalid_build_artifact_record")
        fields = cast(list[object], record)
        if len(fields) != 2:
            raise ValueError("invalid_build_artifact_record")
        name, encoded = fields
        if not isinstance(name, str) or not isinstance(encoded, str):
            raise ValueError("invalid_build_artifact_record")
        _validate_name(name, nodes)
        if len(encoded) > 4 * ((remaining + 2) // 3):
            raise ValueError("build_artifact_too_large")
        data = base64.b64decode(encoded, validate=True)
        remaining -= len(data)
        if remaining < 0:
            raise ValueError("build_artifact_too_large")
        files[name] = data
    return files


async def _settle[Result](operation: Callable[[], Result]) -> Result:
    # Cancelling an await cannot stop a filesystem worker. Wait before allowing cleanup.
    job = asyncio.create_task(asyncio.to_thread(operation))
    cancelled: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(job)
            break
        except asyncio.CancelledError as error:
            if job.cancelled():
                raise
            cancelled = error
        except Exception as error:
            if cancelled is not None:
                raise cancelled from error
            raise
    if cancelled is not None:
        raise cancelled
    return result


def _target(task_id: UUID | str) -> str:
    return f"{UUID(str(task_id))}.zip"


def _without_reparse(path: Path, mode_check: Callable[[int], bool]) -> os.stat_result:
    info = path.lstat()
    if not mode_check(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise OSError("invalid_build_artifact_storage_path")
    return info


def _read_exact(stream: BinaryIO, size_bytes: int) -> bytes:
    info = os.fstat(stream.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_size != size_bytes:
        raise ValueError("invalid_build_artifact_content")
    return stream.read(size_bytes + 1)


class BuildArtifacts:
    """Store build output outside the container; callers own task authorization."""

    def __init__(self, runtime_root: Path) -> None:
        self.root = runtime_root / "build-artifacts"

    async def save(self, task_id: UUID | str, raw: bytes) -> BuildArtifactMetadata:
        """Publish a ZIP once. A cancelled call settles before caller cleanup."""
        target = _target(task_id)
        return await _settle(lambda: self._save(target, raw))

    async def read(self, task_id: UUID | str, sha256: str, size_bytes: int) -> bytes:
        """Read only a ZIP matching a trusted committed digest and bounded size."""
        if (
            not re.fullmatch(r"[a-f0-9]{64}", sha256)
            or type(size_bytes) is not int
            or not 22 <= size_bytes <= MAX_ZIP_BYTES
        ):
            raise ValueError("invalid_build_artifact_identity")
        return await asyncio.to_thread(self._read, _target(task_id), sha256, size_bytes)

    async def delete(self, task_id: UUID | str) -> None:
        """Remove an uncommitted ZIP after its save settles. Missing ZIPs are fine."""
        target = _target(task_id)
        await _settle(lambda: self._delete(target))

    def _windows_target(self, target: str) -> Path:
        # ponytail: no dirfds on Windows. Runtime root and its ancestors are operator-owned,
        # not exposed to the container. These checks do not defend against a hostile host user.
        _without_reparse(self.root.parent, stat.S_ISDIR)
        _without_reparse(self.root, stat.S_ISDIR)
        return self.root / target

    def _delete(self, target: str) -> None:
        if _WINDOWS:
            try:
                path = self._windows_target(target)
                if _without_reparse(path, stat.S_ISREG).st_nlink != 1:
                    raise OSError("invalid_build_artifact_storage_path")
                path.chmod(stat.S_IREAD | stat.S_IWRITE)
                path.unlink()
            except FileNotFoundError:
                return
            return
        try:
            directory = self._open_directory()
        except FileNotFoundError:
            return
        try:
            try:
                os.unlink(target, dir_fd=directory)
            except FileNotFoundError:
                return
            os.fsync(directory)
        finally:
            os.close(directory)

    def _open_directory(self) -> int:
        return os.open(self.root, _DIR_FLAGS)

    def _save_windows(self, target: str, data: bytes) -> None:
        self.root.parent.mkdir(parents=True, exist_ok=True)
        _without_reparse(self.root.parent, stat.S_ISDIR)
        self.root.mkdir(exist_ok=True)
        path = self._windows_target(target)
        with NamedTemporaryFile(dir=self.root, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
                temporary.chmod(stat.S_IREAD)
                # Windows rename fails if target already exists and does not follow it.
                os.rename(temporary, path)
            except BaseException:
                stream.close()
                temporary.chmod(stat.S_IREAD | stat.S_IWRITE)
                temporary.unlink()
                raise

    def _save(self, target: str, raw: bytes) -> BuildArtifactMetadata:
        files = _decode(raw)
        names = sorted(files)
        buffer = io.BytesIO()
        with ZipFile(buffer, "w", allowZip64=False) as archive:
            for name in names:
                info = ZipInfo(name)
                info.create_system = 3  # Always serialize Unix regular-file permissions.
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, files[name])
        data = buffer.getvalue()
        if _WINDOWS:
            self._save_windows(target, data)
        else:
            self._save_posix(target, data)
        return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), "names": names}

    def _save_posix(self, target: str, data: bytes) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = self._open_directory()
        try:
            temporary = f".{uuid4().hex}.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    if sys.platform != "win32":
                        os.fchmod(fd, 0o400)
                    os.fsync(fd)
                # Atomic, no-clobber publication on the same filesystem.
                os.link(temporary, target, src_dir_fd=directory, dst_dir_fd=directory)
            finally:
                os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)

    def _read(self, target: str, sha256: str, size_bytes: int) -> bytes:
        if _WINDOWS:
            path = self._windows_target(target)
            _without_reparse(path, stat.S_ISREG)
            with path.open("rb") as stream:
                data = _read_exact(stream, size_bytes)
        else:
            directory = self._open_directory()
            try:
                fd = os.open(target, _FILE_FLAGS, dir_fd=directory)
                with os.fdopen(fd, "rb") as stream:
                    data = _read_exact(stream, size_bytes)
            finally:
                os.close(directory)
        if len(data) != size_bytes or hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("invalid_build_artifact_content")
        return data
