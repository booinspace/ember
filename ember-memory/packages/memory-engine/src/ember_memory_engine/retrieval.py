"""Engine-internal retrieval behavior; executes on owned SQLite workers."""

import sqlite3
from uuid import UUID

from ember_memory_abi.archive import SourceRecord
from ember_memory_abi.common import (
    CanonicalId,
    timestamp_from_datetime,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.errors import (
    SourceUnavailableError,
)
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ArchiveResult,
    ArchiveSearchResponse,
    ContextRequest,
    MemoryQuery,
    MemoryResult,
    PageRequest,
    RankingExplanation,
    RetrievalContext,
    RetrievalCoverage,
    SearchResponse,
)
from ember_memory_abi.memory import (
    ArchiveChunk,
    Memory,
    PinnedMemory,
)

from ._state import CharacterState
from .database import (
    check_deadline,
    load_model,
    row_int,
    row_text,
)
from .errors import CorruptStoreError, UnsupportedQueryError


def coverage(state: CharacterState, *reasons: str) -> RetrievalCoverage:
    """Report canonical fallback limits without claiming derived-index completeness."""
    return RetrievalCoverage(
        status="partial",
        index_status="degraded",
        reason_codes=("derived_indexes_not_implemented", "canonical_fallback", *reasons),
    )


def search(
    state: CharacterState,
    connection: sqlite3.Connection,
    query: MemoryQuery,
    *,
    recent_first: bool = False,
) -> SearchResponse:
    """Filter current policy and temporal visibility before bounded canonical ranking."""
    check_deadline(state.database.clock, query.options.deadline_at)
    if query.cursor is not None:
        raise UnsupportedQueryError()
    epoch = state.policy_epoch(connection)
    conditions = [
        "current.lifecycle NOT IN ('disabled','forgotten')",
        (
            "NOT EXISTS(SELECT 1 FROM suppression_targets WHERE target_kind='memory' AND "
            "target_key=r.memory_id)"
        ),
        (
            "NOT EXISTS(SELECT 1 FROM memory_dependencies d JOIN suppression_targets s ON"
            " d.target_kind=s.target_kind AND d.target_key=s.target_key WHERE "
            "d.memory_id=r.memory_id AND d.revision=r.revision)"
        ),
    ]
    parameters: list[str | int] = []
    before = query.temporal.recorded_before
    if before is not None:
        conditions.append(
            "r.revision=(SELECT MAX(h.revision) FROM memory_revisions h WHERE "
            "h.memory_id=r.memory_id AND h.recorded_at_us < ?)"
        )
        parameters.append(timestamp_to_unix_microseconds(before))
    else:
        conditions.append("r.revision=m.current_revision")
    for column, values in (("r.kind", query.kinds), ("r.modality", query.modalities)):
        if values:
            conditions.append(column + " IN (" + ",".join("?" for _ in values) + ")")
            parameters.extend(values)
    if query.lifecycles:
        if query.temporal.valid_at is not None and "active" in query.lifecycles:
            conditions.append(
                "r.lifecycle IN (" + ",".join("?" for _ in (*query.lifecycles, "superseded")) + ")"
            )
            parameters.extend((*query.lifecycles, "superseded"))
        else:
            conditions.append("r.lifecycle IN (" + ",".join("?" for _ in query.lifecycles) + ")")
            parameters.extend(query.lifecycles)
    valid_at = query.temporal.valid_at
    if valid_at is not None:
        at = timestamp_to_unix_microseconds(valid_at)
        conditions.append(
            "r.valid_from_us IS NOT NULL AND r.valid_from_us <= ? AND "
            "((r.valid_until_us IS NOT NULL AND ? < r.valid_until_us) OR "
            "(r.valid_until_us IS NULL AND "
            "json_extract(r.payload_json,'$.temporal.basis')='explicit_open_end'))"
        )
        parameters.extend((at, at))
    elif before is None:
        now = timestamp_to_unix_microseconds(timestamp_from_datetime(state.database.clock.now()))
        conditions.append(
            "(r.valid_from_us IS NULL OR r.valid_from_us<=?) AND (r.valid_until_us IS"
            " NULL OR ?<r.valid_until_us)"
        )
        parameters.extend((now, now))
    if query.text:
        conditions.append(
            "instr(lower(json_extract(r.payload_json,'$.content.payload')),lower(?))>0"
        )
        parameters.append(query.text)
    parameters.append(query.limit)
    order = (
        "r.recorded_at_us DESC,r.memory_id"
        if recent_first
        else ("r.importance DESC,r.recorded_at_us DESC,r.memory_id")
    )
    sql = (
        (
            "SELECT r.* FROM memory_revisions r JOIN memories m ON "
            "m.memory_id=r.memory_id JOIN memory_revisions current ON "
            "current.memory_id=m.memory_id AND current.revision=m.current_revision WHERE "
        )
        + " AND ".join(conditions)
        + " ORDER BY "
        + order
        + " LIMIT ?"
    )
    results: list[MemoryResult] = []
    for row in connection.execute(sql, tuple(parameters)).fetchall():
        memory = load_model(row, Memory)
        if memory.character_id != state.character_id:
            raise CorruptStoreError()
        results.append(
            MemoryResult(
                memory=memory,
                score=memory.content.importance,
                ranking=(
                    RankingExplanation(
                        method="canonical_fallback",
                        score=memory.content.importance,
                        reasons=("DEGRADED_FALLBACK",),
                    ),
                ),
            )
        )
    check_deadline(state.database.clock, query.options.deadline_at)
    state.check_policy(connection, epoch)
    reasons = ("unknown_validity_excluded",) if valid_at is not None else ()
    return SearchResponse(results=tuple(results), coverage=coverage(state, *reasons))


