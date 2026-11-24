"""Engine-internal retrieval behavior; executes on owned SQLite workers."""

import sqlite3
from time import monotonic
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

from . import identity_recall, profile, related
from ._state import CharacterState
from .archive import SourceHeaderResolver
from .database import (
    check_deadline,
    load_model,
    row_int,
    row_text,
)
from .errors import CorruptStoreError, UnsupportedQueryError
from .selection import origin_scope, source_scope
from .source_access import memory_source_scope
from .word_recall import EXTRACTED_TEXT_SQL, register_word_match, value_word_hits


def coverage(state: CharacterState, *reasons: str) -> RetrievalCoverage:
    """Report canonical fallback limits without claiming derived-index completeness."""
    return RetrievalCoverage(
        status="partial",
        index_status="degraded",
        reason_codes=("index_missing_or_unavailable", "canonical_fallback", *reasons),
    )


def memory_scope(
    state: CharacterState,
    query: MemoryQuery,
    *,
    include_text: bool = False,
    recent_first: bool = False,
    metadata_only: bool = False,
    index_lookup: bool = False,
    identity_vocabulary: bool = False,
    current_state: bool = False,
    narrowing: tuple[str, tuple[str | int, ...]] = ("", ()),
) -> tuple[str, tuple[str | int, ...]]:
    """Build canonical eligibility used before both indexed and fallback candidate limits."""
    conditions = [
        "current.lifecycle NOT IN ('disabled','forgotten')"
        if query.temporal.recorded_before
        else "r.lifecycle NOT IN ('disabled','forgotten')",
        (
            "(NOT EXISTS(SELECT 1 FROM suppression_targets) OR ("
            "NOT EXISTS(SELECT 1 FROM suppression_targets WHERE target_kind='memory' "
            "AND target_key=r.memory_id) AND NOT EXISTS(SELECT 1 FROM memory_dependencies d "
            "JOIN suppression_targets s ON d.target_kind=s.target_kind "
            "AND d.target_key=s.target_key "
            "WHERE d.memory_id=r.memory_id AND d.revision=r.revision)))"
        ),
    ]
    source_condition, source_values = memory_source_scope(
        state.character_id,
        timestamp_from_datetime(state.database.clock.now()),
        "r.memory_id",
        "r.revision",
    )
    conditions.append("(p.foreign_evidence=0 OR " + source_condition + ")")
    selection_sql, selection_values = origin_scope(state.character_id, "p.origin_key")
    conditions.append(selection_sql)
    parameters: list[str | int] = [*source_values, *selection_values]
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
        if (
            any((query.temporal.valid_at, query.temporal.valid_from, query.temporal.valid_until))
            and "active" in query.lifecycles
        ):
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
    elif before is None and not any(
        (
            query.temporal.valid_from,
            query.temporal.valid_until,
            query.temporal.observed_from,
            query.temporal.observed_until,
        )
    ):
        now = timestamp_to_unix_microseconds(timestamp_from_datetime(state.database.clock.now()))
        ended_episode = "" if current_state else "r.kind='episode' OR "
        conditions.append(
            "(r.valid_from_us IS NULL OR r.valid_from_us<=?) AND ("
            + ended_episode
            + "r.valid_until_us IS NULL OR ?<r.valid_until_us)"
        )
        parameters.extend((now, now))
    if include_text and query.text:
        conditions.append(
            "(ember_word_hits(" + EXTRACTED_TEXT_SQL + ")>0 OR "
            "ember_word_hits(replace(replace(p.predicate,'.',' '),'_',' '))>0)"
        )
    for column, lower, upper in (
        ("p.observed_at", query.temporal.observed_from, query.temporal.observed_until),
    ):
        if lower is not None:
            conditions.append(column + " IS NOT NULL AND " + column + ">=?")
            parameters.append(lower)
        if upper is not None:
            conditions.append(column + " IS NOT NULL AND " + column + "<?")
            parameters.append(upper)
    if query.temporal.valid_from is not None or query.temporal.valid_until is not None:
        conditions.append("r.valid_from_us IS NOT NULL")
        conditions.append("(r.valid_until_us IS NOT NULL OR p.temporal_basis='explicit_open_end')")
        if query.temporal.valid_from is not None:
            conditions.append("(r.valid_until_us IS NULL OR r.valid_until_us>?)")
            parameters.append(timestamp_to_unix_microseconds(query.temporal.valid_from))
        if query.temporal.valid_until is not None:
            conditions.append("r.valid_from_us<?")
            parameters.append(timestamp_to_unix_microseconds(query.temporal.valid_until))
    if query.predicates:
        conditions.append("p.predicate IN (" + ",".join("?" for _ in query.predicates) + ")")
        parameters.extend(query.predicates)
    if query.entity_ids:
        values = tuple(str(item) for item in query.entity_ids)
        placeholders = ",".join("?" for _ in values)
        conditions.append(
            "EXISTS(SELECT 1 FROM memory_dependencies d WHERE d.memory_id=r.memory_id "
            "AND d.revision=r.revision AND d.target_kind='entity' AND d.target_key IN ("
            + placeholders
            + "))"
        )
        parameters.extend(values)
    restriction, restriction_values = narrowing
    parameters.extend(restriction_values)
    if not identity_vocabulary:
        parameters.append(query.limit)
    order = (
        "r.recorded_at_us DESC,r.memory_id"
        if recent_first
        else ("r.importance DESC,r.recorded_at_us DESC,r.memory_id")
    )
    if include_text and query.text and not recent_first:
        order = "ember_word_hits(" + EXTRACTED_TEXT_SQL + ") DESC," + order
    projection = (
        "r.memory_id,r.revision,p.subject_id"
        if identity_vocabulary
        else "r.memory_id,r.revision"
        if metadata_only
        else "r.*"
    )
    ordered_recent = recent_first and before is None and query.lifecycles == ("active",)
    lookup = (
        " INDEXED BY memory_recent_order"
        if ordered_recent
        else " INDEXED BY memory_retrieval_identity"
        if index_lookup
        else ""
    )
    join = " CROSS JOIN " if ordered_recent else " JOIN "
    current = (
        " JOIN memory_revisions current ON current.memory_id=m.memory_id "
        "AND current.revision=m.current_revision"
        if before
        else ""
    )
    sql = (
        "SELECT "
        + projection
        + " FROM memory_revisions r"
        + lookup
        + join
        + "memories m INDEXED BY memory_pointer ON m.memory_id=r.memory_id"
        + join
        + "claim_projection p ON p.memory_id=r.memory_id AND p.revision=r.revision"
        + current
        + " WHERE "
        + " AND ".join(conditions)
        + restriction
        + ("" if identity_vocabulary else " ORDER BY " + order + " LIMIT ?")
    )
    return sql, tuple(parameters)


