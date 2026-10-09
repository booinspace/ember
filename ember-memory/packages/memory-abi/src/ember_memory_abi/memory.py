"""Character beliefs, library snapshots, candidates, and working-context contracts."""

from typing import Annotated, Literal, Self

from pydantic import UUID5, Field, JsonValue, model_validator

from .archive import ProcessingSpecRef
from .common import (
    CanonicalId,
    Confidence,
    ContractModel,
    FiniteScore,
    NonnegativeInteger,
    PositiveInteger,
    Revision,
    UtcTimestamp,
    WireString,
)
from .evidence import Evidence, EvidenceAssertion, SourceRef, source_text_sha256
from .temporal import TemporalBounds, TemporalMetadata


class EntityRef(ContractModel):
    """Store-owned entity identity; similarity never merges entities across stores."""

    store_id: CanonicalId
    entity_id: CanonicalId


class Entity(ContractModel):
    """Revisioned entity context with explicit origin mapping for independent seeds."""

    reference: EntityRef
    revision: Revision
    name: str = Field(min_length=1, repr=False)
    aliases: tuple[str, ...] = ()
    origin: EntityRef | None = None


class StringValue(ContractModel):
    """An exact string fact value, normalized only through a referenced predicate policy."""

    type: Literal["string"] = "string"
    value: str = Field(repr=False)


class NumberValue(ContractModel):
    """A finite scalar fact value; units remain explicit."""

    type: Literal["number"] = "number"
    value: FiniteScore
    unit: WireString | None = None


class BooleanValue(ContractModel):
    """A strict boolean fact value, distinct from numeric zero/one."""

    type: Literal["boolean"] = "boolean"
    value: bool


class EntityValue(ContractModel):
    """A relation to a specific store-owned entity."""

    type: Literal["entity"] = "entity"
    value: EntityRef


type FactValue = Annotated[
    StringValue | NumberValue | BooleanValue | EntityValue, Field(discriminator="type")
]


class FactPayload(ContractModel):
    """Subject/predicate/value with polarity; validity lives on the interpretation."""

    type: Literal["fact"] = "fact"
    subject: EntityRef
    predicate: WireString
    definition_version: WireString | None = None
    value: FactValue
    polarity: WireString = "positive"
    original_predicate: WireString | None = None


class EpisodePayload(ContractModel):
    """One attributed event; similar text alone never merges episodes."""

    type: Literal["episode"] = "episode"
    event: str = Field(min_length=1, repr=False)
    participants: tuple[EntityRef, ...] = ()
    place: EntityRef | None = None


class GoalPayload(ContractModel):
    """An intent/state, without converting aspiration into an observed fact."""

    type: Literal["goal"] = "goal"
    description: str = Field(min_length=1, repr=False)
    state: WireString = "proposed"


class PlanPayload(ContractModel):
    """An uncertain proposed procedure, not evidence of completed action."""

    type: Literal["plan"] = "plan"
    description: str = Field(min_length=1, repr=False)
    steps: tuple[Annotated[str, Field(min_length=1)], ...] = ()
    state: WireString = "proposed"
    goal_id: CanonicalId | None = None


class OpaquePayload(ContractModel):
    """Read-preserved unfamiliar payload; it never acquires supported write semantics."""

    type: Literal["opaque"] = "opaque"
    original_kind: WireString
    data: dict[str, JsonValue] = Field(repr=False)


type MemoryPayload = Annotated[
    FactPayload | EpisodePayload | GoalPayload | PlanPayload | OpaquePayload,
    Field(discriminator="type"),
]


class ProcessingProvenance(ContractModel):
    """Interpretation run/specification identity, separate from original source evidence."""

    run_id: CanonicalId
    specification: ProcessingSpecRef
    processed_at: UtcTimestamp
    input_sources: tuple[SourceRef, ...] = ()


class MemoryContent(ContractModel):
    """Proposed or accepted semantic content; validation is not automatic activation."""

    kind: WireString
    payload: MemoryPayload
    modality: WireString
    confidence: Confidence
    importance: Confidence
    evidence: tuple[EvidenceAssertion, ...] = ()
    provenance: tuple[ProcessingProvenance, ...] = ()

    @model_validator(mode="after")
    def _matching_kind(self) -> Self:
        expected = (
            self.payload.original_kind
            if isinstance(self.payload, OpaquePayload)
            else self.payload.type
        )
        if self.kind != expected:
            raise ValueError("Memory kind must match its payload's declared kind.")
        return self


