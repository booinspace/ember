"""Outbox replay into a separate generation; unchanged documents and vectors are copied."""

import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from ember_memory_abi.archive import Conversation, GraphNode, Message, SourceRecord
from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.memory import Entity

from .database import load_model, payload_hash, row_int, row_text
from .embedding import file_sha256
from .engine import CanonicalEngine
from .errors import IndexError
from .index_builder import IndexBuilder
from .index_storage import connect
from .search_contracts import Encoder, IndexGeneration

_ADAPTER: TypeAdapter[SourceRecord] = TypeAdapter(SourceRecord)
_KNOWN = frozenset(
    {
        "memory_revision",
        "source_changed",
        "entity_changed",
        "suppression_changed",
        "seed_grants",
        "grant_changed",
        "grant_revoked",
        "remember",
        "update",
        "supersede",
        "disable",
        "forget",
        "pin",
        "unpin",
        "record_access",
    }
)


def _selection(connection: sqlite3.Connection, table: str, identities: set[str]) -> None:
    """Create only trusted temporary metadata tables on a thread-owned connection."""
    connection.execute(f"CREATE TEMP TABLE {table}(id TEXT PRIMARY KEY)")
    connection.executemany(f"INSERT INTO {table} VALUES(?)", ((item,) for item in identities))


def _source_closure(source: sqlite3.Connection, changed: set[str]) -> set[str]:
    """Reproject complete affected conversations when graph selection/links gain information."""
    _selection(source, "changed_sources", changed)
    conversations: set[str] = set()
    for row in source.execute("SELECT * FROM source_records WHERE record_id IN changed_sources"):
        payload = row_text(row, "payload_json")
        if payload_hash(payload) != row_text(row, "payload_sha256"):
            raise IndexError()
        record = _ADAPTER.validate_json(payload)
        if isinstance(record, Conversation):
            conversations.add(str(record.source.record_id))
        elif isinstance(record, (GraphNode, Message)):
            conversations.add(str(record.conversation.record_id))
    _selection(source, "changed_conversations", conversations)
    related = source.execute(
        "SELECT record_id FROM source_records WHERE "
        "json_extract(payload_json,'$.conversation.record_id') IN changed_conversations"
    ).fetchall()
    return changed | {row_text(row, "record_id") for row in related}


def _delete_documents(output: sqlite3.Connection, predicate: str) -> None:
    """Remove complete projections together; preserve the published generation."""
    rows = output.execute("SELECT id FROM documents WHERE " + predicate).fetchall()
    output.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            identity = row_int(row, "id")
            vectors = output.execute(
                "SELECT id FROM embedding_records WHERE document_id=?", (identity,)
            ).fetchall()
            for vector in vectors:
                output.execute("DELETE FROM vec_documents WHERE rowid=?", (row_int(vector, "id"),))
            output.execute("DELETE FROM embedding_records WHERE document_id=?", (identity,))
            output.execute("DELETE FROM chunk_dependencies WHERE document_id=?", (identity,))
            output.execute("DELETE FROM documents WHERE id=?", (identity,))
        output.commit()
    except BaseException:
        output.rollback()
        raise


def _refresh_headers(output: sqlite3.Connection) -> None:
    """Recompute descendant suppression flags without re-encoding."""
    output.execute("BEGIN IMMEDIATE")
    try:
        output.execute("""
            UPDATE documents AS d SET
              is_current=EXISTS(SELECT 1 FROM canonical.memories m
                WHERE m.memory_id=d.target_id AND m.current_revision=d.revision),
              recall_eligible=EXISTS(SELECT 1 FROM canonical.memories m
                JOIN canonical.memory_revisions current INDEXED BY memory_retrieval_identity
                  ON current.memory_id=m.memory_id AND current.revision=m.current_revision
                WHERE m.memory_id=d.target_id AND current.lifecycle NOT IN ('disabled','forgotten')
                  AND NOT EXISTS(SELECT 1 FROM canonical.suppression_targets s
                    WHERE s.target_kind='memory' AND s.target_key=d.target_id)
                  AND NOT EXISTS(SELECT 1 FROM canonical.memory_dependencies dep
                    JOIN canonical.suppression_targets s ON s.target_kind=dep.target_kind
                      AND s.target_key=dep.target_key
                    WHERE dep.memory_id=d.target_id AND dep.revision=d.revision))
            WHERE d.kind='memory'
        """)
        # vec0 metadata is part of the same disposable transaction. Old revisions
        # remain present for historical queries through the canonical scope path.
        rows = output.execute(
            "SELECT e.id,d.recall_eligible,d.is_current,d.lifecycle FROM embedding_records e "
            "JOIN documents d ON d.id=e.document_id WHERE d.kind='memory'"
        ).fetchall()
        for row in rows:
            active = int(
                row_int(row, "recall_eligible") == 1
                and row_int(row, "is_current") == 1
                and row["lifecycle"] == "active"
            )
            output.execute(
                "UPDATE vec_documents SET active=? WHERE rowid=?", (active, row_int(row, "id"))
            )
        output.commit()
    except BaseException:
        output.rollback()
        raise


