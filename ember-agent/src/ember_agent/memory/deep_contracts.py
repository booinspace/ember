"""Agent-owned bounded investigations; result associations never mutate canonical beliefs."""

from typing import Annotated, Literal

from pydantic import UUID5, Field

from ember_memory_abi.common import CanonicalId, ContractModel, NonnegativeInteger, UtcTimestamp
from ember_memory_abi.interfaces import MemoryResult
from ember_memory_abi.related import (
    ContextDependencies,
    MemoryReference,
    ReadManifest,
    SourceObservation,
)

from ..character.temporal import CalendarScope


class DeepRecallBudget(ContractModel):
    """Caller may lower these ceilings; request fields cannot raise service authority."""

    anchors: Annotated[int, Field(ge=1, le=6)] = 6
    hops: Annotated[int, Field(ge=0, le=2)] = 2
    inspected: Annotated[int, Field(ge=1, le=256)] = 256
    memories: Annotated[int, Field(ge=1, le=24)] = 24
    archive_chunks: Annotated[int, Field(ge=0, le=12)] = 12
    search_calls: Annotated[int, Field(ge=1, le=12)] = 12
    abi_calls: Annotated[int, Field(ge=1, le=24)] = 24
    excerpts: Annotated[int, Field(ge=0, le=24)] = 24
    deadline_ms: Annotated[int, Field(ge=100, le=5000)] = 5000
    result_bytes: Annotated[int, Field(ge=1024, le=262144)] = 262144


class DeepRecallRequest(ContractModel):
    """Runtime binds character/session/request identity and a conversation reference clock."""

    request_id: CanonicalId
    character_id: CanonicalId
    context_id: CanonicalId
    query: Annotated[str, Field(min_length=1, max_length=8192)] = Field(repr=False)
    reference_at: UtcTimestamp
    timezone: str = "Europe/London"
    previous_scope: CalendarScope | None = None
    budget: DeepRecallBudget = Field(default_factory=DeepRecallBudget)


class RecallAssociation(ContractModel):
    """Strength is an evidence label, distinct from retrieval score and belief confidence."""

    anchor: MemoryReference
    target: MemoryReference
    reason: Literal[
        "explicit_transition",
        "overlapping_evidence",
        "conflicting_claims",
        "source_neighbor",
        "entity_alias",
    ]
    strength: Literal["explicit", "source_supported", "topical_hypothesis"]


class RecallCluster(ContractModel):
    """Only explicit or overlapping-evidence links may unite incident candidates."""

    cluster_id: UUID5
    members: Annotated[tuple[MemoryReference, ...], Field(max_length=24)]
    strength: Literal["explicit", "source_supported", "independent"]


class RecallCounters(ContractModel):
    """Bounded execution counters; no prompt/source text enters diagnostics."""

    inspected: NonnegativeInteger = 0
    searches: NonnegativeInteger = 0
    abi_calls: NonnegativeInteger = 0
    rows_reported: NonnegativeInteger = 0
    hydrated_spans: NonnegativeInteger = 0
    elapsed_ms: NonnegativeInteger = 0
    uncached_embeddings: NonnegativeInteger = 0
    sql_statements: NonnegativeInteger = 0
    materialized_bytes: NonnegativeInteger = 0
    limits_hit: Annotated[tuple[str, ...], Field(max_length=8)] = ()


class DeepRecallResult(ContractModel):
    """Completion means this bounded investigation finished, never exhaustive archive coverage."""

    request_id: CanonicalId
    character_id: CanonicalId
    context_id: CanonicalId
    query: Annotated[str, Field(max_length=8192)] = Field(default="", repr=False)
    outcome: Literal["complete_within_budget", "partial", "unavailable", "cancelled", "deferred"]
    memories: Annotated[tuple[MemoryResult, ...], Field(max_length=24)] = ()
    observations: Annotated[tuple[SourceObservation, ...], Field(max_length=24)] = ()
    associations: Annotated[tuple[RecallAssociation, ...], Field(max_length=256)] = ()
    clusters: Annotated[tuple[RecallCluster, ...], Field(max_length=24)] = ()
    reasons: Annotated[tuple[str, ...], Field(max_length=64)] = ()
    scope: CalendarScope | None = None
    manifest: ReadManifest | None = None
    dependencies: ContextDependencies | None = None
    counters: RecallCounters = Field(default_factory=RecallCounters)
    prompt_memories: Annotated[tuple[MemoryReference, ...], Field(max_length=24)] = ()
    prompt_observations: Annotated[tuple[int, ...], Field(max_length=24)] = ()
