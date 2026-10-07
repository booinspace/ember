"""Safe deterministic directory enumeration, without following links or reparse points."""

import os
import stat
from pathlib import Path


class DirectorySourceError(ValueError):
    """Unsafe or changing directory source; diagnostics omit filesystem paths."""


def _ordinary(path: Path) -> os.stat_result:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise DirectorySourceError("Directory sources cannot contain links or reparse points.")
    return metadata


def directory_files(root: Path, maximum: int) -> tuple[list[tuple[Path, str]], int]:
    """List bounded regular files and directories; reject ambiguous portable names.

    The caller must check file identities before/after reads and rescan the final
    inventory. Files are sorted by normalized relative path, never by OS ordering.
    """
    if not stat.S_ISDIR(_ordinary(root).st_mode):
        raise DirectorySourceError("Input must be an ordinary directory.")
    pending = [root]
    files: list[tuple[Path, str]] = []
    seen: set[str] = set()
    members = 0
    while pending:
        directory = pending.pop()
        _ordinary(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                members += 1
                if members > maximum:
                    raise DirectorySourceError("Directory exceeds the member-count limit.")
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                identity = relative.casefold()
                if (
                    identity in seen
                    or any(part in {"", ".", ".."} or ":" in part for part in relative.split("/"))
                    or "\\" in relative
                ):
                    raise DirectorySourceError("Directory contains ambiguous portable paths.")
                seen.add(identity)
                metadata = _ordinary(path)
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(metadata.st_mode):
                    files.append((path, relative))
                else:
                    raise DirectorySourceError("Directory contains unsupported special files.")
    return sorted(files, key=lambda item: item[1]), members


def file_signature(path: Path) -> tuple[int, int, int, int]:
    """Return stable identity/size/mtime fields, rejecting nonregular files and links."""
    metadata = _ordinary(path)
    if not stat.S_ISREG(metadata.st_mode):
        raise DirectorySourceError("Directory source file is no longer regular.")
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns
