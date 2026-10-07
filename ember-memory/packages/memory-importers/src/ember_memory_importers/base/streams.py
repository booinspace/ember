"""Narrow file interfaces used across source inspection boundaries."""

from typing import Protocol


class BinaryReader(Protocol):
    """Read bounded bytes from files, ZIP members, or temporary staging streams."""

    def read(self, size: int = -1, /) -> bytes: ...


class BinaryWriter(Protocol):
    """Stage bytes without requiring a particular concrete IO implementation."""

    def write(self, payload: bytes, /) -> int: ...