def _identity_search(
    state: CharacterState, connection: sqlite3.Connection, query: MemoryQuery, epoch: int
) -> SearchResponse:
    deadline = monotonic() + 0.15
    current = identity_recall.CURRENT_BELIEF in query.options.extensions
    extension = identity_recall.CURRENT_BELIEF if current else identity_recall.IDENTITY_RECALL
    origin, origins = identity_recall.origin_scope(query, extension)
    if current:
        query = identity_recall.current_query(query)
    predicate, names, truncated = identity_recall.exact(query.text, current=current)
    reasons = ["identity_query_word_limit"] if truncated else []
    selected: list[Memory] = []
    method = "exact_entity_identity_v1"
    sql, values = memory_scope(
        state,
        query,
        metadata_only=True,
        current_state=current,
        narrowing=(predicate + origin, (*names, *origins)),
    )
    if state.read_budget is None:
        connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
    try:
        rows = connection.execute(sql, values).fetchall()
        if current and not rows and names and not truncated and origins != ("[]",):
            eligible_sql, eligible_values = memory_scope(
                state,
                query,
                identity_vocabulary=True,
                current_state=True,
                narrowing=(origin, origins),
            )
            ids, reason = identity_recall.fuzzy_ids(
                connection, query.text, eligible_sql, eligible_values, deadline
            )
            if reason:
                reasons.append(reason)
            if ids:
                predicate, names = identity_recall.resolved(ids)
                sql, values = memory_scope(
                    state,
                    query,
                    metadata_only=True,
                    current_state=True,
                    narrowing=(predicate + origin, (*names, *origins)),
                )
                rows = connection.execute(sql, values).fetchall()
                method = "fuzzy_entity_identity_v1"
        for row in rows:
            if monotonic() >= deadline:
                break
            selected.append(
                state.read_memory(
                    connection,
                    UUID(row_text(row, "memory_id")),
                    row_int(row, "revision"),
                    query.options,
                )
            )
        if selected and monotonic() < deadline:
            selected, incomplete = _qualify_identity(
                state, connection, query, selected, current=current, origin_scope=(origin, origins)
            )
            if incomplete:
                reasons.append("source_unavailable_or_changed")
    except sqlite3.OperationalError as error:
        if state.read_budget is not None or error.sqlite_errorcode != sqlite3.SQLITE_INTERRUPT:
            raise
        reasons.append("identity_soft_deadline_miss")
    finally:
        if state.read_budget is None:
            connection.set_progress_handler(None, 0)
    if monotonic() >= deadline and "identity_soft_deadline_miss" not in reasons:
        reasons.append("identity_soft_deadline_miss")
    if "identity_soft_deadline_miss" in reasons:
        selected.clear()
    check_deadline(state.database.clock, query.options.deadline_at)
    state.check_policy(connection, epoch)
    strict_time = (query.temporal.valid_at, query.temporal.valid_from, query.temporal.valid_until)
    return SearchResponse(
        results=tuple(
            MemoryResult(
                memory=memory,
                score=memory.content.importance,
                ranking=(
                    RankingExplanation(
                        method=method,
                        score=memory.content.importance,
                        reasons=("ENTITY_MATCH",) if query.text else ("STRUCTURED_FACT_MATCH",),
                    ),
                ),
            )
            for memory in selected
        ),
        coverage=RetrievalCoverage(
            status="partial" if reasons else "complete",
            index_status="unavailable",  # canonical path does not inspect derived indexes
            reason_codes=(*reasons, *(("unknown_validity_excluded",) if any(strict_time) else ())),
        ),
    )


