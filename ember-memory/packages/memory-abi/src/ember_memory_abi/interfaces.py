"""Storage-independent requests/results and character-bound sync/async client protocols."""

from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import Field

from .archive import ArchiveWriteReceipt, NormalizedArchiveBatch, SourceRecord
from .common import (
    ABI_VERSION,
    ARCHIVE_VERSION,
    CanonicalId,
    ContractModel,
    FiniteScore,
    NonnegativeInteger,
    Revision,
    UtcTimestamp,
    WireString,
)
from .errors import ErrorInfo
from .evidence import EvidenceAssertion, SourceRef
from .memory import (
    ArchiveChunk,
    Memory,
    MemoryDraft,
    MemorySource,
    PinnedMemory,
    SessionSummary,
)
from .related import ContextDependencies, ProfileSelection
from .temporal import TemporalQuery as TemporalQuery

type ResultLimit = Annotated[int, Field(gt=0, le=100)]
type IdempotencyKey = Annotated[str, Field(min_length=1, max_length=256)]


class RequestOptions(ContractModel):
    """Caller deadline metadata; transport cancellation does not resolve uncertain writes."""

    deadline_at: UtcTimestamp | None = None


class MemoryCapabilities(ContractModel):
    """Actual implementation support; schema presence never implies a working backend/index."""

    abi_version: str = ABI_VERSION
    archive_version: str = ARCHIVE_VERSION
    supported_kinds: tuple[WireString, ...] = ()
    supported_modalities: tuple[WireString, ...] = ()
    supported_predicates: tuple[WireString, ...] = ()
    supported_extensions: tuple[WireString, ...] = ()
    retrieval_modes: tuple[WireString, ...] = ()
    mutations: tuple[WireString, ...] = ()
    index_status: Literal["available", "degraded", "unavailable"] = "unavailable"


class MemoryQuery(ContractModel):
    """Bounded belief query scoped by trusted client identity, not a model-supplied character ID."""

    text: str = Field(default="", max_length=8192, repr=False)
    predicates: Annotated[tuple[WireString, ...], Field(max_length=128)] = ()
    entity_ids: Annotated[tuple[CanonicalId, ...], Field(max_length=128)] = ()
    mode: Literal["hybrid", "lexical", "semantic", "structured"] = "hybrid"
    kinds: Annotated[tuple[WireString, ...], Field(max_length=128)] = ()
    modalities: Annotated[tuple[WireString, ...], Field(max_length=128)] = ()
    lifecycles: Annotated[tuple[WireString, ...], Field(max_length=128)] = ("active",)
    temporal: TemporalQuery = Field(default_factory=TemporalQuery)
    limit: ResultLimit = 20
    cursor: str | None = None
    options: RequestOptions = Field(default_factory=RequestOptions)


class ArchiveQuery(ContractModel):
    """Deep archive query; filters can only narrow current grants and suppression."""

    text: str = Field(default="", max_length=8192, repr=False)
    mode: Literal["hybrid", "lexical", "semantic", "literal"] = "hybrid"
    conversation_ids: Annotated[tuple[CanonicalId, ...], Field(max_length=128)] = ()
    source_from: UtcTimestamp | None = None
    source_until: UtcTimestamp | None = None
    source_store_ids: Annotated[tuple[CanonicalId, ...], Field(max_length=128)] = ()
    record_kinds: Annotated[tuple[WireString, ...], Field(max_length=128)] = ()
    roles: Annotated[tuple[WireString, ...], Field(max_length=128)] = ()
    include_alternative_branches: bool = True
    temporal: TemporalQuery = Field(default_factory=TemporalQuery)
    limit: ResultLimit = 20
    cursor: str | None = None
    options: RequestOptions = Field(default_factory=RequestOptions)


class RankingExplanation(ContractModel):
    """Finite ranking inputs and reason codes, without inventing a confidence probability."""

    method: WireString
    score: FiniteScore
    reasons: tuple[WireString, ...] = ()