class MemoryDraft(ContractModel):
    """Mutation/candidate input, without character ownership or a forged recorded clock."""

    content: MemoryContent
    temporal: TemporalBounds = Field(default_factory=TemporalBounds)


class OriginMapping(ContractModel):
    """Independent seed identity; source evidence can remain shared without message duplication."""

    origin_memory_id: CanonicalId
    origin_memory_revision: Revision
    origin_source_store_id: CanonicalId
    seed_operation_id: CanonicalId
    seeded_at: UtcTimestamp


class Memory(ContractModel):
    """An immutable revision of a character-owned belief, never a shared mutable library row."""

    memory_id: CanonicalId
    character_id: CanonicalId
    revision: Revision
    lifecycle: WireString
    content: MemoryContent
    temporal: TemporalMetadata
    created_at: UtcTimestamp
    origin: OriginMapping | None = None


class ValidationVerdict(ContractModel):
    """Persist validation/activation decision inputs; confidence is not a probability."""

    policy: ProcessingSpecRef
    verdict: WireString
    reasons: tuple[WireString, ...]
    evaluated_at: UtcTimestamp
    manual_override: bool = False


class CandidateMemory(ContractModel):
    """Library/store-owned proposed interpretation with versioned verdicts and evidence."""

    candidate_id: CanonicalId
    owner_store_id: CanonicalId
    revision: Revision
    draft: MemoryDraft
    recorded_at: UtcTimestamp
    verdicts: tuple[ValidationVerdict, ...] = ()


class ApprovedKnowledge(ContractModel):
    """Accepted library snapshot eligible for explicit seeding; future imports cannot rewrite it."""

    knowledge_id: CanonicalId
    library_id: CanonicalId
    revision: Revision
    draft: MemoryDraft
    approval: ValidationVerdict
    recorded_at: UtcTimestamp


class MemorySource(ContractModel):
    """A grant-checked original quote or explicit unavailable source, with no machine locator."""

    evidence: Evidence
    availability: Literal["available", "unavailable"]
    quote: str | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _availability(self) -> Self:
        if (self.availability == "available") != (self.quote is not None):
            raise ValueError("Only available sources carry an original quote.")
        if self.quote is not None:
            span = self.evidence.span
            if (
                source_text_sha256(self.quote) != span.quoted_span_sha256
                or len(self.quote) != span.end_codepoint - span.start_codepoint
                or len(self.quote.encode("utf-8")) != span.end_byte - span.start_byte
            ):
                raise ValueError("Resolved quotes must match exact evidence hashes and lengths.")
        return self


class ChunkSpan(ContractModel):
    """Map a nonempty derived-text interval to exact original evidence."""

    start_codepoint: NonnegativeInteger
    end_codepoint: NonnegativeInteger
    evidence: Evidence

    @model_validator(mode="after")
    def _nonempty(self) -> Self:
        if self.end_codepoint <= self.start_codepoint:
            raise ValueError("Chunk mappings must be nonempty half-open intervals.")
        return self


class ArchiveChunk(ContractModel):
    """Disposable derived text, identified by UUID5 over immutable inputs/specification."""

    chunk_id: UUID5
    specification: ProcessingSpecRef
    text: str = Field(repr=False)
    source_spans: tuple[ChunkSpan, ...]

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if any(span.end_codepoint > len(self.text) for span in self.source_spans):
            raise ValueError("Chunk source mappings cannot exceed the derived text.")
        return self


class SessionSummary(ContractModel):
    """Bounded derived working context; it does not replace source history or corroboration."""

    summary_id: CanonicalId
    character_id: CanonicalId
    session_id: CanonicalId
    revision: Revision
    text: str = Field(repr=False)
    token_count: NonnegativeInteger
    token_budget: PositiveInteger
    dependencies: tuple[SourceRef, ...]
    generated_at: UtcTimestamp

    @model_validator(mode="after")
    def _budget(self) -> Self:
        if self.token_count > self.token_budget:
            raise ValueError("A session summary must fit its explicit token budget.")
        return self


class PinnedMemory(ContractModel):
    """A character-owned pin bound to a particular memory revision."""

    character_id: CanonicalId
    memory_id: CanonicalId
    memory_revision: Revision
    priority: NonnegativeInteger
    pinned_at: UtcTimestamp
    purpose: Literal["manual", "profile"] = "manual"
