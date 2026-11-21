"""Explicit bounded investigations through ABI clients; no model planning or mutation."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.evidence import Evidence
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    AsyncMemoryClient,
    MemoryResult,
    RankingExplanation,
    RequestOptions,
)
from ember_memory_abi.read_profiles import ReadUsageClient
from ember_memory_abi.related import (
    AsyncRelatedLookupClient,
    ContextDependencies,
    MemoryRelationship,
    RelatedRequest,
    RelatedResponse,
    ResolvedEntity,
    SourceObservation,
)
from ember_memory_abi.temporal import TemporalQuery

from ..character.temporal import CalendarResolver, QueryResolution
from .deep_admission import DeepAdmission
from .deep_contracts import DeepRecallRequest, DeepRecallResult, RecallCounters
from .deep_graph import connections, reference, relevant, terms
from .deep_prompt import condense
from .deep_sources import archive_evidence


class DeepClient(AsyncMemoryClient, AsyncRelatedLookupClient, ReadUsageClient, Protocol):
    """A composition-owned read-only handle with actual counters and an explicit drain."""

    async def aclose(self) -> object:
        """Drain accepted native work before releasing the investigation slot."""
        ...


@dataclass
class _Work:
    request: DeepRecallRequest
    client: DeepClient
    started: float
    deadline_at: str
    temporal: TemporalQuery
    topics: set[str]
    selected: dict[UUID, MemoryResult] = field(default_factory=lambda: {})
    inspected: set[tuple[UUID, int]] = field(default_factory=lambda: set())
    edges: dict[UUID, MemoryRelationship] = field(default_factory=lambda: {})
    reasons: set[str] = field(default_factory=lambda: set())
    abi_calls: int = 0
    searches: int = 0
    rows: int = 0
    checkpoint: RelatedResponse | None = None

    def accept(
        self,
        result: MemoryResult,
        *,
        expanded: bool,
        aliases: tuple[ResolvedEntity, ...] = (),
    ) -> bool:
        memory = result.memory
        key = (memory.memory_id, memory.revision)
        if key in self.inspected:
            return False
        if len(self.inspected) >= self.request.budget.inspected:
            self.reasons.add("inspected_budget")
            return False
        self.inspected.add(key)
        if expanded and not relevant(memory, self.topics, aliases):
            return False
        if len(self.selected) >= self.request.budget.memories:
            self.reasons.add("memory_budget")
            return False
        self.selected.setdefault(memory.memory_id, result)
        return True

    async def related(self, **fields: object) -> RelatedResponse:
        # Fields are internal typed contract data; the validated request cannot raise caps.
        if self.abi_calls >= self.request.budget.abi_calls - 1:
            self.reasons.add("abi_call_budget")
            return RelatedResponse(status="partial", reasons=("abi_call_budget",))
        self.abi_calls += 1
        request = RelatedRequest.model_validate(
            {
                "intent": "expand",
                "recall_only": True,
                "temporal": self.temporal,
                "deadline_at": self.deadline_at,
                "limit": self.request.budget.memories,
                "row_budget": 256,
                **fields,
            }
        )
        response = await self.client.lookup_related(request)
        self.rows += response.rows_examined
        if (
            response.status in {"complete", "partial"}
            and response.dependencies is not None
            and response.memories
        ):
            self.checkpoint = response
        if response.status != "complete":
            self.reasons.update(response.reasons or ("related_partial",))
        return response


class DeepRecallService:
    """Character-bound investigations with an ephemeral, revalidated inspector.

    A factory reuses composition's existing store/workers and shared encoder. Its
    request-local meter begins at admission, includes queue time, and bounds SQL,
    hydration and result serialization. Ordinary recall never invokes this service.
    """

    def __init__(
        self,
        character_id: UUID,
        factory: Callable[[float], DeepClient],
        *,
        admission: DeepAdmission | None = None,
        validation: AsyncRelatedLookupClient,
    ) -> None:
        self.character_id = character_id
        self.factory = factory
        self.admission = admission or DeepAdmission()
        self.validation = validation
        self._results: dict[UUID, tuple[float, DeepRecallResult]] = {}

    async def investigate(
        self, request: DeepRecallRequest, *, retain_for_inspection: bool = True
    ) -> DeepRecallResult:
        """Explicit invocation only; the trusted character binding cannot be broadened."""
        if request.character_id != self.character_id:
            return self._empty(request, "unavailable", "character_scope_mismatch")
        started = monotonic()
        result = await self.admission.submit(request, lambda: self._run(request, started))
        # One-shot consumers validate their own delivery and never enter the inspector cache.
        if not retain_for_inspection:
            return result
        self._prune()
        if result.outcome in {
            "complete_within_budget",
            "partial",
            "unavailable",
            "deferred",
            "cancelled",
        }:
            # Keep at most one result per conversation; never persist result collections.
            self._results = {
                key: value
                for key, value in self._results.items()
                if value[1].context_id != request.context_id
            }
            self._results[request.request_id] = (monotonic() + 300, result)
            self._prune()
        return result

    async def inspect(self, request_id: UUID, context_id: UUID) -> DeepRecallResult | None:
        """Context-bound inspection rechecks current privacy, revision and grant policy."""
        self._prune()
        cached = self._results.get(request_id)
        if cached is None or cached[1].context_id != context_id:
            return None
        result = cached[1]
        if result.dependencies is None:
            return result if not result.memories and not result.observations else None
        try:
            validation = await self.validation.lookup_related(
                RelatedRequest(
                    intent="validate",
                    dependencies=result.dependencies,
                    row_budget=512,
                    call_budget=32,
                    deadline_at=timestamp_from_datetime(datetime.now(UTC) + timedelta(seconds=1)),
                )
            )
        except MemoryError:
            validation = RelatedResponse(status="invalid", valid=False)
        if validation.valid is not True:
            self._results.pop(request_id, None)
            return None
        return result

    def cancel(self, request_id: UUID) -> bool:
        """Signal this character's owned request and discard its ephemeral inspection."""
        self._results.pop(request_id, None)
        return self.admission.cancel(request_id, self.character_id)

    async def aclose(self) -> None:
        """Discard results and drain accepted investigations before the engine closes."""
        self._results.clear()
        await self.admission.aclose()

    def _prune(self) -> None:
        now = monotonic()
        self._results = {key: value for key, value in self._results.items() if value[0] > now}
        while len(self._results) > 16:
            self._results.pop(next(iter(self._results)))

    @staticmethod
    def _empty(request: DeepRecallRequest, outcome: str, reason: str) -> DeepRecallResult:
        return DeepRecallResult.model_validate(
            {
                "request_id": request.request_id,
                "character_id": request.character_id,
                "context_id": request.context_id,
                "outcome": outcome,
                "reasons": (reason,),
            }
        )

    async def _archive_observations(
        self, work: _Work, resolution: QueryResolution, accepted: RelatedResponse
    ) -> tuple[tuple[SourceObservation, ...], ContextDependencies | None]:
        """Add authorized neighbors without turning observed clocks into event dates."""
        existing = accepted.excerpts
        allowance = min(
            work.request.budget.archive_chunks,
            work.request.budget.excerpts - len(existing),
        )
        if not allowance or not work.topics:
            return existing, accepted.dependencies
        if resolution.scope is not None and resolution.scope.axis != "observed":
            work.reasons.add("archive_observation_dates_cannot_supply_event_time")
            return existing, accepted.dependencies
        conversations = tuple(
            dict.fromkeys(
                observation.conversation_id
                for observation in existing
                if observation.conversation_id is not None
            )
        )
        source_stores = tuple(
            dict.fromkeys(
                observation.source.evidence.source.source_store_id
                for observation in existing
                if observation.conversation_id is not None
            )
        )
        if work.selected and not conversations:
            work.reasons.add("source_neighborhood_unavailable")
            return existing, accepted.dependencies
        if (
            work.searches >= work.request.budget.search_calls
            or work.abi_calls > work.request.budget.abi_calls - 2
        ):
            work.reasons.add("archive_call_budget")
            return existing, accepted.dependencies
        work.abi_calls += 1
        work.searches += 1
        archive = await work.client.search_archive(
            ArchiveQuery(
                text=resolution.query.text,
                limit=allowance,
                conversation_ids=conversations,
                source_store_ids=source_stores,
                roles=("user",),
                source_from=resolution.scope.start if resolution.scope else None,
                source_until=resolution.scope.end if resolution.scope else None,
                options=RequestOptions(deadline_at=work.deadline_at),
            )
        )
        if archive.coverage.status != "complete":
            work.reasons.update(archive.coverage.reason_codes or ("archive_partial",))
        known = {
            (
                item.source.evidence.source.identity_key(),
                item.source.evidence.span.quoted_span_sha256,
            )
            for item in existing
        }
        evidence = tuple(
            item
            for item in archive_evidence(archive.results, topic_terms=work.topics)
            if (item.source.identity_key(), item.span.quoted_span_sha256) not in known
        )[:allowance]
        if not evidence:
            return existing, accepted.dependencies
        work.abi_calls += 1
        checked = await work.client.lookup_related(
            RelatedRequest(
                intent="expand",
                include_relationships=False,
                recall_only=True,
                excerpts=evidence,
                source_capability="archive_search",
                temporal=work.temporal,
                row_budget=256,
                deadline_at=work.deadline_at,
            )
        )
        work.rows += checked.rows_examined
        if checked.status != "complete":
            work.reasons.update(checked.reasons or ("archive_validation_partial",))
        if checked.dependencies is None or accepted.dependencies is None:
            work.reasons.add("archive_validation_unavailable")
            return existing, accepted.dependencies
        if checked.dependencies.policy_epoch != accepted.dependencies.policy_epoch:
            work.reasons.add("archive_policy_changed")
            return existing, accepted.dependencies
        # Reuse certificates; archive_search remains an independent requirement.
        parent = accepted.dependencies
        other = checked.dependencies
        expiry = [stamp for stamp in (parent.valid_until, other.valid_until) if stamp]
        try:
            merged = ContextDependencies(
                memories=parent.memories,
                selections=parent.selections,
                sources=tuple(
                    {
                        source.identity_key(): source
                        for source in (*parent.sources, *other.sources)
                    }.values()
                ),
                excerpt_sources=tuple(
                    {
                        source.identity_key(): source
                        for source in (*parent.excerpt_sources, *other.excerpt_sources)
                    }.values()
                ),
                archive_sources=tuple(
                    {
                        source.identity_key(): source
                        for source in (*parent.archive_sources, *other.archive_sources)
                    }.values()
                ),
                policy_epoch=parent.policy_epoch,
                selection_epoch=parent.selection_epoch,
                valid_until=min(expiry) if expiry else None,
            )
        except ValidationError:
            work.reasons.add("archive_dependency_budget")
            return existing, parent
        used_bytes = sum(len((item.source.quote or "").encode("utf-8")) for item in existing)
        retained: list[SourceObservation] = []
        for item in checked.excerpts:
            size = len((item.source.quote or "").encode("utf-8"))
            if used_bytes + size > 32768:
                work.reasons.add("combined_excerpt_byte_budget")
                continue
            used_bytes += size
            retained.append(item)
        if retained:
            work.reasons.add("archive_observations_are_not_accepted_beliefs")
        return (*existing, *retained), merged

    def _finish(
        self,
        work: _Work,
        resolution: QueryResolution,
        final: RelatedResponse,
        observations: tuple[SourceObservation, ...],
        dependencies: ContextDependencies | None,
    ) -> DeepRecallResult:
        """Build only from a completed authorization checkpoint, without further reads."""
        request, client, started = work.request, work.client, work.started
        results = tuple(
            work.selected[memory.memory_id].model_copy(update={"memory": memory})
            for memory in final.memories
            if memory.memory_id in work.selected
        )
        checked_quotes = {
            (
                observation.source.evidence.source.identity_key(),
                observation.source.evidence.span.quoted_span_sha256,
            )
            for observation in final.excerpts
        }
        checked_edges = tuple(
            edge
            for edge in work.edges.values()
            if edge.evidence
            and all(
                (a.evidence.source.identity_key(), a.evidence.span.quoted_span_sha256)
                in checked_quotes
                for a in edge.evidence
            )
        )
        associations, clusters = connections(results, checked_edges, request.request_id)
        if resolution.scope is not None and resolution.scope.axis == "event":
            work.reasons.add("undated_records_excluded")
        usage = client.read_usage()
        result = DeepRecallResult(
            request_id=request.request_id,
            character_id=request.character_id,
            context_id=request.context_id,
            query=request.query,
            scope=resolution.scope,
            outcome="partial" if work.reasons else "complete_within_budget",
            memories=results,
            observations=observations,
            associations=associations,
            clusters=clusters,
            dependencies=dependencies,
            manifest=final.manifest,
            reasons=tuple(sorted(work.reasons))[:64],
            counters=RecallCounters(
                inspected=len(work.inspected),
                searches=work.searches,
                abi_calls=work.abi_calls,
                rows_reported=usage.materialized_rows,
                hydrated_spans=len(observations),
                elapsed_ms=int((monotonic() - started) * 1000),
                sql_statements=usage.sql_statements,
                materialized_bytes=usage.materialized_bytes,
                limits_hit=usage.limits_hit,
            ),
        )
        prompt = condense(result)
        result = result.model_copy(
            update={
                "prompt_memories": prompt.memories,
                "prompt_observations": prompt.observations,
            }
        )
        if len(result.model_dump_json().encode()) > request.budget.result_bytes:
            return self._empty(request, "partial", "serialized_result_budget").model_copy(
                update={"counters": result.counters}
            )
        return result

    async def _run(self, request: DeepRecallRequest, started: float) -> DeepRecallResult:
        deadline = started + request.budget.deadline_ms / 1000
        resolution = CalendarResolver(request.timezone).resolve(
            request.query,
            datetime.fromisoformat(request.reference_at.replace("Z", "+00:00")),
            request.previous_scope,
        )
        if resolution.status == "clarification":
            return self._empty(request, "unavailable", "temporal_clarification")
        if any(
            phrase in request.query.casefold()
            for phrase in (
                "before i moved",
                "after i moved",
                "when i had",
                "before the correction",
            )
        ):
            return self._empty(request, "unavailable", "supported_event_anchor_required")
        if request.budget.abi_calls < 3:
            return self._empty(request, "deferred", "insufficient_validation_budget")
        client = self.factory(deadline)
        remaining = max(0, deadline - monotonic())
        work = _Work(
            request=request,
            client=client,
            started=started,
            deadline_at=timestamp_from_datetime(datetime.now(UTC) + timedelta(seconds=remaining)),
            temporal=resolution.query.temporal,
            topics=terms(resolution.query.text),
        )
        try:
            work.abi_calls += 1
            capabilities = await client.capabilities()
            if "deep_read_profile_v1" not in capabilities.retrieval_modes:
                return self._empty(request, "unavailable", "deep_read_profile_unsupported")
            query = resolution.query.model_copy(
                update={
                    "limit": min(40, request.budget.inspected),
                    "options": RequestOptions(deadline_at=work.deadline_at),
                }
            )
            fallback = capabilities.index_status != "available"
            if fallback and query.text:
                query = query.model_copy(update={"text": "", "mode": "structured"})
                work.reasons.add("bounded_structured_fallback")
            work.abi_calls += 1
            work.searches += 1
            searched = await client.search(query)
            if searched.coverage.status != "complete":
                work.reasons.update(searched.coverage.reason_codes or ("search_partial",))
            candidates = (
                tuple(result for result in searched.results if relevant(result.memory, work.topics))
                if fallback and work.topics
                else searched.results
            )
            anchors = candidates[: request.budget.anchors]
            for result in anchors:
                work.accept(result, expanded=False)
            frontier = tuple(reference(result.memory) for result in anchors)
            for hop in range(request.budget.hops):
                usage = client.read_usage()
                if usage.materialized_rows > 1200 or usage.sql_statements > 3000:
                    work.reasons.add("validation_reserve")
                    break
                sources = {
                    assertion.evidence.source.identity_key(): assertion.evidence.source
                    for result in tuple(work.selected.values())[:6]
                    for assertion in result.memory.content.evidence
                }
                expanded = await work.related(
                    anchors=frontier,
                    sources=tuple(sources.values())[:32],
                    entity_names=tuple(sorted(work.topics))[:8] if hop == 0 else (),
                )
                for edge in expanded.relationships:
                    work.edges.setdefault(edge.relationship_id, edge)
                frontier = tuple(
                    reference(memory)
                    for memory in expanded.memories
                    if work.accept(
                        MemoryResult(
                            memory=memory,
                            score=0,
                            ranking=(
                                RankingExplanation(
                                    method="bounded_related",
                                    score=0,
                                    reasons=(
                                        "authorized_related_scope",
                                        "original_query_topic"
                                        if work.topics and relevant(memory, work.topics)
                                        else "authorized_entity_alias"
                                        if work.topics
                                        else "original_query_scope",
                                    ),
                                ),
                            ),
                        ),
                        expanded=True,
                        aliases=expanded.entities,
                    )
                )
                if not frontier:
                    break
            excerpts: tuple[Evidence, ...] = tuple(
                assertion.evidence
                for result in work.selected.values()
                for assertion in result.memory.content.evidence
                if assertion.evidence.span.end_codepoint - assertion.evidence.span.start_codepoint
                <= 2000
            )[: request.budget.excerpts]
            edge_quotes = tuple(
                assertion.evidence
                for edge in work.edges.values()
                for assertion in edge.evidence
                if assertion.evidence.span.end_codepoint - assertion.evidence.span.start_codepoint
                <= 2000
            )
            quotes = {
                (e.source.identity_key(), e.span.quoted_span_sha256): e
                for e in (*excerpts, *edge_quotes)
            }
            excerpts = tuple(quotes.values())[: request.budget.excerpts]
            work.abi_calls += 1
            final = await client.lookup_related(
                RelatedRequest.model_validate(
                    {
                        "intent": "expand",
                        "recall_only": True,
                        "include_relationships": False,
                        "anchors": tuple(
                            reference(result.memory) for result in work.selected.values()
                        ),
                        "excerpts": excerpts,
                        "source_capability": "evidence_read",
                        "temporal": work.temporal,
                        "limit": request.budget.memories,
                        "row_budget": 512,
                        "call_budget": 32,
                        "deadline_at": work.deadline_at,
                    }
                )
            )
            work.rows += final.rows_examined
            if final.status != "complete":
                work.reasons.update(final.reasons or ("delivery_validation_partial",))
            try:
                observations, dependencies = await self._archive_observations(
                    work, resolution, final
                )
            except MemoryError as error:
                if error.info.code not in {"read_budget_exceeded", "deadline_exceeded"}:
                    raise
                # Optional archive expansion cannot erase a completed, authorized
                # memory checkpoint. Inspection/delivery still revalidate its certificate.
                work.reasons.add(error.info.code)
                work.reasons.add("archive_expansion_budget")
                observations, dependencies = final.excerpts, final.dependencies
            return self._finish(work, resolution, final, observations, dependencies)
        except MemoryError as error:
            if (
                error.info.code in {"read_budget_exceeded", "deadline_exceeded"}
                and work.checkpoint is not None
            ):
                work.reasons.update((error.info.code, "validated_checkpoint_only"))
                return self._finish(
                    work,
                    resolution,
                    work.checkpoint,
                    work.checkpoint.excerpts,
                    work.checkpoint.dependencies,
                )
            usage = client.read_usage()
            return self._empty(
                request,
                "deferred" if error.info.code == "deep_deferred_for_foreground" else "partial",
                error.info.code,
            ).model_copy(
                update={
                    "counters": RecallCounters(
                        inspected=len(work.inspected),
                        searches=work.searches,
                        abi_calls=work.abi_calls,
                        rows_reported=usage.materialized_rows,
                        sql_statements=usage.sql_statements,
                        materialized_bytes=usage.materialized_bytes,
                        limits_hit=usage.limits_hit,
                        elapsed_ms=int((monotonic() - started) * 1000),
                    )
                }
            )
        finally:
            # Even cancelled SQLite/vector jobs retain their slot until the handle drains.
            await client.aclose()
