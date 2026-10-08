"""Explicit offline derived rebuild; all canonical rows/IDs and raw sources stay unchanged."""

import json
import sqlite3
from contextlib import ExitStack, closing
from hashlib import sha256
from uuid import UUID, uuid4

from ember_memory_abi.archive import Document, Message
from ember_memory_abi.common import (
    canonical_object_sha256,
    timestamp_from_datetime,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.memory import (
    Entity,
    EntityValue,
    EpisodePayload,
    FactPayload,
    GoalPayload,
    Memory,
    PlanPayload,
)

from .chunking import Unit, group_chunks
from .database import load_model, row_int, row_text
from .embedding import file_sha256, validate_vector
from .engine import CanonicalEngine
from .errors import IndexError
from .index_storage import INDEX_SQL, connect
from .search_contracts import Encoder, IndexGeneration
from .source_access import source_keys, source_rows
from .source_graph import selected_path


def memory_text(memory: Memory, names: dict[str, str]) -> str:
    """Project semantics and owned entity names; never include evidence JSON or hidden metadata."""
    payload = memory.content.payload
    if isinstance(payload, FactPayload):
        value = (
            names.get(str(payload.value.value.entity_id), "entity")
            if isinstance(payload.value, EntityValue)
            else str(payload.value.value)
        )
        return " ".join(
            (
                names.get(str(payload.subject.entity_id), "subject"),
                payload.predicate.replace(".", " ").replace("_", " "),
                payload.polarity,
                value,
            )
        )
    if isinstance(payload, EpisodePayload):
        return (
            payload.event
            + " "
            + " ".join(
                names.get(str(item.entity_id), "participant") for item in payload.participants
            )
        )
    if isinstance(payload, GoalPayload):
        return payload.description
    if isinstance(payload, PlanPayload):
        return "\n".join((payload.description, *payload.steps))
    return ""


class IndexBuilder:
    """An explicit maintenance owner builds/validates a generation before publishing it."""

    def __init__(self, engine: CanonicalEngine, encoder: Encoder) -> None:
        self.engine = engine
        self.encoder = encoder

    def rebuild(self, store_id: UUID, *, reuse: bool = False) -> IndexGeneration:
        """Build FTS, graph units and vectors with embedding work outside transactions.

        A canonical watermark change aborts publication. Previous valid generation
        stays selected; failed scratch generations can be removed without data loss.
        """
        info, database = self.engine.maintenance_store(store_id)
        index = self.engine.indexes[store_id]
        directory = index.directory / "generations"
        directory.mkdir(parents=True, exist_ok=True)
        identity = uuid4()
        target = directory / (str(identity) + ".db")
        with closing(sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True)) as source:
            source.row_factory = sqlite3.Row
            watermark = row_int(
                source.execute("SELECT recorded_clock_us FROM store_metadata").fetchone(),
                "recorded_clock_us",
            )
            sequence = source.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM index_outbox"
            ).fetchone()[0]
            if not isinstance(sequence, int):
                raise IndexError()
            names: dict[str, str] = {}
            for row in source.execute(
                "SELECT e.* FROM entities e WHERE revision=(SELECT MAX(revision) FROM "
                "entities n WHERE n.entity_id=e.entity_id)"
            ).fetchall():
                entity = load_model(row, Entity)
                names[str(entity.reference.entity_id)] = " ".join((entity.name, *entity.aliases))
            with ExitStack() as cleanup:
                previous: sqlite3.Connection | None = None
                if reuse:
                    try:
                        old, old_path = index.active()
                        if old.embedding == self.encoder.specification:
                            index.certified(old, old_path)
                            previous = cleanup.enter_context(
                                closing(connect(old_path, immutable=True))
                            )
                    except IndexError:
                        pass
                output = cleanup.enter_context(closing(connect(target)))
                output.executescript(INDEX_SQL)
                output.execute(
                    "INSERT INTO metadata VALUES(?)",
                    (self.encoder.specification.model_dump_json(),),
                )
                if info.kind == "character":
                    self.project_memories(source, output, watermark, names)
                self.project_sources(source, output, watermark)
                self.project_chunks(output)
                # Encode bounded batches after every SELECT cursor is exhausted.
                self.project_vectors(output, previous)
                output.execute("INSERT INTO document_fts(document_fts) VALUES('rebuild')")
                if output.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise IndexError()
                documents = output.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                vectors = output.execute("SELECT COUNT(*) FROM embedding_records").fetchone()[0]
                if not isinstance(documents, int) or not isinstance(vectors, int):
                    raise IndexError()
            current = row_int(
                source.execute("SELECT recorded_clock_us FROM store_metadata").fetchone(),
                "recorded_clock_us",
            )
            if current != watermark:
                raise IndexError()
        generation = IndexGeneration(
            generation=identity,
            store_id=store_id,
            canonical_clock_us=watermark,
            outbox_sequence=sequence,
            embedding=self.encoder.specification,
            distance_metric="l2",
            header_projection="ember.canonical-headers@0.1.0",
            layout="ember.search-layout@0.4.0",
            documents=documents,
            vectors=vectors,
            created_at=timestamp_from_datetime(self.engine.clock.now()),
            file_sha256=file_sha256(target),
        )
        index.publish(generation)
        index.prune()
        return generation

    def project_memories(
        self,
        source: sqlite3.Connection,
        target: sqlite3.Connection,
        watermark: int,
        names: dict[str, str],
        selection: str = "",
    ) -> None:
        """Project bounded selected canonical pages into a caller-owned disposable database."""
        last = 0
        while True:
            rows = source.execute(
                "SELECT r.rowid AS source_rowid,r.*,m.current_revision,current.lifecycle AS "
                "current_lifecycle,(EXISTS(SELECT 1 FROM suppression_targets s "
                "WHERE s.target_kind='memory' AND s.target_key=r.memory_id) OR EXISTS("
                "SELECT 1 FROM memory_dependencies d JOIN suppression_targets s ON "
                "s.target_kind=d.target_kind AND s.target_key=d.target_key WHERE "
                "d.memory_id=r.memory_id AND d.revision=r.revision)) AS suppressed "
                "FROM memory_revisions r JOIN memories m ON m.memory_id=r.memory_id "
                "JOIN memory_revisions current ON current.memory_id=m.memory_id AND "
                "current.revision=m.current_revision WHERE r.rowid>? AND "
                "r.recorded_at_us<=? " + selection + " ORDER BY r.rowid LIMIT 256",
                (last, watermark),
            ).fetchall()
            if not rows:
                return
            target.execute("BEGIN IMMEDIATE")
            for row in rows:
                last = row_int(row, "source_rowid")
                memory = load_model(row, Memory)
                text = memory_text(memory, names)
                payload = memory.content.payload
                entities: list[str] = []
                predicate = None
                if isinstance(payload, FactPayload):
                    predicate = payload.predicate
                    entities.append(str(payload.subject.entity_id))
                    if isinstance(payload.value, EntityValue):
                        entities.append(str(payload.value.value.entity_id))
                elif isinstance(payload, EpisodePayload):
                    entities.extend(str(item.entity_id) for item in payload.participants)
                target.execute(
                    "INSERT INTO "
                    "documents(kind,target_id,revision,text,source_store_id,recorded_at_us,"
                    "recall_eligible,is_current,memory_kind,lifecycle,modality,valid_from_us,"
                    "valid_until_us,open_end_known,predicate,entity_keys,payload_key) "
                    "VALUES('memory',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(memory.memory_id),
                        memory.revision,
                        text,
                        str(memory.character_id),
                        timestamp_to_unix_microseconds(memory.temporal.recorded_at),
                        int(
                            row_text(row, "current_lifecycle") not in {"disabled", "forgotten"}
                            and not row_int(row, "suppressed")
                        ),
                        int(memory.revision == row_int(row, "current_revision")),
                        memory.content.kind,
                        memory.lifecycle,
                        memory.content.modality,
                        timestamp_to_unix_microseconds(memory.temporal.valid_from)
                        if memory.temporal.valid_from
                        else None,
                        timestamp_to_unix_microseconds(memory.temporal.valid_until)
                        if memory.temporal.valid_until
                        else None,
                        int(memory.temporal.basis == "explicit_open_end"),
                        predicate,
                        json.dumps(entities),
                        canonical_object_sha256(memory.content.payload),
                    ),
                )

            target.commit()

    def project_sources(
        self,
        source: sqlite3.Connection,
        target: sqlite3.Connection,
        watermark: int,
        selection: str = "",
    ) -> None:
        """Project visible source parts under the supplied canonical watermark."""
        # Cache only graph metadata, never source bodies. A typical conversation's
        # nodes are small; cap the cache to prevent account-scale metadata growth.
        cache: dict[tuple[str, int], tuple[dict[tuple[str, int], int], bool]] = {}
        target.execute("BEGIN IMMEDIATE")
        for position_in_batch, record in enumerate(source_rows(source, watermark, selection)):
            if position_in_batch and position_in_batch % 256 == 0:
                target.commit()
                target.execute("BEGIN IMMEDIATE")
            if not isinstance(record, (Message, Document)):
                continue
            if isinstance(record, Message) and record.channel in {
                "analysis",
                "reasoning",
                "thoughts",
                "reasoning_recap",
            }:
                continue
            conversation, branch, position = "", "not_applicable", 0
            role = record.actor.role if isinstance(record, Message) else "user"
            source_at = record.created_time.value
            if isinstance(record, Message):
                key = (str(record.conversation.record_id), record.conversation.record_revision)
                if key not in cache:
                    if len(cache) >= 32:
                        cache.clear()
                    cache[key] = selected_path(source, record.conversation)
                selected, resolved = cache[key]
                membership = (str(record.source.record_id), record.source.record_revision)
                branch = (
                    ("selected" if membership in selected else "alternative")
                    if resolved
                    else "ambiguous"
                )
                position = selected.get(membership, -1)
                conversation = (
                    str(record.conversation.record_id)
                    + "@"
                    + str(record.conversation.record_revision)
                )
            for part in record.parts:
                if (
                    part.status != "available"
                    or not part.text
                    or part.kind in {"analysis", "reasoning", "thoughts", "reasoning_recap"}
                ):
                    continue
                reference = record.source.model_copy(update={"part_id": part.part_id})
                part_key, whole_key = source_keys(reference)
                inserted = target.execute(
                    "INSERT INTO "
                    "documents(kind,target_id,revision,text,payload_json,source_store_id,"
                    "record_kind,record_id,part_id,conversation_id,branch,position,role,"
                    "source_at_us,recorded_at_us,source_key,whole_source_key) "
                    "VALUES('message',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(record.source.record_id),
                        record.source.record_revision,
                        part.text,
                        reference.model_dump_json(),
                        str(record.source.source_store_id),
                        record.source.record_kind,
                        str(record.source.record_id),
                        str(part.part_id),
                        conversation,
                        branch,
                        position,
                        role,
                        timestamp_to_unix_microseconds(source_at) if source_at else None,
                        timestamp_to_unix_microseconds(record.recorded_at),
                        part_key,
                        whole_key,
                    ),
                )

                target.execute(
                    "INSERT INTO chunk_dependencies VALUES(?,?,?,?,?,?,?,?)",
                    (
                        inserted.lastrowid,
                        str(reference.source_store_id),
                        reference.record_kind,
                        str(reference.record_id),
                        reference.record_revision,
                        str(reference.part_id),
                        part_key,
                        whole_key,
                    ),
                )

        target.commit()

    def project_chunks(self, target: sqlite3.Connection, selection: str = "") -> None:
        """Build exact graph-compatible chunks from the selected source projections."""
        last_group: tuple[str, str] | None = None
        units: list[Unit] = []
        # Enumerating metadata IDs does not retain corpus text in memory.
        ids = [
            row[0]
            for row in target.execute(
                "SELECT id FROM documents WHERE kind='message' " + selection + " ORDER BY "
                "conversation_id,branch,position,recorded_at_us,id"
            ).fetchall()
        ]

        def flush() -> None:
            if not units:
                return
            chunks = group_chunks(tuple(units), self.encoder)
            target.execute("BEGIN IMMEDIATE")
            for chunk in chunks:
                first = next(
                    unit for unit in units if unit.source == chunk.source_spans[0].evidence.source
                )
                cursor = target.execute(
                    "INSERT INTO "
                    "documents(kind,target_id,revision,text,payload_json,source_store_id,"
                    "conversation_id,branch,position,role,source_at_us,recorded_at_us) "
                    "VALUES('chunk',?,1,?,?,?,?,?,?,?,?,?)",
                    (
                        str(chunk.chunk_id),
                        chunk.text,
                        chunk.model_dump_json(),
                        str(first.source.source_store_id),
                        first.conversation,
                        first.branch,
                        first.position,
                        first.role,
                        first.source_at_us,
                        0,
                    ),
                )
                identity = cursor.lastrowid
                for span in chunk.source_spans:
                    ref = span.evidence.source
                    key, whole = source_keys(ref)
                    target.execute(
                        "INSERT OR IGNORE INTO chunk_dependencies VALUES(?,?,?,?,?,?,?,?)",
                        (
                            identity,
                            str(ref.source_store_id),
                            ref.record_kind,
                            str(ref.record_id),
                            ref.record_revision,
                            str(ref.part_id),
                            key,
                            whole,
                        ),
                    )
            target.commit()
            units.clear()

        for identity in ids:
            row = target.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            ref = SourceRef.model_validate_json(row_text(row, "payload_json"))
            group = (row_text(row, "conversation_id"), row_text(row, "branch"))
            if group != last_group or row_text(row, "branch") != "selected" or len(units) >= 64:
                flush()
            units.append(
                Unit(
                    source=ref,
                    text=row_text(row, "text"),
                    role=row_text(row, "role"),
                    conversation=group[0],
                    branch=group[1],
                    position=row_int(row, "position"),
                    source_at_us=row["source_at_us"],
                )
            )
            last_group = group
        flush()

    def project_vectors(
        self, target: sqlite3.Connection, previous: sqlite3.Connection | None
    ) -> None:
        last = 0
        while True:
            rows = target.execute(
                "SELECT * FROM documents d WHERE kind IN ('memory','chunk') AND id>? "
                "AND NOT EXISTS(SELECT 1 FROM embedding_records e WHERE e.document_id=d.id) "
                "ORDER BY id LIMIT 32",
                (last,),
            ).fetchall()
            if not rows:
                return
            inputs: list[tuple[int, int, str]] = []
            for row in rows:
                last = row_int(row, "id")
                text = row_text(row, "text")
                # Long belief content is explicitly split; the canonical belief and
                # its FTS projection remain whole and unchanged.
                offsets = self.encoder.offsets(text)
                for start in range(0, max(1, len(offsets)), 480):
                    end = min(start + 480, len(offsets))
                    fragment = text[offsets[start][0] : offsets[end - 1][1]] if offsets else text
                    inputs.append((row_int(row, "id"), row_int(row, "revision"), fragment))
            for start in range(0, len(inputs), 32):
                batch = inputs[start : start + 32]
                vectors_by_hash: dict[str, bytes] = {}
                lookup = previous if previous is not None else target
                hashes = [
                    sha256(
                        (self.encoder.specification.document_prefix + item[2]).encode()
                    ).hexdigest()
                    for item in batch
                ]
                for input_hash in dict.fromkeys(hashes):
                    record = lookup.execute(
                        "SELECT id FROM embedding_records WHERE input_sha256=? "
                        "AND mode='document' LIMIT 1",
                        (input_hash,),
                    ).fetchone()
                    if record is not None:
                        row = lookup.execute(
                            "SELECT embedding FROM vec_documents WHERE rowid=?",
                            (row_int(record, "id"),),
                        ).fetchone()
                        if row is not None and isinstance(row["embedding"], bytes):
                            vectors_by_hash[input_hash] = row["embedding"]
                missing = {
                    input_hash: item[2]
                    for input_hash, item in zip(hashes, batch, strict=True)
                    if input_hash not in vectors_by_hash
                }
                fresh = self.encoder.encode(tuple(missing.values())) if missing else ()
                if len(fresh) != len(missing):
                    raise IndexError()
                vectors_by_hash.update(zip(missing, fresh, strict=True))
                encoded = tuple(vectors_by_hash[input_hash] for input_hash in hashes)
                # No embedding call executes inside a SQLite write transaction.
                target.execute("BEGIN IMMEDIATE")
                try:
                    for (document_id, revision, text), vector in zip(batch, encoded, strict=True):
                        validate_vector(vector)
                        row = target.execute(
                            "INSERT INTO "
                            "embedding_records(document_id,input_sha256,input_tokens,target_revision,"
                            "mode,encoded_at) VALUES(?,?,?,?,?,?)",
                            (
                                document_id,
                                sha256(
                                    (self.encoder.specification.document_prefix + text).encode()
                                ).hexdigest(),
                                self.encoder.token_count(text),
                                revision,
                                "document",
                                timestamp_from_datetime(self.engine.clock.now()),
                            ),
                        )
                        header = target.execute(
                            "SELECT * FROM documents WHERE id=?", (document_id,)
                        ).fetchone()
                        active = int(
                            row_int(header, "recall_eligible") == 1
                            and row_int(header, "is_current") == 1
                            and header["lifecycle"] == "active"
                        )
                        target.execute(
                            "INSERT INTO vec_documents(rowid,embedding,active,valid_from,"
                            "valid_until,"
                            "document_id) VALUES(?,?,?,?,?,?)",
                            (
                                row.lastrowid,
                                vector,
                                active,
                                header["valid_from_us"]
                                if header["valid_from_us"] is not None
                                else -(2**63),
                                header["valid_until_us"]
                                if header["valid_until_us"] is not None
                                else 2**63 - 1,
                                document_id,
                            ),
                        )
                    target.commit()
                except BaseException:
                    target.rollback()
                    raise