def _qualify_identity(
    state: CharacterState,
    connection: sqlite3.Connection,
    query: MemoryQuery,
    selected: list[Memory],
    *,
    current: bool,
    origin_scope: tuple[str, tuple[str, ...]],
) -> tuple[list[Memory], bool]:
    # Accepted revisions already prove exact spans. Check original availability, then reread
    # eligibility at the fresh clock boundary (a grant can expire without a policy write).
    references = {
        item.evidence.source.identity_key(): item.evidence.source
        for memory in selected
        for item in memory.content.evidence
    }
    resolver = state.resolver
    if not isinstance(resolver, SourceHeaderResolver):
        return [], True
    checked = tuple(references.values())[:256]
    unavailable, _ = resolver.unavailable_sources(
        checked,
        local_connection=connection,
        local_store_id=state.character_id,
        call_budget=8,
    )
    available = {ref.identity_key() for ref in checked} - {
        ref.identity_key() for ref in unavailable
    }
    keys = tuple(key for memory in selected for key in (str(memory.memory_id), memory.revision))
    pairs = ",".join("(?,?)" for _ in selected)
    origin, origins = origin_scope
    sql, values = memory_scope(
        state,
        query,
        metadata_only=True,
        current_state=current,
        narrowing=(" AND (r.memory_id,r.revision) IN (" + pairs + ")" + origin, (*keys, *origins)),
    )
    allowed = {
        (row_text(row, "memory_id"), row_int(row, "revision"))
        for row in connection.execute(sql, values).fetchall()
    }
    return [
        memory
        for memory in selected
        if (str(memory.memory_id), memory.revision) in allowed
        and all(
            item.evidence.source.identity_key() in available for item in memory.content.evidence
        )
    ], len(available) != len(references)