def update(engine: CanonicalEngine, encoder: Encoder, store_id: UUID) -> IndexGeneration:
    """Replay at most 8192 events; incompatible/corrupt generations rebuild from canonical data."""
    _, database = engine.maintenance_store(store_id)
    index = engine.indexes[store_id]
    builder = IndexBuilder(engine, encoder)
    try:
        previous, path = index.active()
        index.certified(previous, path)
    except IndexError:
        return builder.rebuild(store_id)
    if (
        previous.layout != "ember.search-layout@0.4.0"
        or previous.embedding != encoder.specification
    ):
        return builder.rebuild(store_id, reuse=True)
    identity = uuid4()
    target: Path = index.directory / "generations" / (str(identity) + ".db")
    with ExitStack() as cleanup:
        source = cleanup.enter_context(
            closing(sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True))
        )
        source.row_factory = sqlite3.Row
        watermark = row_int(
            source.execute("SELECT recorded_clock_us FROM store_metadata").fetchone(),
            "recorded_clock_us",
        )
        if watermark == previous.canonical_clock_us:
            return previous
        events = source.execute(
            "SELECT * FROM index_outbox WHERE sequence>? ORDER BY sequence LIMIT 8193",
            (previous.outbox_sequence,),
        ).fetchall()
        if len(events) > 8192 or any(row_text(row, "kind") not in _KNOWN for row in events):
            return builder.rebuild(store_id, reuse=True)
        memories = {
            row_text(row, "record_id")
            for row in events
            if row_text(row, "kind") == "memory_revision"
        }
        source_ids = {
            row_text(row, "record_id")
            for row in events
            if row_text(row, "kind") == "source_changed"
        }
        source_ids = _source_closure(source, source_ids)
        old = cleanup.enter_context(closing(connect(path, immutable=True)))
        output = cleanup.enter_context(closing(connect(target)))
        old.backup(output, pages=256)
        _selection(output, "affected_sources", source_ids)
        _selection(source, "affected_sources", source_ids)
        # Entity rename changes all matching semantic projections, even if the
        # immutable beliefs did not gain a revision.
        entities = {
            row_text(row, "record_id")
            for row in events
            if row_text(row, "kind") == "entity_changed"
        }
        _selection(output, "affected_entities", entities)
        memories.update(
            row_text(row, "target_id")
            for row in output.execute(
                "SELECT DISTINCT d.target_id FROM documents d JOIN json_each(d.entity_keys) entity "
                "WHERE d.kind='memory' AND entity.value IN affected_entities"
            ).fetchall()
        )
        _selection(output, "affected_memories", memories)
        _selection(source, "affected_memories", memories)
        _delete_documents(output, "kind='memory' AND target_id IN affected_memories")
        _delete_documents(
            output,
            "(kind='message' AND record_id IN affected_sources) OR "
            "(kind='chunk' AND id IN (SELECT document_id FROM chunk_dependencies "
            "WHERE record_id IN affected_sources))",
        )
        names: dict[str, str] = {}
        for row in source.execute(
            "SELECT e.* FROM entities e WHERE revision=(SELECT MAX(revision) "
            "FROM entities n WHERE n.entity_id=e.entity_id)"
        ).fetchall():
            entity = load_model(row, Entity)
            names[str(entity.reference.entity_id)] = " ".join((entity.name, *entity.aliases))
        builder.project_memories(
            source, output, watermark, names, "AND r.memory_id IN affected_memories"
        )
        builder.project_sources(source, output, watermark, "AND record_id IN affected_sources")
        builder.project_chunks(output, "AND record_id IN affected_sources")
        output.execute("ATTACH DATABASE ? AS canonical", (database.path.as_uri() + "?mode=ro",))
        if any(row_text(row, "kind") == "suppression_changed" for row in events):
            _refresh_headers(output)
        output.execute("DETACH DATABASE canonical")
        builder.project_vectors(output, old)
        if output.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise IndexError()
        documents = row_int(
            output.execute("SELECT COUNT(*) AS total FROM documents").fetchone(), "total"
        )
        vectors = row_int(
            output.execute("SELECT COUNT(*) AS total FROM embedding_records").fetchone(), "total"
        )
        if (
            row_int(
                source.execute("SELECT recorded_clock_us FROM store_metadata").fetchone(),
                "recorded_clock_us",
            )
            != watermark
        ):
            raise IndexError()
        sequence = row_int(
            source.execute(
                "SELECT COALESCE(MAX(sequence),0) AS total FROM index_outbox"
            ).fetchone(),
            "total",
        )
    generation = previous.model_copy(
        update={
            "generation": identity,
            "canonical_clock_us": watermark,
            "outbox_sequence": sequence,
            "documents": documents,
            "vectors": vectors,
            "created_at": timestamp_from_datetime(engine.clock.now()),
            "file_sha256": file_sha256(target),
        }
    )
    index.publish(generation)
    index.prune()
    return generation
