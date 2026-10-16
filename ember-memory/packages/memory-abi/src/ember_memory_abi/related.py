"""Optional bounded related reads and compact prompt dependencies; no backend dependencies."""

from typing import Annotated, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from .common import (
    CanonicalId,
    ContractModel,
    NonnegativeInteger,
    Revision,
    UtcTimestamp,
    WireString,
)
from .evidence import Evidence, EvidenceAssertion, SourceRef
from .memory import (
    EntityRef,
    Memory,
    MemorySource,
    OriginMapping,
    PinnedMemory,
    ProcessingProvenance,
)
from .temporal import SourceTimestamp, TemporalQuery

MAX_CONTEXT_REFERENCES = 256
MAX_CONTEXT_METADATA_BYTES = 65536


class MemoryReference(ContractModel):
    """An exact revision whose current lifecycle and authorization must remain eligible."""

    memory_id: CanonicalId
    revision: Revision


class TemporalSelection(ContractModel):
    """Exact references selected under a fixed historical selector, not new authority."""

    temporal: TemporalQuery
    memories: Annotated[tuple[MemoryReference, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def historical(self) -> Self:
        """An empty temporal selector must use ordinary current dependencies."""
        if not any(self.temporal.model_dump(exclude={"extensions"}).values()):
            raise ValueError("Historical selection requires an explicit temporal scope.")
        return self


class ContextDependencies(ContractModel):
    """Small fast-path certificate; policy changes invalidate rather than widen access."""

    memories: Annotated[tuple[MemoryReference, ...], Field(max_length=256)] = ()
    sources: Annotated[tuple[SourceRef, ...], Field(max_length=256)] = ()
    selections: Annotated[
        tuple[TemporalSelection, ...], Field(max_length=32, exclude_if=lambda value: not value)
    ] = ()
    excerpt_sources: Annotated[
        tuple[SourceRef, ...], Field(max_length=256, exclude_if=lambda value: not value)
    ] = ()
    archive_sources: Annotated[
        tuple[SourceRef, ...], Field(max_length=256, exclude_if=lambda value: not value)
    ] = ()
    policy_epoch: NonnegativeInteger
    selection_epoch: NonnegativeInteger | None = None
    valid_until: UtcTimestamp | None = None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        """Reject oversized unions, including transitive retained-history metadata."""
        selected = [
            (ref.memory_id, ref.revision) for group in self.selections for ref in group.memories
        ]
        references = {(ref.memory_id, ref.revision) for ref in self.memories}
        if len(selected) != len(set(selected)) or not set(selected) <= references:
            raise ValueError("Temporal groups must uniquely select existing dependencies.")
        if not {ref.identity_key() for ref in self.excerpt_sources} <= {
            ref.identity_key() for ref in self.sources
        }:
            raise ValueError("Raw evidence excerpts require their evidence-read dependencies.")
        source_refs = {
            ref.identity_key()
            for ref in (*self.sources, *self.archive_sources, *self.excerpt_sources)
        }
        if len(self.memories) + len(source_refs) > MAX_CONTEXT_REFERENCES:
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
    sources: Annotated[
        tuple[SourceRef, ...], Field(max_length=32, exclude_if=lambda value: not value)
    ] = ()
    excerpts: Annotated[
        tuple[Evidence, ...], Field(max_length=24, exclude_if=lambda value: not value)
    ] = ()
    source_capability: Literal["evidence_read", "archive_search"] = Field(
        default="evidence_read", exclude_if=lambda value: value == "evidence_read"
    )
    entity_names: Annotated[
        tuple[WireString, ...], Field(max_length=8, exclude_if=lambda value: not value)
    ] = ()
    temporal: TemporalQuery = Field(
        default_factory=TemporalQuery,
        exclude_if=lambda value: not any(value.model_dump(exclude={"extensions"}).values()),
    )
    recall_only: bool = Field(default=False, exclude_if=lambda value: not value)
    include_relationships: bool = Field(default=True, exclude_if=lambda value: value)
    dependencies: ContextDependencies | None = None
    refresh_dependencies: bool = Field(
        default=False,
        exclude_if=lambda value: not value,
        description=(
            "Negotiated selected_context_dependencies_v1 validation returns a fresh bounded "
            "canonical evidence closure for the selected memories and explicit raw excerpts. "
            "Caller time bounds and historical/current policy checks remain mandatory."
        ),
    )
    limit: Annotated[int, Field(ge=1, le=100)] = 32
    row_budget: Annotated[int, Field(ge=1, le=512)] = 256
    call_budget: Annotated[int, Field(ge=1, le=32)] = 8
    deadline_at: UtcTimestamp | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if (self.intent == "validate") != (self.dependencies is not None):
            raise ValueError("Only validate requests carry compact context dependencies.")
        if self.refresh_dependencies and self.intent != "validate":
            raise ValueError("Only validation can certify selected prompt dependencies.")
        if self.intent == "validate" and (
            self.anchors or self.sources or self.entity_names or self.excerpts
        ):
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


class ResolvedEntity(ContractModel):
    """An explicitly matched non-generic entity alias from eligible accepted memories."""

    reference: EntityRef
    alias: WireString


class SourceObservation(ContractModel):
    """Exact original excerpt plus independent observation clock and branch labels."""

    source: MemorySource
    role: WireString = "unknown"
    conversation_id: CanonicalId | None = None
    observed: SourceTimestamp | None = None
    recorded_at: UtcTimestamp | None = None
    branch_status: WireString = "not_evaluated"


class RelatedResponse(ContractModel):
    """Validation performs no hydration; relationship reads disclose partial coverage."""

    status: Literal["complete", "partial", "invalid", "unavailable"]
    valid: bool | None = None
    reasons: tuple[str, ...] = ()
    memories: Annotated[tuple[Memory, ...], Field(max_length=100)] = ()
    relationships: Annotated[tuple[MemoryRelationship, ...], Field(max_length=100)] = ()
    excerpts: Annotated[tuple[SourceObservation, ...], Field(max_length=24)] = ()
    entities: Annotated[tuple[ResolvedEntity, ...], Field(max_length=8)] = ()
    dependencies: ContextDependencies | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
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
