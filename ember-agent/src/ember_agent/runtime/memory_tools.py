"""Character-bound, claims-only operations shared by memory tool transports."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import Field, StringConstraints

from ember_memory_abi.common import timestamp_from_datetime, timestamp_to_unix_microseconds
from ember_memory_abi.errors import AccessDeniedError, MemoryError
from ember_memory_abi.interfaces import (
    ContextRequest,
    MemoryQuery,
    MemoryResult,
    MutationReceipt,
    RequestOptions,
    RetrievalContext,
)
from ember_memory_abi.related import AsyncRelatedLookupClient, RelatedRequest

from ..character.prompt_budget import recall_value
from ..character.temporal import CalendarScope
from ..memory.deep_contracts import DeepRecallBudget, DeepRecallRequest, RecallAssociation
from ..memory.deep_recall import DeepRecallService
from .config import RuntimeModel

MAX_RESULTS = 8
MAX_RESULT_BYTES = 6144
DEADLINE_SECONDS = 4
DEEP_MAX_CLAIM_BYTES = 12_288
DEEP_MAX_ASSOCIATIONS = 16
DEEP_VALIDATION_SECONDS = 1
RecallQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)]
NoteText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
type NoteWriter = Callable[[str], Awaitable[MutationReceipt]]


class RecallClient(AsyncRelatedLookupClient, Protocol):
    """Only the existing recall and delivery-validation calls cross this boundary."""

    async def retrieve_context(self, request: ContextRequest) -> RetrievalContext:
        """Return character-bound, policy-filtered context."""
        ...


class RecallHit(RuntimeModel):
    """A claim with citation identity, not an archive record or source envelope."""

    memory_id: UUID
    revision: int
    kind: str
    claim: dict[str, object] = Field(repr=False)


class RecallResult(RuntimeModel):
    """Bounded hits and the engine's coverage status."""

    memories: tuple[RecallHit, ...]
    coverage: Literal["complete", "partial", "unavailable"]
    omitted_for_size: int


class DeepMCPResult(RuntimeModel):
    """Bounded investigation results, without source records or inspection handles."""

    outcome: Literal["complete_within_budget", "partial", "unavailable", "cancelled", "deferred"]
    memories: tuple[RecallHit, ...]
    associations: tuple[RecallAssociation, ...]
    scope: CalendarScope | None
    reasons: tuple[str, ...]
    omitted_for_size: int


def _project_hits(
    memories: tuple[MemoryResult, ...], byte_limit: int | None, *, coding: bool = False
) -> tuple[tuple[RecallHit, ...], int]:
    hits: list[RecallHit] = []
    omitted = 0
    for result in memories:
        item = result.memory
        hit = RecallHit(
            memory_id=item.memory_id,
            revision=item.revision,
            kind=item.content.kind,
            claim=(
                {
                    **recall_value(item, historical=True),
                    "confidence": item.content.confidence,
                    "relevance": result.score,
                }
                if coding
                else recall_value(item)
            ),
        )
        cost = len(hit.model_dump_json().encode("utf-8")) + 1
        if byte_limit is not None and cost > byte_limit:
            omitted += 1
            continue
        hits.append(hit)
        if byte_limit is not None:
            byte_limit -= cost
    return tuple(hits), omitted


class NoteResult(RuntimeModel):
    """Content-free receipt for a note adopted by the active character."""

    library: Literal["OpenCode", "Coding"] = "OpenCode"
    outcome: Literal["applied", "already_applied"]
    memory_id: UUID
    revision: int


class MemoryToolError(RuntimeError):
    """Safe tool diagnostic with no private query or note content."""


def _coding_rank(results: tuple[MemoryResult, ...]) -> tuple[MemoryResult, ...]:
    """Keep relevance dominant and use confidence and recent observation as bounded boosts."""
    now = datetime.now(UTC)

    def score(item: MemoryResult) -> float:
        temporal = item.memory.temporal
        stamp = temporal.observed_at or temporal.valid_from or temporal.recorded_at
        age = max(0.0, now.timestamp() - timestamp_to_unix_microseconds(stamp) / 1_000_000)
        recent = max(0.0, 1 - age / (90 * 86_400))
        adjustment = 0.10 * item.memory.content.confidence + 0.10 * recent
        return item.score + abs(item.score) * adjustment

    return tuple(sorted(results, key=score, reverse=True))


