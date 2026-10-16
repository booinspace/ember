"""Bounded canonical related reads; compact validation never expands or hydrates."""

import sqlite3
from datetime import timedelta
from uuid import UUID

from pydantic import ValidationError

from ember_memory_abi.common import (
    CanonicalId,
    timestamp_from_datetime,
    timestamp_from_unix_microseconds,
)
from ember_memory_abi.errors import AccessDeniedError, MemoryError
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.memory import Memory
from ember_memory_abi.related import (
    ContextDependencies,
    MemoryReference,
    MemoryRelationship,
    ReadManifest,
    RelatedRequest,
    RelatedResponse,
    TemporalSelection,
)
from ember_memory_abi.temporal import TemporalQuery

from ._state import CharacterState
from .archive import SourceHeaderResolver
from .database import check_deadline, load_model, row_int, row_text
from .selection import origin_scope, permitted, settings, source_selected
from .source_access import evidence_grant_window


def certificate(
    state: CharacterState,
    connection: sqlite3.Connection,
    memories: tuple[Memory, ...],
    *,
    profile_epoch: int | None = None,
    temporal: TemporalQuery | None = None,
    current_ids: frozenset[CanonicalId] = frozenset(),
) -> ContextDependencies:
    """Collect only selected memory/evidence and known time boundaries; no relationship joins."""
    sources = {
        item.evidence.source.identity_key(): item.evidence.source
        for memory in memories
        for item in memory.content.evidence
    }
    if memories:
        placeholders = ",".join("(?,?)" for _ in memories)
        parameters = tuple(x for m in memories for x in (str(m.memory_id), m.revision))
        for row in connection.execute(
            "SELECT DISTINCT "
            "source_store_id,record_kind,record_id,record_revision,part_id FROM "
            "memory_evidence "
            "WHERE (memory_id,revision) IN (" + placeholders + ") LIMIT 257",
            parameters,
        ):
            ref = SourceRef(
                source_store_id=UUID(row_text(row, "source_store_id")),
                record_kind=row_text(row, "record_kind"),
                record_id=UUID(row_text(row, "record_id")),
                record_revision=row_int(row, "record_revision"),
                part_id=UUID(row_text(row, "part_id")) if row["part_id"] else None,
            )
            sources[ref.identity_key()] = ref
    historical = temporal is not None and any(temporal.model_dump(exclude={"extensions"}).values())
    selected = tuple(
        MemoryReference(memory_id=m.memory_id, revision=m.revision)
        for m in memories
        if historical and m.memory_id not in current_ids
    )
    boundaries = [
        m.temporal.valid_until
        for m in memories
        if m.temporal.valid_until
        and m.content.kind != "episode"
        and (not historical or m.memory_id in current_ids)
    ]
    now = timestamp_from_datetime(state.database.clock.now())
    allowed, grant_until = evidence_grant_window(
        connection, state.character_id, tuple(sources.values()), now
    )
    if not allowed:
        boundaries.append(now)
    elif grant_until is not None:
        boundaries.append(timestamp_from_unix_microseconds(grant_until))
    return ContextDependencies(
        memories=tuple(
            MemoryReference(memory_id=m.memory_id, revision=m.revision) for m in memories
        ),
        selections=(TemporalSelection(temporal=temporal, memories=selected),)
        if selected and temporal is not None
        else (),
        sources=tuple(sources.values()),
        policy_epoch=state.policy_epoch(connection),
        selection_epoch=profile_epoch,
        valid_until=min(boundaries) if boundaries else None,
    )