def search(
    state: CharacterState,
    connection: sqlite3.Connection,
    query: MemoryQuery,
    *,
    recent_first: bool = False,
    deep: bool = False,
) -> SearchResponse:
    """Filter current policy and temporal visibility before bounded canonical ranking."""
    check_deadline(state.database.clock, query.options.deadline_at)
    if query.cursor is not None:
        raise UnsupportedQueryError()
    epoch = state.policy_epoch(connection)
    if query.options.extensions.keys() & {
        identity_recall.IDENTITY_RECALL,
        identity_recall.CURRENT_BELIEF,
    }:
        return _identity_search(state, connection, query, epoch)
    terms = register_word_match(connection, query.text)
    if (
        state.retriever is not None
        and query.text
        and query.mode != "structured"
        and not recent_first
    ):
        indexed = state.retriever.memory(state, connection, query, deep=deep)
        if indexed is not None:
            return indexed
    sql, parameters = memory_scope(
        state,
        query,
        include_text=query.mode != "structured",
        recent_first=recent_first,
        metadata_only=True,
    )
    results: list[MemoryResult] = []
    for row in connection.execute(sql, tuple(parameters)).fetchall():
        memory = state.read_memory(
            connection, UUID(row_text(row, "memory_id")), row_int(row, "revision")
        )
        if memory.character_id != state.character_id:
            raise CorruptStoreError()
        score = memory.content.importance
        if terms and query.mode != "structured" and not recent_first:
            score += 2 * value_word_hits(memory, terms) / len(terms)
        results.append(
            MemoryResult(
                memory=memory,
                score=score,
                ranking=(
                    RankingExplanation(
                        method="canonical_fallback",
                        score=score,
                        reasons=("STRUCTURED_FACT_MATCH",)
                        if query.mode == "structured"
                        else ("DEGRADED_FALLBACK",),
                    ),
                ),
            )
        )
    check_deadline(state.database.clock, query.options.deadline_at)
    state.check_policy(connection, epoch)
    reasons = ("unknown_validity_excluded",) if query.temporal.valid_at is not None else ()
    return SearchResponse(results=tuple(results), coverage=coverage(state, *reasons))


