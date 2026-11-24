"""Character-bound canonical MemoryStore: transactional mutations and safe degraded reads."""

import sqlite3
from collections.abc import Callable
from concurrent.futures import Future

from ember_memory_abi.common import (
    CanonicalId,
)
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ArchiveSearchResponse,
    ContextRequest,
    DisableRequest,
    ForgetRequest,
    MemoryCapabilities,
    MemoryQuery,
    MutationReceipt,
    MutationRequest,
    PageRequest,
    PinRequest,
    RecordAccessRequest,
    RememberRequest,
    RequestOptions,
    RetrievalContext,
    SearchResponse,
    SupersedeRequest,
    UnpinRequest,
    UpdateRequest,
)
from ember_memory_abi.memory import (
    Memory,
    MemorySource,
    PinnedMemory,
)
from ember_memory_abi.predicates import KNOWN_MODALITIES
from ember_memory_abi.related import RelatedRequest, RelatedResponse
from ember_memory_abi.selection import RecallSelection

from . import identity_recall, mutations, profile, related, retrieval, selection
from ._state import CharacterState
from .archive import SourceResolver
from .database import (
    SqliteDatabase,
)
from .models import ForgetImpact, MutationAuthority, TokenCounter
from .profile import ProfileSettings
from .retrieval_port import RetrievalBackend


