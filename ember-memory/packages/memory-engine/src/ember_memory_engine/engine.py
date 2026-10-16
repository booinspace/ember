"""Explicit composition/maintenance owner for registry, library and character stores."""

import json
import sqlite3
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from ember_memory_abi.archive import Conversation, GraphNode, Message, SourceRecord
from ember_memory_abi.common import CanonicalId, canonical_object_sha256, timestamp_from_datetime
from ember_memory_abi.errors import (
    AccessDeniedError,
    InvalidEvidenceError,
    RevisionConflictError,
    SourceUnavailableError,
)
from ember_memory_abi.evidence import Evidence, SourceRef, validate_evidence
from ember_memory_abi.memory import Entity, MemorySource

from .archive import SqliteArchiveStore, source_text
from .database import SqliteDatabase, load_model, model_bytes, payload_hash, row_int, row_text
from .embedding import EncodingPool
from .errors import CorruptStoreError, EngineBusyError, EngineClosedError, IndexError
from .index_storage import IndexStore
from .models import (
    Clock,
    MutationAuthority,
    SourceGrant,
    StoreInfo,
    SystemClock,
    TokenCounter,
    Utf8ByteBudget,
)
from .ownership import validate_local_root
from .read_budget import ReadBudget, bounded_connection
from .search_contracts import Encoder, IndexGeneration
from .vector_search import VectorPool
from .workers import OwnedWorkers

if TYPE_CHECKING:
    from .character import SqliteMemoryStore