class MemoryResult(ContractModel):
    """A permitted memory revision with its independent retrieval score/explanation."""

    memory: Memory
    score: FiniteScore
    ranking: tuple[RankingExplanation, ...] = ()


class ArchiveResult(ContractModel):
    """A permitted source revision or rebuildable chunk with branch/source labels."""

    record: SourceRecord | ArchiveChunk
    score: FiniteScore
    ranking: tuple[RankingExplanation, ...] = ()
    branch_status: WireString = "not_evaluated"


class RetrievalCoverage(ContractModel):
    """Explicit search/context coverage; an empty result is not an index/source failure."""

    status: Literal["complete", "partial", "unavailable"]
    index_status: Literal["available", "degraded", "unavailable"]
    warnings: tuple[ErrorInfo, ...] = ()
    reason_codes: tuple[WireString, ...] = ()


class SearchResponse(ContractModel):
    """Bounded ranked beliefs, pagination, and truthful derived-index coverage."""

    results: Annotated[tuple[MemoryResult, ...], Field(max_length=100)]
    coverage: RetrievalCoverage
    next_cursor: str | None = None


class ArchiveSearchResponse(ContractModel):
    """Bounded deep archive results with current authorization rechecked by the client."""

    results: Annotated[tuple[ArchiveResult, ...], Field(max_length=100)]
    coverage: RetrievalCoverage
    next_cursor: str | None = None


class ContextRequest(ContractModel):
    """Explicit working-context budgets; indexes and runtime prompt composition remain separate."""

    memory_query: MemoryQuery
    archive_query: ArchiveQuery | None = None
    memory_token_budget: Annotated[int, Field(gt=0)]
    archive_token_budget: NonnegativeInteger = 0
    summary_token_budget: NonnegativeInteger = 0
    include_recent_native: bool = True
    include_memory_search: bool = Field(default=True, exclude_if=lambda value: value)
    include_pinned: bool = True
    include_profile: bool = False
    include_summaries: bool = True
    options: RequestOptions = Field(default_factory=RequestOptions)


class RetrievalContext(ContractModel):
    """Structured prompt inputs/dependencies; no prebuilt prompt or generated belief."""

    memories: Annotated[tuple[MemoryResult, ...], Field(max_length=100)] = ()
    archive: Annotated[tuple[ArchiveResult, ...], Field(max_length=100)] = ()
    pinned: Annotated[tuple[PinnedMemory, ...], Field(max_length=100)] = ()
    summaries: Annotated[tuple[SessionSummary, ...], Field(max_length=100)] = ()
    dependencies: tuple[SourceRef, ...] = ()
    coverage: RetrievalCoverage
    profile: ProfileSelection | None = None
    fast_dependencies: ContextDependencies | None = None


class PageRequest(ContractModel):
    """Bounded recent/pinned retrieval scoped by the trusted client binding."""

    limit: ResultLimit = 20
    options: RequestOptions = Field(default_factory=RequestOptions)


class MutationRequest(ContractModel):
    """Idempotency is scoped to the bound character/operation and exact request identity.

    Reusing a key with different content must fail. An uncertain transport timeout
    never authorizes a different key/retry that might duplicate a committed mutation.
    """

    idempotency_key: IdempotencyKey
    options: RequestOptions = Field(default_factory=RequestOptions)


class RevisionMutation(MutationRequest):
    """Compare-and-swap target; it cannot change the trusted character binding."""

    memory_id: CanonicalId
    expected_revision: Revision


class RememberRequest(MutationRequest):
    """Propose content to the bound character; engine policy supplies ownership/recorded time."""

    draft: MemoryDraft


class UpdateRequest(RevisionMutation):
    """Append a replacement interpretation revision without mutating historical evidence."""

    draft: MemoryDraft
    reason: WireString


class SupersedeRequest(RevisionMutation):
    """Explicit transition evidence, preserving the old belief and temporal interpretation."""

    replacement: MemoryDraft
    transition_evidence: tuple[EvidenceAssertion, ...]
    previous_valid_until: UtcTimestamp | None = None
    reason: WireString


