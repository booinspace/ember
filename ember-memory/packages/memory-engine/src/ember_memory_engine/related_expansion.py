"""Bounded canonical alias/source neighborhoods reuse retrieval eligibility before limits."""

import sqlite3
from uuid import UUID

from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_abi.memory import EntityRef
from ember_memory_abi.related import MemoryReference, RelatedRequest, ResolvedEntity

from ._state import CharacterState
from .database import row_int, row_text
from .retrieval import memory_scope


def scoped_statement(
    state: CharacterState, query: MemoryQuery, clause: str, parameters: tuple[str | int, ...]
) -> tuple[str, tuple[str | int, ...]]:
    """Insert an internal parameterized narrowing predicate before ranking and LIMIT."""
    sql, values = memory_scope(state, query, metadata_only=True)
    marker = sql.index(" ORDER BY ")
    return sql[:marker] + " AND (" + clause + ")" + sql[marker:], (
        *values[:-1],
        *parameters,
        values[-1],
    )


def eligible_reference(
    state: CharacterState,
    connection: sqlite3.Connection,
    request: RelatedRequest,
    reference: MemoryReference,
) -> bool:
    """Relationship inspection can only narrow recall under the original query selector."""
    sql, values = scoped_statement(
        state,
        MemoryQuery(temporal=request.temporal, mode="structured", limit=1),
        "r.memory_id=? AND r.revision=?",
        (str(reference.memory_id), reference.revision),
    )
    return connection.execute(sql, values).fetchone() is not None


def neighborhood(
    state: CharacterState,
    connection: sqlite3.Connection,
    request: RelatedRequest,
) -> tuple[tuple[MemoryReference, ...], tuple[ResolvedEntity, ...], int]:
    """One alias batch and one source batch; no similarity recursion or graph service."""
    limit = min(24, request.limit, request.row_budget)
    aliases: list[ResolvedEntity] = []
    references: dict[tuple[UUID, int], MemoryReference] = {}
    rows = 0
    if request.entity_names:
        placeholders = ",".join("?" for _ in request.entity_names)
        # Exclude the common subject entity. Similar text or a shared user is never
        # an incident connection, and pending/import-only aliases have no eligible row.
        clause = (
            "EXISTS(SELECT 1 FROM memory_dependencies d JOIN entity_aliases a "
            "ON a.entity_id=d.target_key WHERE d.memory_id=r.memory_id AND "
            "d.revision=r.revision AND d.target_kind='entity' "
            "AND a.entity_id<>p.subject_id "
            "AND a.entity_id<>COALESCE((SELECT subject_id FROM profile_policy),'') "
            "AND lower(a.alias) IN (" + placeholders + "))"
        )
        sql, parameters = scoped_statement(
            state,
            MemoryQuery(temporal=request.temporal, mode="structured", limit=limit),
            clause,
            tuple(name.casefold() for name in request.entity_names),
        )
        found = connection.execute(sql, parameters).fetchall()
        rows += len(found)
        for row in found:
            reference = MemoryReference(
                memory_id=UUID(row_text(row, "memory_id")), revision=row_int(row, "revision")
            )
            references[(reference.memory_id, reference.revision)] = reference
        if references:
            pairs = ",".join("(?,?)" for _ in references)
            values = tuple(
                value for identity, revision in references for value in (str(identity), revision)
            )
            matches = connection.execute(
                "SELECT DISTINCT a.entity_id,a.alias FROM entity_aliases a "
                "JOIN memory_dependencies d ON d.target_kind='entity' AND d.target_key=a.entity_id "
                "JOIN claim_projection p ON p.memory_id=d.memory_id AND p.revision=d.revision "
                "WHERE (d.memory_id,d.revision) IN (" + pairs + ") AND a.entity_id<>p.subject_id "
                "AND a.entity_id<>COALESCE((SELECT subject_id FROM profile_policy),'') "
                "AND lower(a.alias) IN (" + placeholders + ") ORDER BY a.entity_id,a.alias LIMIT 8",
                (*values, *(name.casefold() for name in request.entity_names)),
            ).fetchall()
            rows += len(matches)
            aliases = [
                ResolvedEntity(
                    reference=EntityRef(
                        store_id=state.character_id,
                        entity_id=UUID(row_text(row, "entity_id")),
                    ),
                    alias=row_text(row, "alias"),
                )
                for row in matches
            ]
    allowed = tuple(
        ref for ref in request.sources if state.source_permitted(connection, ref, "evidence_read")
    )
    if allowed and len(references) < limit:
        wanted = ",".join("(?,?,?,?,?)" for _ in allowed)
        parameters = tuple(
            value
            for ref in allowed
            for value in (
                str(ref.source_store_id),
                ref.record_kind,
                str(ref.record_id),
                ref.record_revision,
                str(ref.part_id) if ref.part_id else "",
            )
        )
        clause = (
            "EXISTS(SELECT 1 FROM memory_evidence e WHERE e.memory_id=r.memory_id "
            "AND e.revision=r.revision AND "
            "(e.source_store_id,e.record_kind,e.record_id,e.record_revision,e.part_id) IN ("
            + wanted
            + "))"
        )
        sql, values = scoped_statement(
            state,
            MemoryQuery(temporal=request.temporal, mode="structured", limit=limit),
            clause,
            parameters,
        )
        found = connection.execute(sql, values).fetchall()
        rows += len(found)
        for row in found:
            reference = MemoryReference(
                memory_id=UUID(row_text(row, "memory_id")), revision=row_int(row, "revision")
            )
            references[(reference.memory_id, reference.revision)] = reference
    return tuple(references.values())[:limit], tuple(aliases), rows