class CanonicalEngine:
    """Blocking composition owner; construct/close off the realtime loop.

    Stores use local registry/libraries/characters paths and one writer owner each.
    Source/character consistency uses immutable sources plus current policy checks,
    not a cross-database transaction. Ordinary runtime code uses injected ABI clients.
    """

    def __init__(
        self,
        root: Path,
        *,
        clock: Clock | None = None,
        token_counter: TokenCounter | None = None,
        max_pending: int = 16,
        encoder: Encoder | None = None,
        auto_refresh: bool = False,
    ) -> None:
        self.root = validate_local_root(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()
        self.token_counter = token_counter or Utf8ByteBudget()
        self.max_pending = max_pending
        self._guard = RLock()
        self._closed = False
        self.auto_refresh = auto_refresh
        self._refreshes: dict[CanonicalId, Future[IndexGeneration]] = {}
        self._dirty_indexes: set[CanonicalId] = set()
        self._processing_active: set[CanonicalId] = set()
        self.index_error_count = 0
        self.encoding = EncodingPool(encoder)
        self.fast_vectors = VectorPool(name="ember-fast-vectors", cache_mib=192)
        self.deep_vectors = VectorPool(name="ember-deep-vectors", cache_mib=64)
        self.index_writer = OwnedWorkers(1, max_pending=2, name="ember-derived-writer")
        self.indexes: dict[CanonicalId, IndexStore] = {}
        self._stores: dict[CanonicalId, tuple[StoreInfo, SqliteDatabase]] = {}
        self._unavailable_libraries: dict[CanonicalId, StoreInfo] = {}
        self._source_adapter: TypeAdapter[SourceRecord] = TypeAdapter(SourceRecord)
        registry_path = self.root / "registry.db"
        registry_id = uuid4()
        if registry_path.exists():
            try:
                with closing(
                    sqlite3.connect(registry_path.as_uri() + "?mode=ro", uri=True)
                ) as connection:
                    row = connection.execute("SELECT store_id FROM store_metadata").fetchone()
                    if row is None or not isinstance(row[0], str):
                        raise CorruptStoreError()
                    registry_id = UUID(row[0])
                    if registry_id.version != 4:
                        raise CorruptStoreError()
            except (sqlite3.Error, ValueError) as error:
                raise CorruptStoreError() from error
        self.registry = SqliteDatabase(
            registry_path,
            registry_id,
            "registry",
            "ember-registry",
            self.clock,
            max_pending=max_pending,
        )
        try:
            infos = self.registry.read(self._registered)
            for info in infos:
                if (
                    info.kind == "library"
                    and not (self.root / "libraries" / str(info.store_id) / "memory.db").is_file()
                ):
                    self._unavailable_libraries[info.store_id] = info.model_copy(
                        update={"availability": "unavailable"}
                    )
                    continue
                self._open(info, require_existing=True)
        except Exception:
            self.close()
            raise

    def _registered(self, connection: sqlite3.Connection) -> tuple[StoreInfo, ...]:
        infos: list[StoreInfo] = []
        for row in connection.execute(
            "SELECT store_id,kind,namespace FROM registry_stores ORDER BY store_id"
        ):
            try:
                info = StoreInfo.model_validate_json(
                    json.dumps(
                        {
                            "store_id": row_text(row, "store_id"),
                            "kind": row_text(row, "kind"),
                            "namespace": row_text(row, "namespace"),
                        }
                    )
                )
            except ValidationError as error:
                raise CorruptStoreError() from error
            infos.append(info)
        return tuple(infos)

    def _open(self, info: StoreInfo, *, require_existing: bool = False) -> SqliteDatabase:
        directory = "libraries" if info.kind == "library" else "characters"
        path = self.root / directory / str(info.store_id) / "memory.db"
        if require_existing and not path.is_file():
            raise CorruptStoreError()
        database = SqliteDatabase(
            path,
            info.store_id,
            info.kind,
            info.namespace,
            self.clock,
            max_pending=self.max_pending,
        )
        self._stores[info.store_id] = (info, database)
        self.indexes[info.store_id] = IndexStore(database.path, info.store_id)
        database.on_write = lambda: self._queue_refresh(info.store_id)
        return database

    def _create(self, info: StoreInfo) -> StoreInfo:
        with self._guard:
            if self._closed:
                raise EngineClosedError()
            for old in self.stores():
                if old.store_id == info.store_id or (old.kind, old.namespace) == (
                    info.kind,
                    info.namespace,
                ):
                    raise RevisionConflictError()
            database = self._open(info)
            try:

                def register(connection: sqlite3.Connection) -> None:
                    with self.registry.transaction(connection):
                        connection.execute(
                            "INSERT INTO registry_stores VALUES(?,?,?)",
                            (str(info.store_id), info.kind, info.namespace),
                        )

                self.registry.write(register)
            except Exception:
                self._stores.pop(info.store_id, None)
                database.close()
                raise
        return info

    def create_library(self, namespace: str, *, store_id: CanonicalId | None = None) -> StoreInfo:
        """Create an explicitly named library; never infer equivalence or merge accounts."""
        if not namespace.strip():
            raise ValueError("A nonblank source namespace is required.")
        return self._create(
            StoreInfo(store_id=store_id or uuid4(), kind="library", namespace=namespace)
        )

    def create_character(self, *, character_id: CanonicalId | None = None) -> StoreInfo:
        """Create independent canonical identity/state; no seeds, grants or memories are implied."""
        identity = character_id or uuid4()
        return self._create(
            StoreInfo(store_id=identity, kind="character", namespace=f"character:{identity}")
        )

    def stores(self) -> tuple[StoreInfo, ...]:
        """Return registered identities without physical locators."""
        with self._guard:
            return (
                *(info for info, _ in self._stores.values()),
                *self._unavailable_libraries.values(),
            )

    def _store(self, identity: CanonicalId) -> tuple[StoreInfo, SqliteDatabase]:
        with self._guard:
            item = self._stores.get(identity)
        if item is None:
            raise SourceUnavailableError()
        return item

    def acquire_processing(self, job_id: CanonicalId) -> Callable[[], None]:
        """Prevent duplicate concurrent runners in this root; keep admission through drain."""
        from .processing_contracts import ProcessingError

        with self._guard:
            if self._closed:
                raise EngineClosedError()
            if self._processing_active:
                raise ProcessingError("job_already_running")
            self._processing_active.add(job_id)

        def release() -> None:
            with self._guard:
                self._processing_active.discard(job_id)

        return release

    def maintenance_store(self, identity: CanonicalId) -> tuple[StoreInfo, SqliteDatabase]:
        """Engine-internal maintenance access; never expose this capability to runtime tools."""
        return self._store(identity)

    def archive(self, store_id: CanonicalId) -> SqliteArchiveStore:
        """Construct a trusted maintenance archive writer; never expose it to model/harness
        tools.
        """
        info, database = self._store(store_id)
        return SqliteArchiveStore(database, info.namespace)

    def character(
        self, character_id: CanonicalId, *, authority: MutationAuthority | None = None
    ) -> "SqliteMemoryStore":
        """Bind a synchronous ABI client through trusted composition, read-only by default."""
        from .character import SqliteMemoryStore
        from .indexed_retrieval import IndexedRetriever

        info, database = self._store(character_id)
        if info.kind != "character":
            raise AccessDeniedError()
        return SqliteMemoryStore(
            database,
            self,
            authority or MutationAuthority(),
            self.token_counter,
            IndexedRetriever(
                character_id, self.indexes, self.encoding, self.fast_vectors, self.deep_vectors
            ),
        )

    def resolve(
        self,
        reference: SourceRef,
        *,
        max_payload_bytes: int | None = None,
        read_budget: ReadBudget | None = None,
    ) -> SourceRecord:
        """Resolve exact immutable source bytes on this caller's owned thread, without worker
        recursion.
        """
        _, database = self._store(reference.source_store_id)
        try:
            with closing(
                sqlite3.connect(
                    database.path.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=2
                )
            ) as connection:
                connection.row_factory = sqlite3.Row
                with bounded_connection(connection, read_budget):
                    connection.execute("PRAGMA query_only=ON")
                    if max_payload_bytes is not None:
                        connection.execute("PRAGMA cache_size=-1024")
                    row = connection.execute(
                        "SELECT payload_json,payload_sha256 FROM source_records WHERE "
                        "record_kind=? AND record_id=? AND revision=? "
                        "AND (? IS NULL OR length(CAST(payload_json AS BLOB))<=?)",
                        (
                            reference.record_kind,
                            str(reference.record_id),
                            reference.record_revision,
                            max_payload_bytes,
                            max_payload_bytes,
                        ),
                    ).fetchone()
                    if row is None:
                        raise SourceUnavailableError()
                    payload = row_text(row, "payload_json")
                    if payload_hash(payload) != row_text(row, "payload_sha256"):
                        raise CorruptStoreError()
                    record = self._source_adapter.validate_json(payload)
        except (sqlite3.Error, ValidationError) as error:
            raise SourceUnavailableError() from error
        if record.source.identity_key()[:4] != reference.identity_key()[:4]:
            raise CorruptStoreError()
        return record

    def message_branch_status(
        self, message: Message, conversation: Conversation, *, read_budget: ReadBudget | None = None
    ) -> str:
        """Use exact selected-path keys, reading only its first missing or matching node.

        The immutable conversation and matching node are hash-verified. Missing
        earlier nodes retain the legacy ambiguous outcome. No grant is inferred.
        Large paths remain explicitly unavailable within the deep read envelope.
        """
        selection = conversation.selection
        if selection is None or selection.status != "resolved":
            return "ambiguous"
        path = selection.path
        if len(path) > 1024:
            return "unavailable"
        if not path:
            return "alternative"
        if conversation.source.identity_key() != message.conversation.identity_key():
            raise CorruptStoreError()
        _, database = self._store(message.source.source_store_id)
        values = ",".join("(?,?,?,?)" for _ in path)
        parameters = tuple(
            value
            for ordinal, ref in enumerate(path)
            for value in (ordinal, ref.record_kind, str(ref.record_id), ref.record_revision)
        )
        try:
            with closing(
                sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True)
            ) as connection:
                connection.row_factory = sqlite3.Row
                with bounded_connection(connection, read_budget):
                    row = connection.execute(
                        "WITH requested(ordinal,kind,id,rev) AS (VALUES " + values + ") "
                        "SELECT q.ordinal,s.payload_json,s.payload_sha256 FROM requested q "
                        "LEFT JOIN source_records s ON s.record_kind=q.kind "
                        "AND s.record_id=q.id AND s.revision=q.rev "
                        "WHERE s.record_id IS NULL OR "
                        "(json_extract(s.payload_json,'$.message.record_id')=? "
                        "AND json_extract(s.payload_json,'$.message.record_revision')=?) "
                        "ORDER BY q.ordinal LIMIT 1",
                        (
                            *parameters,
                            str(message.source.record_id),
                            message.source.record_revision,
                        ),
                    ).fetchone()
                    if row is None:
                        return "alternative"
                    if row["payload_json"] is None:
                        return "ambiguous"
                    node = load_model(row, GraphNode)
                    if (
                        node.source.identity_key() != path[row_int(row, "ordinal")].identity_key()
                        or node.conversation.identity_key() != message.conversation.identity_key()
                        or node.message is None
                        or node.message.identity_key() != message.source.identity_key()
                    ):
                        raise CorruptStoreError()
                    return "selected"
        except sqlite3.Error as error:
            if read_budget is not None:
                read_budget.check()
            raise SourceUnavailableError() from error

    def unavailable_sources(
        self,
        references: tuple[SourceRef, ...],
        *,
        local_connection: sqlite3.Connection,
        local_store_id: CanonicalId,
        call_budget: int,
        read_budget: ReadBudget | None = None,
    ) -> tuple[tuple[SourceRef, ...], int]:
        "Batch exact/current source headers by store; return missing refs without"
        " source bodies."
        if len(references) > 256:
            raise ValueError("Source header bound exceeded.")
        grouped: dict[CanonicalId, list[SourceRef]] = {}
        for reference in references:
            grouped.setdefault(reference.source_store_id, []).append(reference)
        calls = 0
        missing: list[SourceRef] = []

        def unavailable(
            connection: sqlite3.Connection, refs: list[SourceRef]
        ) -> tuple[SourceRef, ...]:
            values = ",".join("(?,?,?,?)" for _ in refs)
            parameters = tuple(
                x
                for ref in refs
                for x in (
                    ref.record_kind,
                    str(ref.record_id),
                    ref.record_revision,
                    str(ref.part_id) if ref.part_id else "",
                )
            )
            rows = connection.execute(
                "WITH requested(kind,id,rev,part) AS (VALUES "
                + values
                + ") SELECT q.* FROM requested q WHERE NOT ("
                "EXISTS(SELECT 1 FROM source_records s WHERE s.record_kind=q.kind"
                " AND s.record_id=q.id AND s.revision=q.rev "
                "AND NOT EXISTS(SELECT 1 FROM source_records n WHERE "
                "n.record_kind=q.kind AND n.record_id=q.id AND n.revision>q.rev) "
                "AND (q.part='' OR EXISTS(SELECT 1 FROM message_parts p WHERE "
                "p.record_kind=q.kind AND p.record_id=q.id "
                "AND p.revision=q.rev AND p.part_id=q.part AND p.status='available'))) "
                "OR (NOT EXISTS(SELECT 1 FROM source_records r WHERE "
                "r.record_kind=q.kind AND r.record_id=q.id) AND "
                "EXISTS(SELECT 1 FROM materialized_evidence e WHERE "
                "json_extract(e.payload_json,'$.evidence.source.record_kind')=q.kind AND "
                "json_extract(e.payload_json,'$.evidence.source.record_id')=q.id AND "
                "json_extract(e.payload_json,'$.evidence.source.record_revision')=q.rev AND "
                "json_extract(e.payload_json,'$.evidence.source.part_id')=q.part AND "
                "json_extract(e.payload_json,'$.availability')='available')))",
                parameters,
            ).fetchall()
            absent = {(row[0], row[1], row[2], row[3]) for row in rows}
            return tuple(
                ref
                for ref in refs
                if (
                    ref.record_kind,
                    str(ref.record_id),
                    ref.record_revision,
                    str(ref.part_id) if ref.part_id else "",
                )
                in absent
            )

        for store_id, refs in grouped.items():
            if store_id == local_store_id:
                missing.extend(unavailable(local_connection, refs))
                continue
            if calls >= call_budget:
                missing.extend(refs)
                continue
            calls += 1
            try:
                info, database = self._store(store_id)
                if info.availability != "available":
                    missing.extend(refs)
                    continue
                with closing(
                    sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True, timeout=2)
                ) as connection:
                    with bounded_connection(connection, read_budget):
                        missing.extend(unavailable(connection, refs))
            except (sqlite3.Error, SourceUnavailableError):
                missing.extend(refs)
        return tuple(missing), calls

    def validate(
        self,
        evidence: Evidence,
        *,
        max_payload_bytes: int | None = None,
        max_quote_codepoints: int | None = None,
        read_budget: ReadBudget | None = None,
    ) -> str:
        """Verify exact evidence against original normalized source/part, never a summary or
        concatenation.
        """
        if max_quote_codepoints is not None and (
            evidence.span.end_codepoint - evidence.span.start_codepoint > max_quote_codepoints
        ):
            raise SourceUnavailableError()
        try:
            record = (
                self.resolve(evidence.source)
                if max_payload_bytes is None
                else self.resolve(
                    evidence.source, max_payload_bytes=max_payload_bytes, read_budget=read_budget
                )
            )
        except SourceUnavailableError:
            _, database = self._store(evidence.source.source_store_id)

            def quote(connection: sqlite3.Connection) -> str:
                row = connection.execute(
                    "SELECT * FROM materialized_evidence WHERE evidence_id=? "
                    "AND (? IS NULL OR length(CAST(payload_json AS BLOB))<=?)",
                    (str(evidence.evidence_id), max_payload_bytes, max_payload_bytes),
                ).fetchone()
                if row is None:
                    raise SourceUnavailableError()
                materialized = load_model(row, MemorySource)
                if materialized.evidence != evidence or materialized.quote is None:
                    raise InvalidEvidenceError()
                return materialized.quote

            try:
                with closing(
                    sqlite3.connect(database.path.as_uri() + "?mode=ro", uri=True)
                ) as connection:
                    connection.row_factory = sqlite3.Row
                    connection.execute("PRAGMA query_only=ON")
                    if max_payload_bytes is not None:
                        connection.execute("PRAGMA cache_size=-1024")
                    with bounded_connection(connection, read_budget):
                        return quote(connection)
            except sqlite3.Error as error:
                raise SourceUnavailableError() from error
        reference = SourceRef.model_validate(
            {**record.source.model_dump(), "part_id": evidence.source.part_id}
        )
        return validate_evidence(
            evidence, source=reference, text=source_text(record, evidence.source)
        )

    def persist_evidence(
        self,
        evidence: Evidence,
        *,
        local_connection: sqlite3.Connection | None = None,
        local_store_id: CanonicalId | None = None,
    ) -> None:
        """Store evidence with its original source; orphan immutable spans are safe on character
        rollback.
        """
        self.validate(evidence)
        _, database = self._store(evidence.source.source_store_id)

        def persist(connection: sqlite3.Connection) -> None:
            old = connection.execute(
                "SELECT payload_json,payload_sha256 FROM evidence_spans WHERE evidence_id=?",
                (str(evidence.evidence_id),),
            ).fetchone()
            if old is not None:
                existing = load_model(old, Evidence)
                if canonical_object_sha256(existing) != canonical_object_sha256(evidence):
                    raise InvalidEvidenceError()
                return
            source, span = evidence.source, evidence.span
            payload = model_bytes(evidence)
            connection.execute(
                "INSERT INTO evidence_spans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(evidence.evidence_id),
                    source.record_kind,
                    str(source.record_id),
                    source.record_revision,
                    str(source.part_id),
                    span.start_codepoint,
                    span.end_codepoint,
                    span.start_byte,
                    span.end_byte,
                    span.source_content_sha256,
                    span.quoted_span_sha256,
                    payload,
                    payload_hash(payload),
                ),
            )

        if evidence.source.source_store_id == local_store_id and local_connection is not None:
            persist(local_connection)
        else:

            def commit(connection: sqlite3.Connection) -> None:
                with database.transaction(connection):
                    persist(connection)

            database.write(commit)

    def put_entity(self, character_id: CanonicalId, entity: Entity) -> None:
        """Trusted maintenance registers owned entities/revisions; never automatically merge
        aliases.
        """
        self.put_owned_entity(character_id, entity, character_only=True)

    def put_owned_entity(
        self, character_id: CanonicalId, entity: Entity, *, character_only: bool = False
    ) -> None:
        """Register a trusted store-owned entity; public character calls retain their kind check."""
        info, database = self._store(character_id)
        if (
            character_only and info.kind != "character"
        ) or entity.reference.store_id != character_id:
            raise AccessDeniedError()
        snapshot = Entity.model_validate_json(entity.model_dump_json())

        def write(connection: sqlite3.Connection) -> None:
            with database.transaction(connection):
                old = connection.execute(
                    "SELECT payload_json,payload_sha256 FROM entities WHERE entity_id=? "
                    "AND revision=?",
                    (str(snapshot.reference.entity_id), snapshot.revision),
                ).fetchone()
                if old is not None:
                    if canonical_object_sha256(load_model(old, Entity)) != canonical_object_sha256(
                        snapshot
                    ):
                        raise RevisionConflictError()
                    return
                latest: object = connection.execute(
                    "SELECT MAX(revision) FROM entities WHERE entity_id=?",
                    (str(snapshot.reference.entity_id),),
                ).fetchone()[0]
                if snapshot.revision != (latest + 1 if isinstance(latest, int) else 1):
                    raise RevisionConflictError()
                payload = model_bytes(snapshot)
                connection.execute(
                    "INSERT INTO entities VALUES(?,?,?,?)",
                    (
                        str(snapshot.reference.entity_id),
                        snapshot.revision,
                        payload,
                        payload_hash(payload),
                    ),
                )
                _, stamp = database.recorded_stamp(connection)
                connection.execute(
                    "INSERT INTO index_outbox(kind,record_id,revision,policy_epoch,recorded_at_us) "
                    "SELECT ?,?,?,policy_epoch,? FROM store_metadata",
                    ("entity_changed", str(snapshot.reference.entity_id), snapshot.revision, stamp),
                )
                for alias in (snapshot.name, *snapshot.aliases):
                    connection.execute(
                        "INSERT OR IGNORE INTO entity_aliases VALUES(?,?)",
                        (str(snapshot.reference.entity_id), alias),
                    )

        database.write(write)

    def rebuild_index(
        self, store_id: CanonicalId, encoder: Encoder, *, reuse: bool = False
    ) -> IndexGeneration:
        """Explicit blocking maintenance on the separate derived writer; never startup work."""
        from .index_builder import IndexBuilder

        return self.index_writer.submit(
            lambda: IndexBuilder(self, encoder).rebuild(store_id, reuse=reuse)
        ).result()

    def update_index(self, store_id: CanonicalId, encoder: Encoder) -> IndexGeneration:
        """Explicit blocking outbox replay on the separate derived writer."""
        from .index_updates import update

        return self.index_writer.submit(lambda: update(self, encoder, store_id)).result()

    @property
    def index_jobs_pending(self) -> int:
        """Coalesced store refreshes still owned by the derived writer."""
        with self._guard:
            return len(self._refreshes)

    def _queue_refresh(self, store_id: CanonicalId) -> None:
        # Called only after a successful canonical write. A missing/incompatible
        # index requires explicit setup, never an automatic full startup rebuild.
        encoder = self.encoding.encoder
        if not self.auto_refresh or encoder is None:
            return
        with self._guard:
            if self._closed:
                return
            if store_id in self._refreshes:
                self._dirty_indexes.add(store_id)
                return
            try:
                generation, _ = self.indexes[store_id].active()
                if (
                    generation.layout != "ember.search-layout@0.6.0"
                    or generation.embedding != encoder.specification
                ):
                    return
                from .index_updates import update

                future = self.index_writer.submit(lambda: update(self, encoder, store_id))
            except (IndexError, EngineBusyError, EngineClosedError):
                # The durable outbox is still pending; later writes/explicit update
                # can catch up. Search reports pending coverage immediately.
                return
            self._refreshes[store_id] = future

            def completed(job: Future[IndexGeneration]) -> None:
                error = None if job.cancelled() else job.exception()
                with self._guard:
                    self._refreshes.pop(store_id, None)
                    retry = store_id in self._dirty_indexes
                    self._dirty_indexes.discard(store_id)
                    if error is not None:
                        self.index_error_count += 1
                    if retry and not self._closed:
                        self._queue_refresh(store_id)

            future.add_done_callback(completed)

    def grant_source(self, character_id: CanonicalId, grant: SourceGrant) -> None:
        """Grant explicit existing snapshots only; library/native capabilities cannot cross
        characters.
        """
        info, database = self._store(character_id)
        if info.kind != "character" or not grant.capabilities:
            raise AccessDeniedError()
        grant = SourceGrant.model_validate_json(grant.model_dump_json())
        if grant.extensions:
            raise AccessDeniedError()
        if grant.expires_at is not None and grant.expires_at <= timestamp_from_datetime(
            self.clock.now()
        ):
            raise AccessDeniedError()
        for reference in grant.references:
            source_info, _ = self._store(reference.source_store_id)
            if source_info.kind == "character" and reference.source_store_id != character_id:
                raise AccessDeniedError()
            record = self.resolve(reference)
            if reference.part_id is not None:
                source_text(record, reference)
                if "archive_search" in grant.capabilities:
                    raise AccessDeniedError()

        def write(connection: sqlite3.Connection) -> None:
            with database.transaction(connection):
                for reference in grant.references:
                    for capability in grant.capabilities:
                        connection.execute(
                            "INSERT INTO source_grants VALUES(?,?,?,?,?,?,?) ON CONFLICT "
                            "DO UPDATE SET expires_at=excluded.expires_at",
                            (
                                str(reference.source_store_id),
                                reference.record_kind,
                                str(reference.record_id),
                                reference.record_revision,
                                str(reference.part_id) if reference.part_id else "",
                                capability,
                                grant.expires_at,
                            ),
                        )
                connection.execute("UPDATE store_metadata SET policy_epoch=policy_epoch+1")
                _, stamp = database.recorded_stamp(connection)
                connection.execute(
                    "INSERT INTO "
                    "index_outbox(kind,record_id,revision,policy_epoch,recorded_at_us) "
                    "SELECT 'grant_changed',NULL,NULL,policy_epoch,? FROM store_metadata",
                    (stamp,),
                )

                connection.execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?)",
                    (str(uuid4()), "grant_source", None, stamp, "trusted_maintenance"),
                )

        database.write(write)

    def revoke_grant(self, character_id: CanonicalId, grant: SourceGrant) -> None:
        """Revoke only selected exact snapshots/capabilities, retaining adopted beliefs.

        Revocation never resolves a source, so unavailable libraries can be revoked.
        It does not remove suppression or expand another character's permissions.
        """
        info, database = self._store(character_id)
        if info.kind != "character" or grant.extensions:
            raise AccessDeniedError()

        def write(connection: sqlite3.Connection) -> None:
            with database.transaction(connection):
                for reference in grant.references:
                    for capability in grant.capabilities:
                        connection.execute(
                            "DELETE FROM source_grants WHERE source_store_id=? "
                            "AND record_kind=? AND record_id=? AND record_revision=? "
                            "AND part_id=? AND capability=?",
                            (
                                str(reference.source_store_id),
                                reference.record_kind,
                                str(reference.record_id),
                                reference.record_revision,
                                str(reference.part_id) if reference.part_id else "",
                                capability,
                            ),
                        )
                connection.execute("UPDATE store_metadata SET policy_epoch=policy_epoch+1")
                _, stamp = database.recorded_stamp(connection)
                connection.execute(
                    "INSERT INTO index_outbox(kind,policy_epoch,recorded_at_us) "
                    "SELECT 'grant_revoked',policy_epoch,? FROM store_metadata",
                    (stamp,),
                )
                connection.execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?)",
                    (str(uuid4()), "revoke_grant", None, stamp, "trusted_maintenance"),
                )

        database.write(write)

    def revoke_source(self, character_id: CanonicalId, source_store_id: CanonicalId) -> None:
        """Revoke access without rewriting adopted beliefs or deleting shared source history."""
        info, database = self._store(character_id)
        if info.kind != "character":
            raise AccessDeniedError()

        def write(connection: sqlite3.Connection) -> None:
            with database.transaction(connection):
                connection.execute(
                    "DELETE FROM source_grants WHERE source_store_id=?", (str(source_store_id),)
                )
                connection.execute("UPDATE store_metadata SET policy_epoch=policy_epoch+1")
                _, stamp = database.recorded_stamp(connection)
                connection.execute(
                    "INSERT INTO "
                    "index_outbox(kind,record_id,revision,policy_epoch,recorded_at_us) "
                    "SELECT 'grant_revoked',NULL,NULL,policy_epoch,? FROM store_metadata",
                    (stamp,),
                )

        database.write(write)

    def close(self) -> None:
        """Drain character dependencies before library writers, then close registry/ownership."""
        with self._guard:
            if self._closed:
                return
            self._closed = True
            stores = tuple(self._stores.values())
        self.index_writer.shutdown()
        for kind in ("character", "library"):
            for info, database in stores:
                if info.kind == kind:
                    database.close()
        self.fast_vectors.close()
        self.deep_vectors.close()
        for index in self.indexes.values():
            try:
                index.prune()
            except (IndexError, sqlite3.Error, OSError):
                pass
        self.encoding.close()
        self.registry.close()

    def __enter__(self) -> "CanonicalEngine":
        """Own explicit composition lifetime; this API is blocking."""
        return self

    def __exit__(self, *_: object) -> None:
        """Drain all owned work without suppressing the caller's exception."""
        self.close()