class DisableRequest(RevisionMutation):
    """Remove from recall without implying dependency-wide forgetting or source deletion."""

    reason: WireString


class ForgetRequest(RevisionMutation):
    """Create durable dependency suppression; never expand grants or delete shared sources."""

    scope: Literal["memory_and_dependencies"] = "memory_and_dependencies"
    reason: WireString


class PinRequest(RevisionMutation):
    """Pin the exact permitted revision; a pin cannot bypass lifecycle or suppression."""

    priority: NonnegativeInteger = 0


class UnpinRequest(RevisionMutation):
    """Remove a pin using current expected memory revision and idempotent mutation identity."""


class MemoryAccess(ContractModel):
    """Access observation for an exact permitted revision, independent of source time."""

    memory_id: CanonicalId
    memory_revision: Revision
    accessed_at: UtcTimestamp


class RecordAccessRequest(MutationRequest):
    """Bounded idempotent access statistics; no implicit claim/importance mutation."""

    accesses: Annotated[tuple[MemoryAccess, ...], Field(min_length=1, max_length=100)]


class RecordRevision(ContractModel):
    """A canonical mutation result reference, never a backend row/vector key."""

    memory_id: CanonicalId
    revision: Revision


class MutationReceipt(ContractModel):
    """Confirmed commit/idempotent replay result; ownership remains the bound character."""

    operation_id: CanonicalId
    idempotency_key: IdempotencyKey
    outcome: Literal["applied", "already_applied"]
    affected: tuple[RecordRevision, ...]
    recorded_at: UtcTimestamp


@runtime_checkable
class MemoryStore(Protocol):
    """Synchronous, character-bound canonical operations implemented in phase 2.

    Implementations recheck grants/suppression for reads and evidence/policy/CAS for
    writes. Ordinary empty results are responses; domain failures use MemoryError.
    Blocking calls must not run directly on the realtime event loop.
    """

    @property
    def character_id(self) -> CanonicalId:
        """Trusted immutable identity supplied by composition, never query/model arguments."""
        ...

    def capabilities(self) -> MemoryCapabilities:
        """Return actual backend/index/contract support."""
        ...

    def search(self, query: MemoryQuery) -> SearchResponse:
        """Search permitted beliefs, preserving historical/temporal query semantics."""
        ...

    def search_archive(self, query: ArchiveQuery) -> ArchiveSearchResponse:
        """Search only currently granted unsuppressed source scope."""
        ...

    def retrieve_context(self, request: ContextRequest) -> RetrievalContext:
        """Return bounded structured inputs and coverage for runtime prompt composition."""
        ...

    def get(
        self,
        memory_id: CanonicalId,
        *,
        revision: Revision | None = None,
        options: RequestOptions | None = None,
    ) -> Memory:
        """Read an exact/latest permitted belief revision."""
        ...

    def get_sources(
        self,
        memory_id: CanonicalId,
        *,
        revision: Revision | None = None,
        options: RequestOptions | None = None,
    ) -> tuple[MemorySource, ...]:
        """Resolve original evidence with current grants/suppression and exact validation."""
        ...

    def get_recent(self, request: PageRequest) -> SearchResponse:
        """Return bounded recent permitted beliefs with truthful coverage."""
        ...

    def get_pinned(self, request: PageRequest) -> tuple[PinnedMemory, ...]:
        """Return permitted pins; forgotten/disabled revisions cannot reenter recall."""
        ...

    def remember(self, request: RememberRequest) -> MutationReceipt:
        """Validate policy/evidence before recording character-owned content."""
        ...

    def update(self, request: UpdateRequest) -> MutationReceipt:
        """Append an interpretation revision using compare-and-swap."""
        ...

    def supersede(self, request: SupersedeRequest) -> MutationReceipt:
        """Apply a validated explicit transition without erasing historical assertions."""
        ...

    def disable(self, request: DisableRequest) -> MutationReceipt:
        """Remove recall eligibility, preserving canonical history."""
        ...

    def forget(self, request: ForgetRequest) -> MutationReceipt:
        """Persist suppression and invalidate dependent pins/context/jobs atomically."""
        ...

    def pin(self, request: PinRequest) -> MutationReceipt:
        """Pin an authorized current revision without restoring suppressed content."""
        ...

    def unpin(self, request: UnpinRequest) -> MutationReceipt:
        """Remove a pin idempotently."""
        ...

    def record_access(self, request: RecordAccessRequest) -> MutationReceipt:
        """Record permitted accesses without creating independent corroboration."""
        ...


