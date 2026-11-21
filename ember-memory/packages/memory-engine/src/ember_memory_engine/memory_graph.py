"""Read-only graph of eligible current memories and their canonical connections."""

import sqlite3
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from ember_memory_abi.common import ContractModel
from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_abi.memory import (
    EntityValue,
    EpisodePayload,
    FactPayload,
    GoalPayload,
    Memory,
    NumberValue,
    PlanPayload,
)
from ember_memory_abi.related import MemoryRelationship

from ._state import CharacterState
from .database import load_model, row_int, row_text
from .retrieval import memory_scope
from .word_recall import register_word_match

_BATCH = 200
_NEIGHBORHOOD = 36
_ENTITY_LINKS = 400
_RELATIONSHIPS = 120
_NEIGHBOR_ENTITY_SCOPE = (
    "d.target_kind='entity' AND d.target_key<>COALESCE((SELECT subject_id FROM profile_policy),'')"
)


@dataclass(frozen=True)
class GraphQuery:
    """Internal selection from the validated operator request."""

    text: str = ""
    kind: str = ""
    cursor: str | None = None
    anchor: UUID | None = None


class GraphNode(ContractModel):
    """An extracted memory or an entity already linked to a visible memory."""

    id: UUID
    category: Literal["memory", "entity"]
    kind: str
    label: str
    modality: str | None = None
    recorded_at: str | None = None


class GraphLink(ContractModel):
    """A canonical relationship or a reference to a stored entity."""

    source: UUID
    target: UUID
    kind: Literal["entity", "supports", "conflicts", "redirects", "supersedes"]


class MemoryGraph(ContractModel):
    """A bounded operator view with no source excerpts or model payloads."""

    nodes: tuple[GraphNode, ...] = ()
    links: tuple[GraphLink, ...] = ()
    next_cursor: str | None = None
    truncated: bool = False
    total_memories: int | None = None


def _select(
    state: CharacterState,
    connection: sqlite3.Connection,
    query: MemoryQuery,
    clause: str = "",
    values: tuple[str | int, ...] = (),
    *,
    limit: int | None = None,
    metadata_only: bool = False,
) -> list[sqlite3.Row]:
    sql, bindings = memory_scope(
        state, query, include_text=bool(query.text), recent_first=True, metadata_only=metadata_only
    )
    if clause:
        start = sql.index(" ORDER BY ")
        sql = sql[:start] + " AND (" + clause + ")" + sql[start:]
    return connection.execute(sql, (*bindings[:-1], *values, limit or query.limit)).fetchall()


def _label(memory: Memory, names: dict[UUID, str]) -> str:
    payload = memory.content.payload
    if isinstance(payload, FactPayload):
        value = payload.value
        if isinstance(value, EntityValue):
            text = names.get(value.value.entity_id, "Linked entity")
        elif isinstance(value, NumberValue) and value.unit:
            text = f"{value.value} {value.unit}"
        else:
            text = str(value.value)
        polarity = "" if payload.polarity == "positive" else " (" + payload.polarity + ")"
        return f"{payload.predicate.replace('.', ' ').replace('_', ' ')}{polarity} · {text}"
    if isinstance(payload, EpisodePayload):
        return payload.event
    if isinstance(payload, (GoalPayload, PlanPayload)):
        return payload.description
    return memory.content.kind.replace("_", " ")


def _permitted(
    state: CharacterState, connection: sqlite3.Connection, edge: MemoryRelationship
) -> bool:
    return all(
        state.source_permitted(connection, item.evidence.source, "evidence_read")
        for item in edge.evidence
    )