def get_pinned(
    state: CharacterState, connection: sqlite3.Connection, request: PageRequest
) -> tuple[PinnedMemory, ...]:
    """Return current active and temporally eligible pins under current suppression."""
    check_deadline(state.database.clock, request.options.deadline_at)
    epoch = state.policy_epoch(connection)
    result: list[PinnedMemory] = []
    now = timestamp_to_unix_microseconds(timestamp_from_datetime(state.database.clock.now()))
    rows = connection.execute(
        (
            "SELECT p.* FROM pins p JOIN memories m ON m.memory_id=p.memory_id AND "
            "m.current_revision=p.memory_revision JOIN memory_revisions r ON "
            "r.memory_id=p.memory_id AND r.revision=p.memory_revision WHERE "
            "r.lifecycle='active' AND (r.valid_from_us IS NULL OR r.valid_from_us<=?) "
            "AND (r.valid_until_us IS NULL OR ?<r.valid_until_us) "
            "AND NOT EXISTS(SELECT 1 FROM memory_dependencies d JOIN"
            " suppression_targets s ON s.target_kind=d.target_kind AND "
            "s.target_key=d.target_key WHERE d.memory_id=p.memory_id AND "
            "d.revision=p.memory_revision) ORDER BY p.priority DESC,p.pinned_at DESC "
            "LIMIT ?"
        ),
        (now, now, request.limit),
    ).fetchall()
    for row in rows:
        identity = UUID(row_text(row, "memory_id"))
        if state.memory_suppressed(connection, identity, row_int(row, "memory_revision")):
            continue
        result.append(
            PinnedMemory(
                character_id=state.character_id,
                memory_id=identity,
                memory_revision=row_int(row, "memory_revision"),
                priority=row_int(row, "priority"),
                pinned_at=row_text(row, "pinned_at"),
            )
        )
    state.check_policy(connection, epoch)
    return tuple(result)


def branch_status(state: CharacterState, record: SourceRecord) -> str:
    """Preserve original graph selection or ambiguity; never repair a source branch."""
    from ember_memory_abi.archive import Conversation, GraphNode, Message

    if isinstance(record, Conversation):
        return record.selection.status if record.selection else "unavailable"
    if not isinstance(record, (Message, GraphNode)):
        return "not_applicable"
    try:
        conversation = state.resolver.resolve(record.conversation)
    except SourceUnavailableError:
        return "unavailable"
    if (
        not isinstance(conversation, Conversation)
        or conversation.selection is None
        or conversation.selection.status != "resolved"
    ):
        return "ambiguous"
    path = conversation.selection.path
    if isinstance(record, GraphNode):
        return (
            "selected"
            if any(reference.identity_key() == record.source.identity_key() for reference in path)
            else "alternative"
        )
    for reference in path:
        try:
            node = state.resolver.resolve(reference)
        except SourceUnavailableError:
            return "ambiguous"
        if (
            isinstance(node, GraphNode)
            and node.message is not None
            and node.message.identity_key() == record.source.identity_key()
        ):
            return "selected"
    return "alternative"