@runtime_checkable
class AsyncMemoryClient(Protocol):
    """Same character-bound operations through bounded engine workers or a future transport.

    Worker ownership, backpressure and cancellation belong to the implementation.
    Cancellation/timeout does not imply rollback; idempotency/CAS remain mandatory.
    """

    @property
    def character_id(self) -> CanonicalId:
        """Trusted immutable character identity supplied by composition."""
        ...

    async def capabilities(self) -> MemoryCapabilities:
        """Return actual implementation capabilities."""
        ...

    async def search(self, query: MemoryQuery) -> SearchResponse:
        """Search permitted beliefs without blocking the realtime loop."""
        ...

    async def search_archive(self, query: ArchiveQuery) -> ArchiveSearchResponse:
        """Search granted unsuppressed sources through bounded owned work."""
        ...

    async def retrieve_context(self, request: ContextRequest) -> RetrievalContext:
        """Retrieve bounded structured context and explicit coverage."""
        ...

    async def get(
        self,
        memory_id: CanonicalId,
        *,
        revision: Revision | None = None,
        options: RequestOptions | None = None,
    ) -> Memory:
        """Read an exact/latest permitted belief revision."""
        ...

    async def get_sources(
        self,
        memory_id: CanonicalId,
        *,
        revision: Revision | None = None,
        options: RequestOptions | None = None,
    ) -> tuple[MemorySource, ...]:
        """Resolve exact source evidence under current authorization."""
        ...

    async def get_recent(self, request: PageRequest) -> SearchResponse:
        """Retrieve bounded recent permitted beliefs."""
        ...

    async def get_pinned(self, request: PageRequest) -> tuple[PinnedMemory, ...]:
        """Retrieve permitted unsuppressed pins."""
        ...

    async def remember(self, request: RememberRequest) -> MutationReceipt:
        """Validate and record a character-owned belief idempotently."""
        ...

    async def update(self, request: UpdateRequest) -> MutationReceipt:
        """Append a revision using compare-and-swap."""
        ...

    async def supersede(self, request: SupersedeRequest) -> MutationReceipt:
        """Apply a validated explicit historical transition."""
        ...

    async def disable(self, request: DisableRequest) -> MutationReceipt:
        """Remove recall eligibility while preserving history."""
        ...

    async def forget(self, request: ForgetRequest) -> MutationReceipt:
        """Persist dependency suppression and invalidate copied context."""
        ...

    async def pin(self, request: PinRequest) -> MutationReceipt:
        """Pin an authorized exact revision."""
        ...

    async def unpin(self, request: UnpinRequest) -> MutationReceipt:
        """Remove a pin idempotently."""
        ...

    async def record_access(self, request: RecordAccessRequest) -> MutationReceipt:
        """Record bounded permitted access statistics idempotently."""
        ...


@runtime_checkable
class ArchiveWriter(Protocol):
    """Trusted maintenance writer, separate from character/harness client mutations."""

    @property
    def source_store_id(self) -> CanonicalId:
        """Immutable store binding supplied by maintenance composition."""
        ...

    def write_batch(self, batch: NormalizedArchiveBatch) -> ArchiveWriteReceipt:
        """Commit source revisions/crosswalks and resume cursor in one idempotent transaction."""
        ...