class MemoryTools:
    """Apply existing character recall policies and serialize bounded tool operations."""

    def __init__(
        self,
        memory: RecallClient,
        deep: DeepRecallService,
        timezone: str,
        write_note: NoteWriter | None,
        *,
        library: Literal["OpenCode", "Coding"] = "OpenCode",
    ) -> None:
        self.memory = memory
        self.deep = deep
        self.timezone = timezone
        self.write_note = write_note
        self.library: Literal["OpenCode", "Coding"] = library
        self.admission = asyncio.Lock()

    async def recall(self, query: RecallQuery, *, coding_budget: int | None = None) -> RecallResult:
        """Search the active Ember character's recall-eligible memories by topic."""
        if self.admission.locked():
            raise MemoryToolError("Ember recall is busy; try again later.")
        async with self.admission:
            try:
                async with asyncio.timeout(DEADLINE_SECONDS):
                    deadline = timestamp_from_datetime(
                        datetime.now(UTC) + timedelta(seconds=DEADLINE_SECONDS)
                    )
                    options = RequestOptions(deadline_at=deadline)
                    context = await self.memory.retrieve_context(
                        ContextRequest(
                            memory_query=MemoryQuery(
                                text=query,
                                limit=100 if coding_budget is not None else MAX_RESULTS,
                                options=options,
                            ),
                            memory_token_budget=coding_budget
                            if coding_budget is not None
                            else 8192,
                            include_recent_native=False,
                            include_pinned=False,
                            include_summaries=False,
                            options=options,
                        )
                    )
                    if context.memories:
                        certificate = context.fast_dependencies
                        if (
                            certificate is None
                            or not (
                                await self.memory.lookup_related(
                                    RelatedRequest(
                                        intent="validate",
                                        dependencies=certificate,
                                        deadline_at=deadline,
                                    )
                                )
                            ).valid
                        ):
                            raise MemoryToolError("Ember recall changed during lookup; try again.")
            except (TimeoutError, MemoryError):
                raise MemoryToolError("Ember recall unavailable; try again later.") from None

        coding = coding_budget is not None
        results = _coding_rank(context.memories) if coding else context.memories
        hits, omitted = _project_hits(results, None if coding else MAX_RESULT_BYTES, coding=coding)
        return RecallResult(
            memories=hits, coverage=context.coverage.status, omitted_for_size=omitted
        )

    async def deep_recall(self, query: RecallQuery, *, coding: bool = False) -> DeepMCPResult:
        """Investigate related memories with optional dates; no source excerpts or writes."""
        if self.admission.locked():
            raise MemoryToolError("Ember recall is busy; try again later.")
        async with self.admission:
            identity = uuid4()
            try:
                # ponytail: keep archive/excerpt scope closed; a separate capability would
                # need explicit operator opt-in before exposing source text.
                result = await self.deep.investigate(
                    DeepRecallRequest(
                        request_id=identity,
                        context_id=identity,
                        character_id=self.deep.character_id,
                        query=query,
                        reference_at=timestamp_from_datetime(datetime.now(UTC)),
                        timezone=self.timezone,
                        budget=DeepRecallBudget(archive_chunks=0, excerpts=0),
                    ),
                    retain_for_inspection=False,
                )
                if result.memories:
                    certificate = result.dependencies
                    valid = False
                    if certificate is not None:
                        async with asyncio.timeout(DEEP_VALIDATION_SECONDS):
                            checked = await self.memory.lookup_related(
                                RelatedRequest(
                                    intent="validate",
                                    dependencies=certificate,
                                    row_budget=512,
                                    call_budget=32,
                                    deadline_at=timestamp_from_datetime(
                                        datetime.now(UTC)
                                        + timedelta(seconds=DEEP_VALIDATION_SECONDS)
                                    ),
                                )
                            )
                        valid = checked.valid
                    if not valid:
                        raise MemoryToolError("Ember deep recall changed during lookup; try again.")
            except (TimeoutError, MemoryError):
                raise MemoryToolError("Ember deep recall unavailable; try again later.") from None

        results = _coding_rank(result.memories) if coding else result.memories
        hits, omitted = _project_hits(
            results, None if coding else DEEP_MAX_CLAIM_BYTES, coding=coding
        )
        visible = {(hit.memory_id, hit.revision) for hit in hits}
        associations = tuple(
            edge
            for edge in result.associations
            if (edge.anchor.memory_id, edge.anchor.revision) in visible
            and (edge.target.memory_id, edge.target.revision) in visible
        )
        outcome, reasons = result.outcome, result.reasons
        if omitted or len(associations) > DEEP_MAX_ASSOCIATIONS:
            if outcome == "complete_within_budget":
                outcome = "partial"
            reasons = (*reasons, "mcp_response_budget")
        return DeepMCPResult(
            outcome=outcome,
            memories=hits,
            associations=associations[:DEEP_MAX_ASSOCIATIONS],
            scope=result.scope,
            reasons=reasons,
            omitted_for_size=omitted,
        )

    async def save_memory(self, note: NoteText) -> NoteResult:
        """Save one short durable note through the configured delegated writer."""
        writer = self.write_note
        if writer is None:
            raise MemoryToolError("Ember memory writes are disabled.")
        if not 1 <= len(note.strip()) <= 512 or not note.isprintable():
            raise MemoryToolError("Expected one printable, single-line note.")
        if self.admission.locked():
            raise MemoryToolError("Ember memory is busy; try again later.")
        async with self.admission:
            try:
                receipt = await writer(note)
            except AccessDeniedError:
                raise MemoryToolError("Ember did not permit this note to be saved.") from None
            except MemoryError:
                raise MemoryToolError(
                    "Ember could not confirm the save. Retry with the exact same note."
                ) from None
        saved = receipt.affected[0]
        return NoteResult(
            library=self.library,
            outcome=receipt.outcome,
            memory_id=saved.memory_id,
            revision=saved.revision,
        )
