"""Rebuildable indexed claim headers; acceptance never relies on embedding similarity."""

import sqlite3

from ember_memory_abi.common import canonical_object_sha256
from ember_memory_abi.memory import EntityValue, EpisodePayload, FactPayload, Memory, StringValue


def identity(memory: Memory) -> tuple[str, str, str, str, str]:
    """Exact normalized facts; events additionally retain original source/time identity."""
    payload = memory.content.payload
    subject, predicate, value = "", "", ""
    if isinstance(payload, FactPayload):
        subject, predicate = str(payload.subject.entity_id), payload.predicate
        raw = payload.value.model_dump(mode="json")
        if isinstance(payload.value, StringValue):
            raw["value"] = " ".join(payload.value.value.casefold().split())
        value = canonical_object_sha256({"value": raw, "polarity": payload.polarity})
    origin = (
        str(memory.origin.origin_source_store_id) if memory.origin else str(memory.character_id)
    )
    key = canonical_object_sha256(
        {
            "subject": subject,
            "predicate": predicate,
            "value": value,
            "modality": memory.content.modality,
            "kind": memory.content.kind,
            "event": None if predicate else payload.model_dump(mode="json"),
            "event_sources": []
            if predicate and memory.content.modality != "PAST_STATE"
            else [item.evidence.source.model_dump(mode="json") for item in memory.content.evidence],
            "temporal": (
                {
                    "valid_from": memory.temporal.valid_from,
                    "valid_until": memory.temporal.valid_until,
                }
                if predicate
                else memory.temporal.model_dump(exclude={"recorded_at", "last_confirmed_at"})
            ),
            "origin": origin,
        }
    )
    return subject, predicate, value, origin, key


def project(connection: sqlite3.Connection, memory: Memory) -> None:
    """Update only compact headers, preserving immutable memory JSON and historical revisions."""
    subject, predicate, value, origin, key = identity(memory)
    connection.execute(
        "INSERT OR REPLACE INTO claim_projection VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            str(memory.memory_id),
            memory.revision,
            subject,
            predicate,
            value,
            origin,
            key,
            memory.temporal.observed_at,
            memory.temporal.basis,
            int(
                any(
                    a.evidence.source.source_store_id != memory.character_id
                    for a in memory.content.evidence
                )
            ),
        ),
    )

    payload = memory.content.payload
    entities: set[str] = set()
    if isinstance(payload, FactPayload):
        entities.add(str(payload.subject.entity_id))
        if isinstance(payload.value, EntityValue):
            entities.add(str(payload.value.value.entity_id))
    elif isinstance(payload, EpisodePayload):
        entities.update(str(e.entity_id) for e in payload.participants)
        if payload.place:
            entities.add(str(payload.place.entity_id))
    connection.executemany(
        "INSERT OR IGNORE INTO memory_dependencies VALUES(?,?,'entity',?)",
        ((str(memory.memory_id), memory.revision, entity) for entity in entities),
    )


def backfill_supersessions(connection: sqlite3.Connection) -> None:
    """Reconstruct typed edges from already validated canonical transitions, in bounded pages."""
    from uuid import UUID, uuid4

    from pydantic import TypeAdapter

    from ember_memory_abi.evidence import EvidenceAssertion
    from ember_memory_abi.related import MemoryReference, MemoryRelationship

    from .database import load_model, model_bytes, payload_hash, row_int, row_text

    adapter = TypeAdapter(tuple[EvidenceAssertion, ...])
    cursor = connection.execute("SELECT * FROM memory_supersessions")
    while rows := cursor.fetchmany(256):
        for row in rows:
            old_id, new_id = row_text(row, "old_memory_id"), row_text(row, "new_memory_id")
            old_revision, new_revision = row_int(row, "old_revision"), row_int(row, "new_revision")
            if connection.execute(
                "SELECT 1 FROM memory_relationships WHERE kind='supersedes' "
                "AND source_id=? AND source_revision=? AND target_id=? "
                "AND target_revision=? LIMIT 1",
                (old_id, old_revision, new_id, new_revision),
            ).fetchone():
                continue
            originals = tuple(
                load_model(
                    connection.execute(
                        "SELECT * FROM memory_revisions WHERE memory_id=? AND revision=?",
                        (memory_id, revision),
                    ).fetchone(),
                    Memory,
                )
                for memory_id, revision in ((old_id, old_revision), (new_id, new_revision))
            )
            edge = MemoryRelationship(
                relationship_id=uuid4(),
                kind="supersedes",
                source=MemoryReference(memory_id=UUID(old_id), revision=old_revision),
                target=MemoryReference(memory_id=UUID(new_id), revision=new_revision),
                evidence=adapter.validate_json(row_text(row, "transition_json")),
                origins=tuple(memory.origin for memory in originals if memory.origin),
                provenance=tuple(p for memory in originals for p in memory.content.provenance),
            )
            payload = model_bytes(edge)
            connection.execute(
                "INSERT OR IGNORE INTO memory_relationships VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(edge.relationship_id),
                    edge.kind,
                    old_id,
                    old_revision,
                    new_id,
                    new_revision,
                    payload,
                    payload_hash(payload),
                ),
            )