def validate(
    state: CharacterState, connection: sqlite3.Connection, request: RelatedRequest
) -> RelatedResponse:
    """One batched header read, exact evidence closure, fresh grants and source availability."""
    dependencies = request.dependencies
    if dependencies is None:
        raise ValueError("Validation requires dependencies.")
    check_deadline(state.database.clock, request.deadline_at)
    reasons: list[str] = []
    now = timestamp_from_datetime(state.database.clock.now())
    if dependencies.policy_epoch != state.policy_epoch(connection):
        reasons.append("policy_changed")
    if dependencies.selection_epoch is not None and dependencies.selection_epoch != row_int(
        connection.execute("SELECT selection_epoch FROM profile_policy").fetchone(),
        "selection_epoch",
    ):
        reasons.append("selection_changed")
    if dependencies.valid_until is not None and now >= dependencies.valid_until:
        reasons.append("time_boundary")
    rows_count = 0
    required: set[tuple[object, ...]] = set()
    required_sources: list[SourceRef] = []
    boundaries = [dependencies.valid_until] if dependencies.valid_until else []
    if dependencies.memories:
        placeholders = ",".join("(?,?)" for _ in dependencies.memories)
        parameters = tuple(
            x for ref in dependencies.memories for x in (str(ref.memory_id), ref.revision)
        )
        historical_refs = {
            (str(ref.memory_id), ref.revision)
            for group in dependencies.selections
            for ref in group.memories
        }
        current_refs = tuple(
            ref
            for ref in dependencies.memories
            if (str(ref.memory_id), ref.revision) not in historical_refs
        )
        current_placeholders = ",".join("(?,?)" for _ in current_refs) or "(NULL,NULL)"
        current_parameters = tuple(
            value for ref in current_refs for value in (str(ref.memory_id), ref.revision)
        )
        selection_sql, selection_values = origin_scope(state.character_id, "p.origin_key")
        rows = connection.execute(
            "SELECT r.memory_id,r.revision,r.lifecycle,r.kind,r.valid_from_us,r.valid_until_us"
            " FROM memory_revisions r "
            "JOIN memories m ON m.memory_id=r.memory_id AND m.current_revision=r.revision "
            "JOIN claim_projection p ON p.memory_id=r.memory_id AND p.revision=r.revision "
            "WHERE (r.memory_id,r.revision) IN ("
            + current_placeholders
            + ") AND r.lifecycle='active' "
            "AND " + selection_sql + " "
            "AND NOT EXISTS(SELECT 1 FROM suppression_targets WHERE "
            "target_kind='memory' AND target_key=r.memory_id) "
            "AND NOT EXISTS(SELECT 1 FROM memory_dependencies d JOIN suppression_targets s "
            "ON s.target_kind=d.target_kind AND s.target_key=d.target_key WHERE "
            "d.memory_id=r.memory_id AND d.revision=r.revision)",
            (*current_parameters, *selection_values),
        ).fetchall()
        rows_count += len(rows)
        if len(rows) != len(set((r.memory_id, r.revision) for r in current_refs)):
            reasons.append("memory_changed_or_suppressed")
        from ember_memory_abi.common import timestamp_to_unix_microseconds

        at = timestamp_to_unix_microseconds(now)
        for row in rows:
            if (row["valid_from_us"] is not None and row_int(row, "valid_from_us") > at) or (
                row["kind"] != "episode"
                and row["valid_until_us"] is not None
                and at >= row_int(row, "valid_until_us")
            ):
                reasons.append("memory_outside_validity")
            if row["kind"] != "episode" and row["valid_until_us"] is not None:
                boundaries.append(timestamp_from_unix_microseconds(row_int(row, "valid_until_us")))
        # Reuse the actual eligibility compiler. Historical selectors never grant
        # permission or bypass current forgetting, revocation or source controls.
        from ember_memory_abi.interfaces import MemoryQuery

        from .retrieval import memory_scope

        for group in dependencies.selections:
            requested = tuple(
                value for ref in group.memories for value in (str(ref.memory_id), ref.revision)
            )
            wanted = ",".join("(?,?)" for _ in group.memories)
            sql, values = memory_scope(
                state, MemoryQuery(temporal=group.temporal, limit=100), metadata_only=True
            )
            marker = sql.index(" ORDER BY ")
            sql = sql[:marker] + " AND (r.memory_id,r.revision) IN (" + wanted + ")" + sql[marker:]
            found = connection.execute(sql, (*values[:-1], *requested, values[-1])).fetchall()
            rows_count += len(found)
            if len(found) != len(group.memories):
                reasons.append("historical_selection_changed_or_suppressed")
        for row in connection.execute(
            "SELECT e.* FROM memory_evidence e WHERE (e.memory_id,e.revision) IN ("
            + placeholders
            + ") LIMIT ?",
            (*parameters, request.row_budget + 1),
        ):
            rows_count += 1
            reference = SourceRef(
                source_store_id=UUID(row_text(row, "source_store_id")),
                record_kind=row_text(row, "record_kind"),
                record_id=UUID(row_text(row, "record_id")),
                record_revision=row_int(row, "record_revision"),
                part_id=UUID(row_text(row, "part_id")) if row["part_id"] else None,
            )
            required.add(reference.identity_key())
            required_sources.append(reference)
    if request.refresh_dependencies:
        # This is selected-result certification, not authority supplied by the caller.
        # Hard evidence comes from canonical metadata; explicit raw excerpts stay scoped.
        sources = tuple(
            {
                ref.identity_key(): ref
                for ref in (*required_sources, *dependencies.excerpt_sources)
            }.values()
        )
        allowed, expiry = evidence_grant_window(connection, state.character_id, sources, now)
        archive_allowed, archive_expiry = evidence_grant_window(
            connection,
            state.character_id,
            dependencies.archive_sources,
            now,
            capability="archive_search",
        )
        if not allowed or not archive_allowed:
            reasons.append("source_denied")
        boundaries.extend(
            timestamp_from_unix_microseconds(boundary)
            for boundary in (expiry, archive_expiry)
            if boundary is not None
        )
        try:
            dependencies = ContextDependencies.model_validate(
                {
                    **dependencies.model_dump(),
                    "sources": sources,
                    "valid_until": min(boundaries) if boundaries else None,
                }
            )
        except ValidationError:
            reasons.append("dependency_budget")
    supplied = {source.identity_key() for source in dependencies.sources}
    if not required <= supplied:
        reasons.append("evidence_dependency_missing")
    all_sources = (*dependencies.sources, *dependencies.archive_sources)
    raw_sources = (*dependencies.archive_sources, *dependencies.excerpt_sources)
    if raw_sources:
        policy = settings(connection)
        if any(
            not source_selected(policy, state.character_id, ref.source_store_id)
            for ref in raw_sources
        ):
            reasons.append("source_selection_changed")
    if any(
        not state.source_permitted(connection, ref, "archive_search")
        for ref in dependencies.archive_sources
    ):
        reasons.append("archive_source_denied")
    rows_count += len(all_sources)
    if rows_count > request.row_budget:
        reasons.append("row_budget")
    calls = 0
    if not reasons:
        if any(
            not state.source_permitted(connection, ref, "evidence_read")
            for ref in dependencies.sources
        ):
            reasons.append("source_denied")
        else:
            # Optional resolver method groups stores and reads only revision/part headers.
            resolver = state.resolver
            if isinstance(resolver, SourceHeaderResolver):
                try:
                    missing, calls = resolver.unavailable_sources(
                        all_sources,
                        local_connection=connection,
                        local_store_id=state.character_id,
                        call_budget=request.call_budget,
                    )
                except MemoryError:
                    missing = all_sources
                if missing:
                    reasons.append("source_unavailable")
            else:
                # Legacy resolver cannot certify without hydration; fail closed explicitly.
                reasons.append("source_headers_unsupported")
    check_deadline(state.database.clock, request.deadline_at)
    if dependencies.policy_epoch != state.policy_epoch(connection):
        reasons.append("policy_changed")
    if any(
        not state.source_permitted(connection, ref, "evidence_read") for ref in dependencies.sources
    ):
        reasons.append("source_denied")
    if any(
        not state.source_permitted(connection, ref, "archive_search")
        for ref in dependencies.archive_sources
    ):
        reasons.append("archive_source_denied")
    return RelatedResponse(
        status="invalid" if reasons else "complete",
        valid=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        rows_examined=rows_count,
        source_calls=calls,
        dependencies=dependencies if request.refresh_dependencies and not reasons else None,
    )