def read_graph(
    state: CharacterState, connection: sqlite3.Connection, request: GraphQuery
) -> MemoryGraph:
    """Apply current recall eligibility before selecting rows or exposing any edge."""
    text = request.text.strip()
    if text:
        register_word_match(connection, text)
    query = MemoryQuery(text=text, kinds=(request.kind,) if request.kind else ())
    limit = _NEIGHBORHOOD if request.anchor is not None else _BATCH
    anchor_rows: list[sqlite3.Row] = []
    clause = ""
    anchor_truncated = False
    values: tuple[str | int, ...] = ()
    if request.anchor is not None:
        anchor = str(request.anchor)
        anchor_rows = _select(state, connection, MemoryQuery(limit=1), "r.memory_id=?", (anchor,))
        if not anchor_rows:
            return MemoryGraph()
        revision = row_int(anchor_rows[0], "revision")
        linked = connection.execute(
            "SELECT * FROM memory_relationships WHERE "
            "(source_id=? AND source_revision=?) OR (target_id=? AND target_revision=?) "
            "ORDER BY relationship_id LIMIT ?",
            (anchor, revision, anchor, revision, _RELATIONSHIPS + 1),
        ).fetchall()
        anchor_truncated = len(linked) > _RELATIONSHIPS
        neighbors: dict[tuple[str, int], None] = {}
        for row in linked[:_RELATIONSHIPS]:
            edge = load_model(row, MemoryRelationship)
            if edge.target is not None and _permitted(state, connection, edge):
                other = edge.target if edge.source.memory_id == request.anchor else edge.source
                neighbors[(str(other.memory_id), other.revision)] = None
        wanted = ",".join("(?,?)" for _ in neighbors) or "(NULL,NULL)"
        clause = (
            "r.memory_id<>? AND ((r.memory_id,r.revision) IN (" + wanted + ") "
            "OR EXISTS(SELECT 1 FROM memory_dependencies d JOIN memory_dependencies a "
            "ON a.target_kind=d.target_kind AND a.target_key=d.target_key "
            "WHERE a.memory_id=? AND a.revision=? AND d.memory_id=r.memory_id "
            "AND d.revision=r.revision AND " + _NEIGHBOR_ENTITY_SCOPE + "))"
        )
        values = (anchor, *(value for pair in neighbors for value in pair), anchor, revision)
    elif request.cursor:
        clock, identity = request.cursor.split(":", 1)
        timestamp = min(int(clock), 2**63 - 1)
        clause = "r.recorded_at_us<? OR (r.recorded_at_us=? AND r.memory_id>?)"
        values = (timestamp, timestamp, identity)

    total = None
    if request.anchor is None and request.cursor is None:
        sql, bindings = memory_scope(state, query, include_text=bool(text), metadata_only=True)
        scope = sql.split(" FROM ", 1)[1].rsplit(" ORDER BY ", 1)[0]
        count = connection.execute(
            "SELECT COUNT(*) AS total FROM " + scope, bindings[:-1]
        ).fetchone()
        total = row_int(count, "total")
    found = _select(state, connection, query, clause, values, limit=limit + 1)
    page = found[: limit - len(anchor_rows)]
    rows = [*anchor_rows, *page]
    if not rows:
        return MemoryGraph(total_memories=total)
    more = len(found) > len(page)
    memories = [load_model(row, Memory) for row in rows]
    references = tuple((str(memory.memory_id), memory.revision) for memory in memories)
    pairs = ",".join("(?,?)" for _ in references)
    parameters = tuple(value for reference in references for value in reference)
    entity_rows = connection.execute(
        "SELECT d.memory_id,d.target_key AS entity_id,"
        "substr(json_extract(e.payload_json,'$.name'),1,120) AS name "
        "FROM memory_dependencies d JOIN entities e "
        "ON e.entity_id=d.target_key AND e.revision="
        "(SELECT MAX(j.revision) FROM entities j WHERE j.entity_id=e.entity_id) "
        "WHERE (d.memory_id,d.revision) IN ("
        + pairs
        + ") AND d.target_kind='entity' ORDER BY d.memory_id,d.target_key LIMIT ?",
        (*parameters, _ENTITY_LINKS + 1),
    ).fetchall()
    names: dict[UUID, str] = {}
    links: list[GraphLink] = []
    for row in entity_rows[:_ENTITY_LINKS]:
        entity = UUID(row_text(row, "entity_id"))
        names.setdefault(entity, row_text(row, "name"))
        links.append(
            GraphLink(source=UUID(row_text(row, "memory_id")), target=entity, kind="entity")
        )
    relationship_rows = connection.execute(
        "WITH page(id,revision) AS (VALUES " + pairs + ") "
        "SELECT l.* FROM memory_relationships l WHERE target_id IS NOT NULL AND ("
        "source_id IN (SELECT id FROM page) OR target_id IN (SELECT id FROM page)) "
        "AND ((source_id,source_revision) IN page OR (target_id,target_revision) IN page) "
        "ORDER BY relationship_id LIMIT ?",
        (*parameters, _RELATIONSHIPS + 1),
    ).fetchall()
    visible = set(references)
    edges = [load_model(row, MemoryRelationship) for row in relationship_rows[:_RELATIONSHIPS]]
    outside = {
        (str(endpoint.memory_id), endpoint.revision)
        for edge in edges
        for endpoint in (edge.source, edge.target)
        if endpoint is not None and (str(endpoint.memory_id), endpoint.revision) not in visible
    }
    if outside and request.anchor is None:
        wanted = ",".join("(?,?)" for _ in outside)
        identities = tuple(identity for identity, _ in outside)
        eligible = _select(
            state,
            connection,
            query,
            "r.memory_id IN (" + ",".join("?" for _ in identities) + ") "
            "AND (r.memory_id,r.revision) IN (" + wanted + ")",
            (*identities, *(value for pair in outside for value in pair)),
            limit=len(outside),
            metadata_only=True,
        )
        visible.update((row_text(row, "memory_id"), row_int(row, "revision")) for row in eligible)
    for edge in edges:
        if (
            edge.target
            and (str(edge.source.memory_id), edge.source.revision) in visible
            and (str(edge.target.memory_id), edge.target.revision) in visible
            and _permitted(state, connection, edge)
        ):
            links.append(
                GraphLink(
                    source=edge.source.memory_id, target=edge.target.memory_id, kind=edge.kind
                )
            )

    nodes = [
        GraphNode(
            id=memory.memory_id,
            category="memory",
            kind=memory.content.kind,
            label=_label(memory, names)[:600],
            modality=memory.content.modality,
            recorded_at=memory.temporal.recorded_at,
        )
        for memory in memories
    ]
    nodes.extend(
        GraphNode(id=identity, category="entity", kind="entity", label=name)
        for identity, name in names.items()
    )
    cursor = None
    if more and request.anchor is None:
        last = page[-1]
        cursor = f"{row_int(last, 'recorded_at_us')}:{row_text(last, 'memory_id')}"
    return MemoryGraph(
        nodes=tuple(nodes),
        links=tuple(links),
        next_cursor=cursor,
        total_memories=total,
        truncated=anchor_truncated
        or (more and request.anchor is not None)
        or len(entity_rows) > _ENTITY_LINKS
        or len(relationship_rows) > _RELATIONSHIPS,
    )