def search_archive(
    state: CharacterState, connection: sqlite3.Connection, query: ArchiveQuery
) -> ArchiveSearchResponse:
    """Scan a bounded explicit snapshot scope with final current-policy validation."""
    from contextlib import closing

    from ember_memory_abi.archive import Document, Message

    check_deadline(state.database.clock, query.options.deadline_at)
    if query.cursor is not None:
        raise UnsupportedQueryError()
    epoch = state.policy_epoch(connection)
    results: list[ArchiveResult] = []
    # A bounded metadata scan never materializes an entire native archive or seen-set.
    scan_limit = 4096
    limited = False
    predicates: list[str] = []
    parameters: list[str | int] = [
        str(state.character_id),
        timestamp_from_datetime(state.database.clock.now()),
    ]
    for column, values in (
        ("source_store_id", tuple(str(item) for item in query.source_store_ids)),
        ("record_kind", query.record_kinds),
    ):
        if values:
            predicates.append(column + " IN (" + ",".join("?" for _ in values) + ")")
            parameters.extend(values)
    sql = (
        "SELECT * FROM (SELECT ? AS source_store_id,record_kind,record_id,"
        "MAX(revision) AS record_revision FROM source_records GROUP BY record_kind,record_id "
        "UNION SELECT source_store_id,record_kind,record_id,record_revision FROM "
        "source_grants WHERE capability='archive_search' AND part_id='' "
        "AND (expires_at IS NULL OR expires_at>?))"
    )
    if predicates:
        sql += " WHERE " + " AND ".join(predicates)
    sql += " ORDER BY source_store_id,record_kind,record_id,record_revision LIMIT ?"
    parameters.append(scan_limit + 1)
    with closing(connection.execute(sql, tuple(parameters))) as rows:
        for index, row in enumerate(rows):
            check_deadline(state.database.clock, query.options.deadline_at)
            if index == scan_limit:
                limited = True
                break
            reference = SourceRef(
                source_store_id=UUID(row_text(row, "source_store_id")),
                record_kind=row_text(row, "record_kind"),
                record_id=UUID(row_text(row, "record_id")),
                record_revision=row_int(row, "record_revision"),
            )
            if not state.source_permitted(connection, reference, "archive_search"):
                continue
            try:
                record = state.resolver.resolve(reference)
            except SourceUnavailableError:
                continue
            if isinstance(record, (Message, Document)) and any(
                state.source_suppressed(
                    connection,
                    SourceRef.model_validate({**reference.model_dump(), "part_id": part.part_id}),
                )
                for part in record.parts
            ):
                continue
            if query.roles and (
                not isinstance(record, Message) or record.actor.role not in query.roles
            ):
                continue
            if (
                query.temporal.recorded_before
                and record.recorded_at >= query.temporal.recorded_before
            ):
                continue
            if query.temporal.valid_at is not None:
                if (
                    not isinstance(record, (Message, Document))
                    or record.created_time.value is None
                    or record.created_time.value != query.temporal.valid_at
                ):
                    continue
            text = (
                "\n".join(
                    part.text
                    for part in record.parts
                    if part.status == "available" and part.text is not None
                )
                if isinstance(record, (Message, Document))
                else ""
            )
            if query.text and query.text.casefold() not in text.casefold():
                continue
            branch = branch_status(state, record)
            if not query.include_alternative_branches and branch not in {
                "selected",
                "resolved",
                "not_applicable",
            }:
                continue
            results.append(
                ArchiveResult(
                    record=record,
                    score=1.0,
                    branch_status=branch,
                    ranking=(
                        RankingExplanation(
                            method="canonical_literal_fallback",
                            score=1.0,
                            reasons=("EXACT_SOURCE_MATCH", "DEGRADED_FALLBACK"),
                        ),
                    ),
                )
            )
            if len(results) >= query.limit:
                break
    state.check_policy(connection, epoch)
    return ArchiveSearchResponse(
        results=tuple(
            item
            for item in results
            if not isinstance(item.record, ArchiveChunk)
            and state.source_permitted(connection, item.record.source, "archive_search")
        ),
        coverage=coverage(
            state,
            "canonical_archive_scan",
            "unavailable_sources_omitted",
            *(("canonical_scan_limit_reached",) if limited else ()),
        ),
    )