def lookup(
    state: CharacterState, connection: sqlite3.Connection, request: RelatedRequest
) -> RelatedResponse:
    """Explicit relationship reads only; acyclic supersession resolution has a fixed hop ceiling."""
    if request.intent == "validate":
        return validate(state, connection, request)
    deadline = timestamp_from_datetime(state.database.clock.now() + timedelta(seconds=2))
    if request.deadline_at is not None:
        deadline = min(deadline, request.deadline_at)
    check_deadline(state.database.clock, deadline)
    epoch = state.policy_epoch(connection)
    memories: dict[CanonicalId, Memory] = {}
    edges: dict[CanonicalId, MemoryRelationship] = {}
    from .related_expansion import eligible_reference, neighborhood

    expanded, entities, rows = neighborhood(state, connection, request)
    distinct = {
        (reference.memory_id, reference.revision): reference
        for reference in (*request.anchors, *expanded)
    }
    anchors = tuple(distinct.values())[:32]
    partial = len(distinct) > 32 or rows >= request.row_budget

    def authorized(reference: MemoryReference) -> Memory:
        check_deadline(state.database.clock, deadline)
        if request.recall_only and not eligible_reference(state, connection, request, reference):
            raise AccessDeniedError()
        latest = state.read_memory(connection, reference.memory_id)
        # Historical active revisions cannot bypass a later manual lifecycle decision.
        if latest.lifecycle in {"disabled", "forgotten"}:
            raise AccessDeniedError()
        memory = (
            latest
            if latest.revision == reference.revision
            else state.read_memory(connection, reference.memory_id, reference.revision)
        )
        if not permitted(state, connection, str(memory.memory_id), memory.revision):
            raise AccessDeniedError()
        if any(
            not state.source_permitted(connection, a.evidence.source, "evidence_read")
            for a in memory.content.evidence
        ):
            raise AccessDeniedError()
        return memory

    for anchor in anchors:
        current = anchor
        visited: set[CanonicalId] = set()
        for _ in range(min(16, request.row_budget)):
            if current.memory_id in visited:
                partial = True
                break
            visited.add(current.memory_id)
            if rows >= request.row_budget or len(memories) >= request.limit:
                partial = True
                break
            try:
                memory = authorized(current)
            except MemoryError:
                partial = True
                break
            rows += 1
            if rows > request.row_budget:
                partial = True
                break
            memories.setdefault(memory.memory_id, memory)
            remaining = min(request.limit - len(edges), request.row_budget - rows)
            if remaining <= 0:
                partial = True
                break
            found = (
                connection.execute(
                    "SELECT * FROM memory_relationships WHERE "
                    "(source_id=? AND source_revision=?) OR (target_id=? AND target_revision=?) "
                    "ORDER BY relationship_id LIMIT ?",
                    (
                        str(current.memory_id),
                        current.revision,
                        str(current.memory_id),
                        current.revision,
                        remaining,
                    ),
                ).fetchall()
                if request.include_relationships
                else []
            )
            rows += min(len(found), remaining)
            if len(found) >= remaining:
                partial = True
            if (
                request.include_relationships
                and request.intent == "expand"
                and remaining > len(found)
            ):
                supports = connection.execute(
                    "SELECT * FROM memory_supports WHERE memory_id=? ORDER BY support_id LIMIT ?",
                    (str(current.memory_id), remaining - len(found)),
                ).fetchall()
                for support_row in supports[: remaining - len(found)]:
                    support = load_model(support_row, MemoryRelationship)
                    if all(
                        state.source_permitted(connection, e.evidence.source, "evidence_read")
                        for e in support.evidence
                    ):
                        edges.setdefault(support.relationship_id, support)
                    else:
                        partial = True
                    rows += 1
                if len(supports) > remaining - len(found):
                    partial = True
            following: MemoryReference | None = None
            for row in found[:remaining]:
                edge = load_model(row, MemoryRelationship)
                if any(
                    not state.source_permitted(connection, e.evidence.source, "evidence_read")
                    for e in edge.evidence
                ):
                    partial = True
                    continue
                if edge.target is not None:
                    if rows >= request.row_budget:
                        partial = True
                        continue
                    rows += 1
                    try:
                        target = authorized(edge.target)
                    except MemoryError:
                        partial = True
                        continue
                    if len(memories) < request.limit:
                        memories.setdefault(target.memory_id, target)
                    else:
                        partial = True
                edges.setdefault(edge.relationship_id, edge)
                if (
                    edge.kind in {"supersedes", "redirects"}
                    and edge.source.memory_id == current.memory_id
                ):
                    following = edge.target
            if request.intent != "resolve" or following is None:
                break
            current = following
        else:
            partial = True
        if rows >= request.row_budget or len(memories) >= request.limit:
            partial = True
            break
    # The optional expansion has the same fixed reference ceiling as prompt validation.
    # Large support histories stay in the canonical ledger, never in one response.
    selected_sources: dict[
        tuple[CanonicalId, str, CanonicalId, int, CanonicalId | None], SourceRef
    ] = {}
    bounded_memories: dict[CanonicalId, Memory] = {}
    bounded_edges: dict[CanonicalId, MemoryRelationship] = {}
    for memory in memories.values():
        added = {
            a.evidence.source.identity_key(): a.evidence.source for a in memory.content.evidence
        }
        if len(bounded_memories) + 1 + len(selected_sources.keys() | added.keys()) > 256:
            partial = True
            continue
        bounded_memories[memory.memory_id] = memory
        selected_sources.update(added)
    for edge in edges.values():
        added = {a.evidence.source.identity_key(): a.evidence.source for a in edge.evidence}
        if len(bounded_memories) + len(selected_sources.keys() | added.keys()) > 256:
            partial = True
            continue
        if edge.source.memory_id not in bounded_memories:
            partial = True
            continue
        if edge.target and edge.target.memory_id not in bounded_memories:
            partial = True
            continue
        bounded_edges[edge.relationship_id] = edge
        selected_sources.update(added)
    calls = 0
    if selected_sources:
        if isinstance(state.resolver, SourceHeaderResolver):
            missing, calls = state.resolver.unavailable_sources(
                tuple(selected_sources.values()),
                local_connection=connection,
                local_store_id=state.character_id,
                call_budget=request.call_budget,
            )
            denied = {r.identity_key() for r in missing}
        else:
            denied = set(selected_sources)
        if denied:
            partial = True
            bounded_memories = {
                key: m
                for key, m in bounded_memories.items()
                if not any(a.evidence.source.identity_key() in denied for a in m.content.evidence)
            }
            bounded_edges = {
                key: e
                for key, e in bounded_edges.items()
                if e.source.memory_id in bounded_memories
                and (e.target is None or e.target.memory_id in bounded_memories)
                and not any(a.evidence.source.identity_key() in denied for a in e.evidence)
            }
    from pydantic import ValidationError

    from .related_excerpts import excerpts as read_excerpts

    allowed_excerpts = max(0, min(24, (request.row_budget - rows) // 2))
    selected_excerpts, excerpt_partial = read_excerpts(
        state,
        connection,
        request.model_copy(update={"excerpts": request.excerpts[:allowed_excerpts]}),
    )
    partial = partial or excerpt_partial or allowed_excerpts < len(request.excerpts)
    rows += 2 * len(selected_excerpts)
    dependencies = None
    if request.recall_only:
        selected = list(bounded_memories.values())[: request.limit]
        quotes = list(selected_excerpts)
        while True:
            try:
                dependencies = certificate(
                    state,
                    connection,
                    tuple(selected),
                    temporal=request.temporal,
                )
                quoted_sources = tuple(
                    {
                        item.source.evidence.source.identity_key(): item.source.evidence.source
                        for item in quotes
                    }.values()
                )
                permitted_window, expiry = evidence_grant_window(
                    connection,
                    state.character_id,
                    quoted_sources,
                    timestamp_from_datetime(state.database.clock.now()),
                    capability=request.source_capability,
                )
                boundary = (
                    timestamp_from_datetime(state.database.clock.now())
                    if not permitted_window
                    else timestamp_from_unix_microseconds(expiry)
                    if expiry is not None
                    else None
                )
                boundaries = tuple(
                    value for value in (boundary, dependencies.valid_until) if value is not None
                )
                dependencies = ContextDependencies.model_validate(
                    {
                        **dependencies.model_dump(),
                        "excerpt_sources": quoted_sources
                        if request.source_capability == "evidence_read"
                        else (),
                        "archive_sources": quoted_sources
                        if request.source_capability == "archive_search"
                        else (),
                        "sources": dependencies.sources
                        if request.source_capability == "archive_search"
                        else tuple(
                            {
                                ref.identity_key(): ref
                                for ref in (*dependencies.sources, *quoted_sources)
                            }.values()
                        ),
                        "valid_until": min(boundaries) if boundaries else None,
                    }
                )
                break
            except ValidationError:
                partial = True
                if quotes:
                    quotes.pop()
                elif selected:
                    selected.pop()
                else:
                    raise
        selected_excerpts = tuple(quotes)
        bounded_memories = {m.memory_id: m for m in selected}
        bounded_edges = {
            key: edge
            for key, edge in bounded_edges.items()
            if edge.source.memory_id in bounded_memories
            and (edge.target is None or edge.target.memory_id in bounded_memories)
        }
    from ember_memory_abi.memory import MemoryDraft

    if dependencies is not None:
        confirmed = validate(
            state,
            connection,
            RelatedRequest(
                intent="validate",
                dependencies=dependencies,
                row_budget=512,
                call_budget=32,
                deadline_at=deadline,
            ),
        )
        rows += confirmed.rows_examined
        calls += confirmed.source_calls
        if confirmed.valid is not True:
            partial = True
            bounded_memories.clear()
            bounded_edges.clear()
            selected_excerpts = ()
            entities = ()
            dependencies = certificate(state, connection, ())
    from .library import entity_references

    entity_ids = {
        ref.entity_id
        for memory in bounded_memories.values()
        for ref in entity_references(MemoryDraft(content=memory.content))
    }
    entities = tuple(alias for alias in entities if alias.reference.entity_id in entity_ids)
    state.check_policy(connection, epoch)
    check_deadline(state.database.clock, deadline)
    meta = connection.execute("SELECT recorded_clock_us FROM store_metadata").fetchone()
    return RelatedResponse(
        status="partial" if partial else "complete",
        entities=entities,
        excerpts=selected_excerpts,
        dependencies=dependencies,
        memories=tuple(bounded_memories.values())[: request.limit],
        relationships=tuple(bounded_edges.values())[: request.limit],
        rows_examined=rows,
        source_calls=calls,
        manifest=ReadManifest(
            character_id=state.character_id,
            policy_epoch=epoch,
            selection_epoch=row_int(
                connection.execute("SELECT selection_epoch FROM profile_policy").fetchone(),
                "selection_epoch",
            ),
            recorded_clock_us=row_int(meta, "recorded_clock_us"),
        ),
        reasons=("bounded_or_unavailable",) if partial else (),
    )
