"""Trusted engine/composition and maintenance contracts, outside the public ABI."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from pydantic import Field

from ember_memory_abi.common import CanonicalId, ContractModel, Revision, UtcTimestamp, WireString
from ember_memory_abi.evidence import SourceRef


class Clock(Protocol):
    """Recorded-time/deadline clock; historical source times are never synthesized."""

    def now(self) -> datetime:
        """Return an aware UTC observation for recorded time and deadlines."""
        ...


class SystemClock:
    """Read aware UTC time without provider/runtime dependencies."""

    def now(self) -> datetime:
        """Return the current aware UTC instant."""
        return datetime.now(UTC)


class TokenCounter(Protocol):
    """Composition-supplied context cost; a model tokenizer can replace the default later."""

    def count(self, text: str) -> int:
        """Return a nonnegative context cost without I/O or model calls."""
        ...


class Utf8ByteBudget:
    """Conservative provisional byte budgeting, explicitly not a measured model token count."""

    def count(self, text: str) -> int:
        """Count UTF-8 bytes without normalizing source text."""
        return len(text.encode("utf-8"))


class StoreInfo(ContractModel):
    """Persistent store identity, without physical database paths."""

    store_id: CanonicalId
    kind: Literal["library", "character"]
    namespace: WireString
    availability: Literal["available", "unavailable"] = "available"


class SourceKey(ContractModel):
    """Provider key private to maintenance; accounts remain separated by the bound store."""

    adapter: WireString
    record_kind: WireString
    external_id: Annotated[str, Field(min_length=1)] = Field(repr=False)


class SourceKeyAssignment(ContractModel):
    """A proposed crosswalk committed atomically with records/cursor, never a public identity."""

    key: SourceKey
    record_id: CanonicalId


class MutationAuthority(ContractModel):
    """Trusted composition binding, never derived from model arguments/verdict strings."""

    mode: Literal["read_only", "confirmed_user", "opencode_notes"] = "read_only"


class SourceGrant(ContractModel):
    """Exact snapshot grants; evidence reads and archive searching are separate capabilities."""

    references: Annotated[tuple[SourceRef, ...], Field(min_length=1, max_length=1000)]
    capabilities: frozenset[Literal["evidence_read", "archive_search"]]
    expires_at: UtcTimestamp | None = None


class ForgetImpact(ContractModel):
    """Conservative suppression preview for trusted maintenance/operator review."""

    memory_id: CanonicalId
    expected_revision: Revision
    affected_memory_ids: tuple[CanonicalId, ...]
    source_parts: tuple[SourceRef, ...]
    claim_fingerprints: tuple[str, ...]