def retrieve_context(
    state: CharacterState, connection: sqlite3.Connection, request: ContextRequest
) -> RetrievalContext:
    """Pack pins, memories and archive records with all delivered source dependencies."""
    check_deadline(state.database.clock, request.options.deadline_at)
    epoch = state.policy_epoch(connection)
    pinned = (
        get_pinned(state, connection, PageRequest(limit=100, options=request.options))
        if request.include_pinned
        else ()
    )
    response = search(state, connection, request.memory_query)
    candidates: list[MemoryResult] = []
    used: set[CanonicalId] = set()
    for pin in pinned:
        memory = state.read_memory(connection, pin.memory_id, pin.memory_revision)
        candidates.append(
            MemoryResult(
                memory=memory,
                score=memory.content.importance,
                ranking=(
                    RankingExplanation(
                        method="pinned", score=memory.content.importance, reasons=("PINNED",)
                    ),
                ),
            )
        )
        used.add(memory.memory_id)
    ordinary = 0
    for result in response.results:
        if result.memory.memory_id not in used and ordinary < 6:
            candidates.append(result)
            used.add(result.memory.memory_id)
            ordinary += 1
    memories: list[MemoryResult] = []
    kept_pins: list[PinnedMemory] = []
    remaining = request.memory_token_budget
    for result in candidates:
        matching = next((pin for pin in pinned if pin.memory_id == result.memory.memory_id), None)
        cost = state.tokens.count(result.model_dump_json()) + (
            state.tokens.count(matching.model_dump_json()) if matching else 0
        )
        if cost <= remaining:
            memories.append(result)
            remaining -= cost
            if matching:
                kept_pins.append(matching)
    archive: list[ArchiveResult] = []
    remaining_archive = request.archive_token_budget
    if request.archive_query is not None and remaining_archive:
        searched = search_archive(state, connection, request.archive_query)
        for result in searched.results:
            cost = state.tokens.count(result.model_dump_json())
            if cost <= remaining_archive:
                archive.append(result)
                remaining_archive -= cost
    archive = [
        item
        for item in archive
        if (
            all(
                state.source_permitted(connection, span.evidence.source, "archive_search")
                for span in item.record.source_spans
            )
            if isinstance(item.record, ArchiveChunk)
            else state.source_permitted(connection, item.record.source, "archive_search")
        )
    ]
    dependency_map = {
        item.evidence.source.identity_key(): item.evidence.source
        for result in memories
        for item in result.memory.content.evidence
    }
    for result in archive:
        if isinstance(result.record, ArchiveChunk):
            for span in result.record.source_spans:
                reference = span.evidence.source
                dependency_map[reference.identity_key()] = reference
        else:
            dependency_map[result.record.source.identity_key()] = result.record.source
    dependencies = tuple(dependency_map.values())
    check_deadline(state.database.clock, request.options.deadline_at)
    state.check_policy(connection, epoch)
    return RetrievalContext(
        memories=tuple(memories),
        archive=tuple(archive),
        pinned=tuple(kept_pins),
        dependencies=dependencies,
        coverage=coverage(
            state, "provisional_context_counter", "session_summaries_not_implemented"
        ),
    )


def recent(
    state: CharacterState,
    connection: sqlite3.Connection,
    request: PageRequest,
) -> SearchResponse:
    """Apply canonical recency rather than search importance ordering."""
    return search(
        state,
        connection,
        MemoryQuery(limit=request.limit, options=request.options),
        recent_first=True,
    )
