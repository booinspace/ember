"""Explicit composition/maintenance owner for registry, library and character stores."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from ember_memory_abi.archive import SourceRecord
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
from .database import SqliteDatabase, load_model, model_bytes, payload_hash, row_text
from .errors import CorruptStoreError, EngineClosedError
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
    ) -> None:
        self.root = validate_local_root(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()
        self.token_counter = token_counter or Utf8ByteBudget()
        self.max_pending = max_pending
        self._guard = RLock()
        self._closed = False
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

        info, database = self._store(character_id)
        if info.kind != "character":
            raise AccessDeniedError()
        return SqliteMemoryStore(
            database, self, authority or MutationAuthority(), self.token_counter
        )

    def resolve(self, reference: SourceRef) -> SourceRecord:
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
                connection.execute("PRAGMA query_only=ON")
                row = connection.execute(
                    (
                        "SELECT payload_json,payload_sha256 FROM source_records WHERE "
                        "record_kind=? AND record_id=? AND revision=?"
                    ),
                    (reference.record_kind, str(reference.record_id), reference.record_revision),
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

    def validate(self, evidence: Evidence) -> str:
        """Verify exact evidence against original normalized source/part, never a summary or
        concatenation.
        """
        try:
            record = self.resolve(evidence.source)
        except SourceUnavailableError:
            _, database = self._store(evidence.source.source_store_id)

            def quote(connection: sqlite3.Connection) -> str:
                row = connection.execute(
                    "SELECT * FROM materialized_evidence WHERE evidence_id=?",
                    (str(evidence.evidence_id),),
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
                for alias in (snapshot.name, *snapshot.aliases):
                    connection.execute(
                        "INSERT OR IGNORE INTO entity_aliases VALUES(?,?)",
                        (str(snapshot.reference.entity_id), alias),
                    )

        database.write(write)

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
                    (
                        "INSERT INTO "
                        "index_outbox(kind,record_id,revision,policy_epoch,recorded_at_us) "
                        "SELECT 'grant_changed',NULL,NULL,policy_epoch,? FROM store_metadata"
                    ),
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
                    (
                        "INSERT INTO "
                        "index_outbox(kind,record_id,revision,policy_epoch,recorded_at_us) "
                        "SELECT 'grant_revoked',NULL,NULL,policy_epoch,? FROM store_metadata"
                    ),
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
        for kind in ("character", "library"):
            for info, database in stores:
                if info.kind == kind:
                    database.close()
        self.registry.close()

    def __enter__(self) -> "CanonicalEngine":
        """Own explicit composition lifetime; this API is blocking."""
        return self

    def __exit__(self, *_: object) -> None:
        """Drain all owned work without suppressing the caller's exception."""
        self.close()
