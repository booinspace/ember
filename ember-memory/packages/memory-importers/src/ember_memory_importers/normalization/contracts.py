"""Importer-private maintenance boundary; generic source contracts stay in the ABI."""

from collections.abc import Mapping
from typing import Protocol

from pydantic import Field

from ember_memory_abi.archive import (
    ArchiveWriteReceipt,
    ImportCursor,
    NormalizedArchiveBatch,
    SourceRecord,
)
from ember_memory_abi.common import CanonicalId, ContractModel


class SourceImportError(ValueError):
    """Source cannot be normalized safely; diagnostics never expose source values."""


class RecordKey(ContractModel):
    """Private provider key; never a canonical ID or a runtime tool argument."""

    adapter: str
    record_kind: str
    external_id: str = Field(repr=False)


class RecordIdentity(ContractModel):
    """Proposed/existing canonical mapping, committed only with its source batch."""

    key: RecordKey
    record_id: CanonicalId


class ImportTarget(Protocol):
    """Bound source store; composition adapts engine maintenance without importing it here."""

    source_store_id: CanonicalId
    namespace: str

    def identify(self, keys: tuple[RecordKey, ...]) -> tuple[RecordIdentity, ...]:
        """Prepare bounded stable mappings without partially committing a source import."""
        ...

    def latest(self, kind: str, identity: CanonicalId) -> SourceRecord | None:
        """Read the last immutable canonical revision for change detection."""
        ...

    def latest_many(
        self, identities: tuple[tuple[str, CanonicalId], ...]
    ) -> Mapping[tuple[str, CanonicalId], SourceRecord]:
        """Read a bounded set of current source revisions in one maintenance round trip.

        Missing identities are omitted. Importers use this to preserve source-part IDs
        and calculate coherent replacement revisions without one SQLite task per node.
        """
        ...

    def cursor(self, cursor: ImportCursor) -> ImportCursor | None:
        """Read committed import progress; a lost plan cannot safely be regenerated mid-import."""
        ...

    def commit(
        self, batch: NormalizedArchiveBatch, identities: tuple[RecordIdentity, ...]
    ) -> ArchiveWriteReceipt:
        """Atomically write mappings, source revisions, cursor and receipt."""
        ...


class ImportReport(ContractModel):
    """Content-free coverage; counts are not account completeness or memory activation."""

    adapter: str
    source_store_id: CanonicalId
    source_sha256: str
    conversations: int
    messages: int
    available_texts: int
    excluded_texts: int
    failures: int = 0
    records_written: int
    coverage: tuple[str, ...]