class SqliteMemoryStore:
    """Character-bound ABI facade; SQL/policy state and algorithms remain inside the engine."""

    def __init__(
        self,
        database: SqliteDatabase,
        resolver: SourceResolver,
        authority: MutationAuthority,
        token_counter: TokenCounter,
        retriever: RetrievalBackend | None = None,
    ) -> None:
        self.database = database
        self.state = CharacterState(database, resolver, authority, token_counter, retriever)

    @property
    def character_id(self) -> CanonicalId:
        """Trusted composition-supplied identity, never chosen by query/model arguments."""
        return self.database.store_id

    def recall_selection(self) -> RecallSelection:
        """Read the existing character's persistent source-selection policy."""
        return self.database.read(selection.settings)

    def configure_recall(self, value: RecallSelection) -> RecallSelection:
        """Apply an operator-authorized compare-and-swap selection change."""
        return self.database.write(
            lambda connection: selection.configure(self.state, connection, value)
        )

    def capabilities(self, *, _state: CharacterState | None = None) -> MemoryCapabilities:
        """Report implemented modes and current character index availability."""
        state = _state or self.state
        mutations = (
            (
                "remember",
                "update",
                "supersede",
                "disable",
                "forget",
                "pin",
                "unpin",
                "record_access",
            )
            if state.authority.mode == "confirmed_user"
            else ()
        )
        return MemoryCapabilities(
            supported_kinds=("fact", "episode", "goal", "plan"),
            supported_modalities=tuple(sorted(KNOWN_MODALITIES)),
            supported_predicates=tuple(definition.name for definition in state.predicates),
            supported_extensions=(identity_recall.IDENTITY_RECALL, identity_recall.CURRENT_BELIEF),
            retrieval_modes=(
                "hybrid_if_indexed",
                "deep_archive_if_indexed",
                "canonical_structured",
                "canonical_literal_fallback",
                "recent",
                "pins",
                "provenance",
                "profile_pins_v1",
                "related_lookup_v1",
                "batch_context_validation_v1",
                "selected_context_dependencies_v1",
                "current_conflict_status_v1",
                "temporal_ranges_v1",
                "source_selection_v1",
                "deep_read_profile_v1",
                "related_neighborhood_v1",
                "historical_context_validation_v1",
                "pins_only_context_v1",
            ),
            mutations=mutations,
            index_status="available"
            if state.retriever and state.retriever.available()
            else "degraded",
        )

    def submit[T](
        self, work: Callable[[sqlite3.Connection], T], *, write: bool = False, deep: bool = False
    ) -> Future[T]:
        """Engine-internal owned work boundary used by the async ABI facade."""
        return self.database.submit(work, write=write, deep=deep)

    def get(
        self,
        memory_id: CanonicalId,
        *,
        revision: int | None = None,
        options: RequestOptions | None = None,
    ) -> Memory:
        """Read latest/exact owned history, enforcing current suppression even for past
        revisions.
        """
        return self.database.read(
            lambda connection: self.state.read_memory(connection, memory_id, revision, options)
        )

    def get_sources(
        self,
        memory_id: CanonicalId,
        *,
        revision: int | None = None,
        options: RequestOptions | None = None,
    ) -> tuple[MemorySource, ...]:
        """Resolve immutable original quotes with fresh permission/suppression checks."""
        return self.database.read(
            lambda connection: self.state.read_sources(connection, memory_id, revision, options)
        )

    def _mutation(self, operation: str, request: MutationRequest) -> MutationReceipt:
        return self.database.write(
            lambda connection: mutations.mutate(self.state, connection, operation, request)
        )

    def remember(self, request: RememberRequest) -> MutationReceipt:
        """Persist manually authorized, evidence-validated beliefs and receipt/outbox atomically."""
        return self._mutation("remember", request)

    def update(self, request: UpdateRequest) -> MutationReceipt:
        """Append an expected-revision update while preserving prior interpretation history."""
        return self._mutation("update", request)

    def supersede(self, request: SupersedeRequest) -> MutationReceipt:
        """Apply explicit single-valued replacement evidence, without erasing old assertions."""
        return self._mutation("supersede", request)

    def disable(self, request: DisableRequest) -> MutationReceipt:
        """Remove from recall and invalidate its pin, retaining source and belief revisions."""
        return self._mutation("disable", request)

    def forget(self, request: ForgetRequest) -> MutationReceipt:
        """Persist conservative dependency suppression and outbox/policy invalidation."""
        return self._mutation("forget", request)

    def pin(self, request: PinRequest) -> MutationReceipt:
        """Pin only active unsuppressed owned revisions."""
        return self._mutation("pin", request)

    def unpin(self, request: UnpinRequest) -> MutationReceipt:
        """Remove the pin idempotently, with expected memory revision validation."""
        return self._mutation("unpin", request)

    def record_access(self, request: RecordAccessRequest) -> MutationReceipt:
        """Commit bounded idempotent access observations without changing importance/claims."""
        return self._mutation("record_access", request)

    def preview_forget(self, memory_id: CanonicalId) -> ForgetImpact:
        """Trusted maintenance preview of conservative claim/alias/source suppression impact."""
        return self.database.read(
            lambda connection: self.state.forget_impact(
                connection, memory_id, self.state.read_memory(connection, memory_id).revision
            )
        )

    def search(self, query: MemoryQuery) -> SearchResponse:
        """Run bounded structured/literal fallback, with scope/suppression filters before result
        limits.
        """
        return self.database.read(
            lambda connection: retrieval.search(self.state, connection, query)
        )

    def get_pinned(self, request: PageRequest) -> tuple[PinnedMemory, ...]:
        """Read only active current permitted pins; old/forgotten pins never resurrect recall."""
        return self.database.read(
            lambda connection: retrieval.get_pinned(self.state, connection, request)
        )

    def get_recent(self, request: PageRequest) -> SearchResponse:
        """Return active canonical recent beliefs with honest fallback coverage."""
        return self.database.read(
            lambda connection: retrieval.recent(self.state, connection, request)
        )

    def search_archive(self, query: ArchiveQuery) -> ArchiveSearchResponse:
        """Search current explicit snapshots/native sources, never widening grants after
        reimport.
        """
        return self.database.submit(
            lambda connection: retrieval.search_archive(self.state, connection, query), deep=True
        ).result()

    def retrieve_context(self, request: ContextRequest) -> RetrievalContext:
        """Build budgeted structured context with owned source dependencies, not a model prompt."""
        return self.database.read(
            lambda connection: retrieval.retrieve_context(self.state, connection, request)
        )

    def lookup_related(self, request: RelatedRequest) -> RelatedResponse:
        """Optional bounded relationships/validation through existing owned readers."""
        return self.database.submit(
            lambda connection: related.lookup(self.state, connection, request),
            deep=request.intent != "validate",
        ).result()

    def profile_settings(self) -> ProfileSettings:
        """Inspect durable profile binding/exclusions without changing them."""
        return self.database.read(lambda connection: profile.settings(self.state, connection))

    def configure_profile(self, value: ProfileSettings) -> None:
        """Persist trusted human choices independently from model, pins and source grants."""
        self.database.write(lambda connection: profile.configure(self.state, connection, value))
