"""Optional bounded related reads and compact prompt dependencies; no backend dependencies."""

from typing import Annotated, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from .common import CanonicalId, ContractModel, NonnegativeInteger, Revision, UtcTimestamp
from .evidence import EvidenceAssertion, SourceRef
from .memory import Memory, OriginMapping, PinnedMemory, ProcessingProvenance

MAX_CONTEXT_REFERENCES = 256
MAX_CONTEXT_METADATA_BYTES = 65536


class MemoryReference(ContractModel):
    """An exact revision whose current lifecycle and authorization must remain eligible."""

    memory_id: CanonicalId
    revision: Revision


class ContextDependencies(ContractModel):
    """Small fast-path certificate; policy changes invalidate rather than widen access."""

    memories: Annotated[tuple[MemoryReference, ...], Field(max_length=256)] = ()
    sources: Annotated[tuple[SourceRef, ...], Field(max_length=256)] = ()
    policy_epoch: NonnegativeInteger
    selection_epoch: NonnegativeInteger | None = None
    valid_until: UtcTimestamp | None = None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        """Reject oversized unions, including transitive retained-history metadata."""
        if len(self.memories) + len(self.sources) > MAX_CONTEXT_REFERENCES:
            raise ValueError("Context dependency reference budget exceeded.")
        if len(self.model_dump_json().encode()) > MAX_CONTEXT_METADATA_BYTES:
            raise ValueError("Context dependency metadata budget exceeded.")
        return self


class ProfileOmission(ContractModel):
    """Content-free field omission diagnosis; source text never belongs in logs."""

    field: str
    reason: str


class ProfileSelection(ContractModel):
    """Deterministic allowlisted selection, separate from explicit manual pin ownership."""

    policy: Literal["ember.profile@0.1.0"] = "ember.profile@0.1.0"
    enabled: bool
    subject_id: CanonicalId | None = None
    selection_epoch: NonnegativeInteger
    automatic: Annotated[tuple[PinnedMemory, ...], Field(max_length=8)] = ()
    omitted: tuple[ProfileOmission, ...] = ()
    valid_until: UtcTimestamp | None = None


class RelatedRequest(ContractModel):
    """Resolve redirects, inspect explicit edges, or validate a compact context certificate."""

    intent: Literal["resolve", "expand", "validate"]
    anchors: Annotated[tuple[MemoryReference, ...], Field(max_length=32)] = ()
    dependencies: ContextDependencies | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 32
    row_budget: Annotated[int, Field(ge=1, le=512)] = 256
    call_budget: Annotated[int, Field(ge=1, le=32)] = 8
    deadline_at: UtcTimestamp | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if (self.intent == "validate") != (self.dependencies is not None):
            raise ValueError("Only validate requests carry compact context dependencies.")
        if self.intent == "validate" and self.anchors:
            raise ValueError("Validation uses the dependency union, not relationship anchors.")
        return self


class MemoryRelationship(ContractModel):
    """Engine-validated edge; shared dates, topics or users never imply an incident."""

    relationship_id: CanonicalId
    kind: Literal["supersedes", "redirects", "conflicts", "supports"]
    source: MemoryReference
    target: MemoryReference | None = None
    evidence: tuple[EvidenceAssertion, ...] = ()
    origins: tuple[OriginMapping, ...] = ()
    provenance: tuple[ProcessingProvenance, ...] = ()
    candidate_id: CanonicalId | None = None


class ReadManifest(ContractModel):
    """Bounded logical snapshot metadata; never a path, credential or access grant."""

    character_id: CanonicalId
    policy_epoch: NonnegativeInteger
    selection_epoch: NonnegativeInteger
    recorded_clock_us: NonnegativeInteger


class RelatedResponse(ContractModel):
    """Validation performs no hydration; relationship reads disclose partial coverage."""

    status: Literal["complete", "partial", "invalid", "unavailable"]
    valid: bool | None = None
    reasons: tuple[str, ...] = ()
    memories: Annotated[tuple[Memory, ...], Field(max_length=100)] = ()
    relationships: Annotated[tuple[MemoryRelationship, ...], Field(max_length=100)] = ()
    manifest: ReadManifest | None = None
    rows_examined: NonnegativeInteger = 0
    source_calls: NonnegativeInteger = 0


@runtime_checkable
class RelatedLookupClient(Protocol):
    """Optional synchronous capability, independent of the baseline MemoryStore protocol."""

    def lookup_related(self, request: RelatedRequest) -> RelatedResponse:
        """Read bounded typed relationships or validate without similarity search."""
        ...


@runtime_checkable
class AsyncRelatedLookupClient(Protocol):
    """Optional async capability implemented through existing bounded engine workers."""

    async def lookup_related(self, request: RelatedRequest) -> RelatedResponse:
        """Resolve/expand explicitly, or validate a compact certificate without hydration."""
        ...