def get_pinned(
    state: CharacterState, connection: sqlite3.Connection, request: PageRequest
) -> tuple[PinnedMemory, ...]:
    """Return current active and temporally eligible pins under current suppression."""
    check_deadline(state.database.clock, request.options.deadline_at)
    epoch = state.policy_epoch(connection)
    result: list[PinnedMemory] = []
    now = timestamp_to_unix_microseconds(timestamp_from_datetime(state.database.clock.now()))
    selection_sql, selection_values = origin_scope(state.character_id, "c.origin_key")
    rows = connection.execute(
        (
            "SELECT p.* FROM pins p CROSS JOIN memories m ON m.memory_id=p.memory_id AND "
            "m.current_revision=p.memory_revision CROSS JOIN memory_revisions r ON "
            "r.memory_id=p.memory_id AND r.revision=p.memory_revision "
            "CROSS JOIN claim_projection c ON c.memory_id=r.memory_id AND c.revision=r.revision "
            "WHERE " + selection_sql + " AND "
            "r.lifecycle='active' AND (r.valid_from_us IS NULL OR r.valid_from_us<=?) "
            "AND (r.valid_until_us IS NULL OR ?<r.valid_until_us) "
            "AND NOT EXISTS(SELECT 1 FROM memory_dependencies d JOIN"
            " suppression_targets s ON s.target_kind=d.target_kind AND "
            "s.target_key=d.target_key WHERE d.memory_id=p.memory_id AND "
            "d.revision=p.memory_revision) ORDER BY p.priority DESC,p.pinned_at DESC "
            "LIMIT ?"
        ),
        (*selection_values, now, now, request.limit),
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

    from .archive import SourceBranchResolver

    if isinstance(state.resolver, SourceBranchResolver):
        return state.resolver.branch_status(record)
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
    state: CharacterState,
    connection: sqlite3.Connection,
    query: ArchiveQuery,
    *,
    deep: bool = False,
) -> ArchiveSearchResponse:
    """Scan a bounded explicit snapshot scope with final current-policy validation."""
    from contextlib import closing

    from ember_memory_abi.archive import Document, Message

    check_deadline(state.database.clock, query.options.deadline_at)
    if query.cursor is not None:
        raise UnsupportedQueryError()
    # Refuse the retired experiment rather than silently returning a different result set.
    if "ember.archive_ranking" in query.options.extensions:
        raise UnsupportedQueryError()
    epoch = state.policy_epoch(connection)
    if state.retriever is not None and query.text:
        indexed = state.retriever.archive(state, connection, query, deep=deep)
        check_deadline(state.database.clock, query.options.deadline_at)
        if indexed is not None:
            return indexed
    results: list[ArchiveResult] = []
    # A bounded metadata scan never materializes an entire native archive or seen-set.
    scan_limit = 4096
    limited = False
    selection_sql, selection_values = source_scope(state.character_id, "source_store_id")
    predicates: list[str] = [selection_sql]
    parameters: list[str | int] = [
        str(state.character_id),
        timestamp_from_datetime(state.database.clock.now()),
    ]
    parameters.extend(selection_values)
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
            if query.conversation_ids and (
                not isinstance(record, Message)
                or record.conversation.record_id not in query.conversation_ids
            ):
                continue
            if query.source_from or query.source_until:
                source_at = (
                    record.created_time.value if isinstance(record, (Message, Document)) else None
                )
                if (
                    source_at is None
                    or (query.source_from and source_at < query.source_from)
                    or (query.source_until and source_at >= query.source_until)
                ):
                    continue
            if query.roles and (
                (
                    record.actor.role
                    if isinstance(record, Message)
                    else "user"
                    if isinstance(record, Document)
                    else ""
                )
                not in query.roles
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
    delivered = tuple(
        item
        for item in results
        if not isinstance(item.record, ArchiveChunk)
        and state.source_permitted(connection, item.record.source, "archive_search")
    )
    state.check_policy(connection, epoch)
    check_deadline(state.database.clock, query.options.deadline_at)
    return ArchiveSearchResponse(
        results=delivered,
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
        get_pinned(state, connection, PageRequest(limit=32, options=request.options))
        if request.include_pinned
        else ()
    )
    selection, _ = profile.select(state, connection) if request.include_profile else (None, ())
    manual_ids = {pin.memory_id for pin in pinned}
    if selection:
        pinned = (*pinned, *(pin for pin in selection.automatic if pin.memory_id not in manual_ids))
    response = (
        search(state, connection, request.memory_query)
        if request.include_memory_search
        else SearchResponse(results=(), coverage=coverage(state))
    )
    context_coverage = response.coverage
    current_belief_ids: frozenset[CanonicalId] = (
        frozenset(result.memory.memory_id for result in response.results)
        if identity_recall.CURRENT_BELIEF in request.memory_query.options.extensions
        else frozenset()
    )
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
        if (
            result.memory.memory_id not in used
            and ordinary < request.memory_query.limit
            and len(candidates) < 100
        ):
            candidates.append(result)
            used.add(result.memory.memory_id)
            ordinary += 1
    memories: list[MemoryResult] = []
    kept_pins: list[PinnedMemory] = []
    remaining = request.memory_token_budget
    for result in candidates:
        matching = next((pin for pin in pinned if pin.memory_id == result.memory.memory_id), None)
        # Search already checked the historical selector and current authorization.
        # Pins are deliberately selected under today's state instead.
        if matching and not profile.eligible(state, connection, result.memory):
            continue
        cost = state.tokens.count(result.model_dump_json()) + (
            state.tokens.count(matching.model_dump_json()) if matching else 0
        )
        # Pins have their own composer budget. Compact ordinary rows preserve six
        # slots instead of charging immutable evidence JSON to the model prompt.
        cost = state.tokens.count(result.memory.content.payload.model_dump_json()) + 96
        if matching or cost <= remaining:
            memories.append(result)
            if not matching:
                remaining -= cost
            if matching:
                kept_pins.append(matching)
    archive: list[ArchiveResult] = []
    # Recent native observations use the existing archive-result/evidence contract.
    # An explicit archive budget permits them; imported libraries are never added.
    if (
        request.archive_query is None
        and request.include_recent_native
        and request.archive_token_budget
    ):
        from ember_memory_abi.archive import Document, Message

        native_rows = connection.execute(
            "SELECT s.* FROM source_records s WHERE "
            "(SELECT local_enabled FROM recall_policy)=1 "
            "AND record_kind IN ('message','document') "
            "AND revision=(SELECT MAX(n.revision) FROM source_records n WHERE "
            "n.record_kind=s.record_kind AND n.record_id=s.record_id) "
            "ORDER BY recorded_at_us DESC LIMIT 8",
        ).fetchall()
        remaining_native = request.archive_token_budget
        for row in native_rows:
            record = load_model(
                row, Message if row_text(row, "record_kind") == "message" else Document
            )
            if not state.source_permitted(connection, record.source, "archive_search") or any(
                state.source_suppressed(
                    connection, record.source.model_copy(update={"part_id": part.part_id})
                )
                for part in record.parts
            ):
                continue
            result = ArchiveResult(
                record=record,
                score=1.0,
                branch_status=branch_status(state, record),
                ranking=(
                    RankingExplanation(
                        method="recent_native_v1",
                        score=1.0,
                        reasons=("TEMPORAL_MATCH", "EXACT_SOURCE_MATCH"),
                    ),
                ),
            )
            cost = state.tokens.count(result.model_dump_json())
            if cost <= remaining_native:
                archive.append(result)
                remaining_native -= cost
    remaining_archive = request.archive_token_budget
    if request.archive_query is not None and remaining_archive:
        searched = search_archive(state, connection, request.archive_query)
        context_coverage = RetrievalCoverage(
            status=(
                "complete"
                if response.coverage.status == searched.coverage.status == "complete"
                else "partial"
            ),
            index_status=(
                "available"
                if response.coverage.index_status == searched.coverage.index_status == "available"
                else "degraded"
            ),
            reason_codes=tuple(
                dict.fromkeys((*response.coverage.reason_codes, *searched.coverage.reason_codes))
            ),
            warnings=(*response.coverage.warnings, *searched.coverage.warnings),
            extensions={
                "ember.memory_coverage": response.coverage.model_dump(mode="json"),
                "ember.archive_coverage": searched.coverage.model_dump(mode="json"),
            },
        )
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
    from pydantic import ValidationError

    context_certificate = None
    while memories:
        try:
            context_certificate = related.certificate(
                state,
                connection,
                tuple(r.memory for r in memories),
                profile_epoch=selection.selection_epoch if selection else None,
                temporal=request.memory_query.temporal if request.include_memory_search else None,
                current_ids=frozenset(p.memory_id for p in kept_pins),
                current_belief_ids=current_belief_ids,
            )
            break
        except ValidationError:
            removed = memories.pop()
            kept_pins = [p for p in kept_pins if p.memory_id != removed.memory.memory_id]
    if context_certificate is None:
        context_certificate = related.certificate(
            state, connection, (), profile_epoch=selection.selection_epoch if selection else None
        )
    from .archive import SourceHeaderResolver

    if isinstance(state.resolver, SourceHeaderResolver) and context_certificate.sources:
        missing, _ = state.resolver.unavailable_sources(
            context_certificate.sources,
            local_connection=connection,
            local_store_id=state.character_id,
            call_budget=8,
        )
        if missing:
            context_coverage = context_coverage.model_copy(
                update={
                    "status": "partial",
                    "reason_codes": (
                        *context_coverage.reason_codes,
                        "source_unavailable_or_changed",
                    ),
                }
            )
            placeholders = ",".join("(?,?,?,?,?)" for _ in missing)
            parameters = tuple(
                x
                for ref in missing
                for x in (
                    str(ref.source_store_id),
                    ref.record_kind,
                    str(ref.record_id),
                    ref.record_revision,
                    str(ref.part_id) if ref.part_id else "",
                )
            )
            denied = {
                row_text(row, "memory_id")
                for row in connection.execute(
                    "SELECT DISTINCT memory_id FROM memory_evidence WHERE "
                    "(source_store_id,record_kind,record_id,record_revision,part_id)"
                    " IN (" + placeholders + ") LIMIT 100",
                    parameters,
                )
            }
            memories = [r for r in memories if str(r.memory.memory_id) not in denied]
            kept_pins = [p for p in kept_pins if str(p.memory_id) not in denied]
            if selection:
                from ember_memory_abi.related import ProfileOmission

                fields = {
                    r.memory.memory_id: getattr(r.memory.content.payload, "predicate", "unknown")
                    for r in candidates
                }
                excluded = [p for p in selection.automatic if str(p.memory_id) in denied]
                selection = selection.model_copy(
                    update={
                        "automatic": tuple(
                            p for p in selection.automatic if str(p.memory_id) not in denied
                        ),
                        "omitted": (
                            *selection.omitted,
                            *(
                                ProfileOmission(
                                    field=fields[p.memory_id],
                                    reason="source_unavailable_or_changed",
                                )
                                for p in excluded
                            ),
                        ),
                    }
                )
            context_certificate = related.certificate(
                state,
                connection,
                tuple(r.memory for r in memories),
                profile_epoch=selection.selection_epoch if selection else None,
                temporal=request.memory_query.temporal if request.include_memory_search else None,
                current_ids=frozenset(p.memory_id for p in kept_pins),
                current_belief_ids=current_belief_ids,
            )
    if selection and selection.valid_until:
        boundary = min(b for b in (selection.valid_until, context_certificate.valid_until) if b)
        context_certificate = context_certificate.model_copy(update={"valid_until": boundary})
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
        coverage=context_coverage,
        profile=selection,
        fast_dependencies=context_certificate,
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
