"""Engine-private optional derived retrieval port; canonical policy is still authoritative."""

import sqlite3
from typing import TYPE_CHECKING, Protocol

from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ArchiveSearchResponse,
    MemoryQuery,
    SearchResponse,
)

if TYPE_CHECKING:
    from ._state import CharacterState


class RetrievalBackend(Protocol):
    """Optional acceleration; None requests the existing canonical degraded path."""

    def available(self) -> bool: ...
    def memory(
        self, state: "CharacterState", canonical: sqlite3.Connection, query: MemoryQuery
    ) -> SearchResponse | None: ...
    def archive(
        self, state: "CharacterState", canonical: sqlite3.Connection, query: ArchiveQuery
    ) -> ArchiveSearchResponse | None: ...
