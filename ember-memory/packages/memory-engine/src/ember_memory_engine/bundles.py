"""Exclusive offline bundle publication/restore with bounded streaming integrity checks."""

import os
import shutil
import sqlite3
from collections.abc import Callable, Generator, Iterator
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from ember_memory_abi.archive import ProcessingSpecRef
from ember_memory_abi.common import CanonicalId, timestamp_from_datetime
from ember_memory_abi.errors import AccessDeniedError, NotFoundError, SourceUnavailableError
from ember_memory_abi.interfaces import MutationReceipt
from ember_memory_abi.memory import Memory, MemoryDraft, MemorySource
from ember_memory_abi.related import MemoryRelationship

from ._state import CharacterState
from .bundle_contracts import BundleFile, BundleManifest, StoreSnapshot
from .consolidation import NativeAcceptanceReceipt
from .database import SqliteDatabase, load_model, model_bytes, payload_hash, row_text
from .engine import CanonicalEngine
from .errors import BundleError, CorruptStoreError
from .library import KnowledgeSelection, LibraryService, entity_references
from .models import StoreInfo
from .ownership import validate_local_root
from .schema import (
    PHASE_EIGHT_SQL,
    PHASE_FIVE_SQL,
    PHASE_SEVEN_IMMUTABLE,
    PHASE_SEVEN_SQL,
    PHASE_SIX_IMMUTABLE,
    PHASE_SIX_SQL,
    SCHEMA_VERSION,
)
from .specifications import specification_rows


def _directory(info: StoreInfo) -> str:
    return ("libraries" if info.kind == "library" else "characters") + "/" + str(info.store_id)


def _hash(path: Path) -> tuple[int, str]:
    digest = sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _files(directory: Path) -> Iterator[Path]:
    # Walk without following symlinks/reparse ancestors. Never archive locks, WAL,
    # deployment credentials or derived indexes by traversing the whole data root.
    for current, directories, names in os.walk(directory, followlinks=False):
        parent = Path(current)
        for name in (*directories, *names):
            candidate = parent / name
            if candidate.is_symlink() or candidate.is_junction():
                raise BundleError()
        for name in sorted(names):
            yield parent / name


def _copy_file(source: Path, target: Path) -> None:
    # Flush sealed snapshots/retained assets before publishing the manifest. All
    # copying is bounded streaming; target is a fresh staging member.
    with source.open("rb") as original, target.open("xb") as output:
        shutil.copyfileobj(original, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def _copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        return
    if source.is_symlink() or source.is_junction() or not source.is_dir():
        raise BundleError()
    for path in _files(source):
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _copy_file(path, destination)


@contextmanager
def _publication(output: Path) -> Generator[Path]:
    destination = output.absolute()
    if destination.exists() or destination.is_symlink():
        raise BundleError()
    if any(part.is_symlink() or part.is_junction() for part in destination.parents):
        raise BundleError()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Same filesystem for the final rename; incomplete work never appears as a bundle.
    with TemporaryDirectory(prefix=".ember-bundle-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "payload"
        stage.mkdir()
        yield stage
        stage.rename(destination)


def _registry_snapshot(stage: Path, engine: CanonicalEngine, identities: set[CanonicalId]) -> None:
    """Back up only the bundle's catalog identities, without unrelated registry rows."""
    infos = tuple(info for info in engine.stores() if info.store_id in identities)
    if len(infos) != len(identities):
        raise BundleError()
    with TemporaryDirectory(prefix="ember-registry-snapshot-") as temporary:
        with SqliteDatabase(
            Path(temporary) / "registry.db",
            engine.registry.store_id,
            "registry",
            "ember-registry",
            engine.clock,
        ) as target:

            def read(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
                return tuple(
                    tuple(row) for row in connection.execute("SELECT * FROM schema_versions")
                )

            versions = engine.registry.read(read)

            def write(connection: sqlite3.Connection) -> None:
                with target.transaction(connection):
                    connection.execute("DELETE FROM schema_versions")
                    connection.executemany("INSERT INTO schema_versions VALUES(?,?)", versions)
                    connection.executemany(
                        "INSERT INTO registry_stores VALUES(?,?,?)",
                        tuple((str(info.store_id), info.kind, info.namespace) for info in infos),
                    )

            target.write(write)
        _copy_file(Path(temporary) / "registry.db", stage / "registry.db")
    remove_locks(stage)


def _manifest(
    stage: Path,
    engine: CanonicalEngine,
    *,
    kind: Literal["source", "character"],
    mode: Literal["linked", "self_contained"],
    stores: tuple[StoreSnapshot, ...],
    dependencies: tuple[CanonicalId, ...] = (),
    selection: tuple[CanonicalId, ...] = (),
    closure: tuple[MemorySource, ...] = (),
) -> BundleManifest:
    _registry_snapshot(stage, engine, {*(item.store.store_id for item in stores), *dependencies})
    members: list[BundleFile] = []
    for path in _files(stage):
        size, digest = _hash(path)
        members.append(
            BundleFile(path=path.relative_to(stage).as_posix(), size=size, sha256=digest)
        )
    manifest = BundleManifest(
        bundle_id=uuid4(),
        kind=kind,
        mode=mode,
        created_at=timestamp_from_datetime(engine.clock.now()),
        stores=stores,
        dependencies=dependencies,
        selected_memory_ids=selection,
        evidence_closure=tuple(item.evidence.source for item in closure),
        files=tuple(sorted(members, key=lambda item: item.path)),
    )
    if len(manifest.model_dump_json(indent=2).encode("utf-8")) > 2 * 1024 * 1024:
        raise BundleError()
    with (stage / "manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(manifest.model_dump_json(indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    return manifest


class BundleService:
    """Maintenance opens a fresh exclusive engine owner per operation.

    Do not run alongside chat: the existing root ownership lock rejects it. No
    external facade can submit jobs to this private owner, making DB/config/source
    snapshots coherent. Keeping ownership through publication is the source-deletion
    barrier. Blocking streaming work belongs off every realtime event loop.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def export_source(self, library_id: CanonicalId, output: Path) -> BundleManifest:
        """Back up one complete library once, including retained raw sources and manifests."""
        self._outside_root(output)
        try:
            with CanonicalEngine(self.root) as engine, _publication(output) as stage:
                info, database = engine.maintenance_store(library_id)
                if info.kind != "library" or self._coverage(database) != "full":
                    raise BundleError()
                folder = stage / _directory(info)
                folder.mkdir(parents=True)
                database.backup(folder / "memory.db")
                original = engine.root / _directory(info)
                for name in ("rawsource", "assets", "manifests", "specifications"):
                    _copy_tree(original / name, folder / name)
                return _manifest(
                    stage,
                    engine,
                    kind="source",
                    mode="linked",
                    stores=(StoreSnapshot(store=info, coverage="full"),),
                )
        except (OSError, sqlite3.Error, ValidationError) as error:
            raise BundleError() from error

    def export_character(
        self,
        character_id: CanonicalId,
        output: Path,
        *,
        mode: Literal["linked", "self_contained"] = "linked",
        memory_ids: tuple[CanonicalId, ...] | None = None,
    ) -> BundleManifest:
        """Publish full linked state or selected beliefs with exact-span evidence closure.

        A self-contained export never copies entire shared messages, raw ZIPs or
        unrelated native history. Original logical IDs/coordinates remain intact;
        materialized spans explicitly have partial coverage and no archive search.
        """
        self._outside_root(output)
        if mode not in {"linked", "self_contained"} or (
            mode == "linked" and memory_ids is not None
        ):
            raise BundleError()
        try:
            with CanonicalEngine(self.root) as engine, _publication(output) as stage:
                info, database = engine.maintenance_store(character_id)
                if info.kind != "character":
                    raise BundleError()
                folder = stage / _directory(info)
                folder.mkdir(parents=True)
                dependencies = self._dependencies(database)
                if mode == "linked":
                    if self._coverage(database) != "full":
                        raise BundleError()
                    database.backup(folder / "memory.db")
                    _copy_tree(engine.root / _directory(info) / "config", folder / "config")
                    return _manifest(
                        stage,
                        engine,
                        kind="character",
                        mode=mode,
                        stores=(StoreSnapshot(store=info, coverage=self._coverage(database)),),
                        dependencies=dependencies,
                    )
                selection = (
                    memory_ids
                    if memory_ids is not None
                    else database.read(lambda connection: tuple(self._memory_ids(connection)))
                )
                if len(selection) > 10000 or len(set(selection)) != len(selection):
                    raise BundleError()
                # Build canonical selected state on an owned target connection; never
                # delete immutable rows from a full backup to make a privacy projection.
                state = engine.character(character_id).state
                memories = database.read(
                    lambda connection: self._selected(connection, selection, state.read_memory)
                )
                closure = self._closure(engine, character_id, memories)
                with TemporaryDirectory(prefix="ember-selected-snapshot-") as temporary:
                    snapshot_path = Path(temporary) / "memory.db"
                    self._selected_database(database, snapshot_path, memories, closure)
                    _copy_file(snapshot_path, folder / "memory.db")
                self._selected_config(engine.root / _directory(info), folder, memories)
                stores = [StoreSnapshot(store=info, coverage="selected_character")]
                libraries = sorted(
                    {
                        item.evidence.source.source_store_id
                        for item in closure
                        if item.evidence.source.source_store_id != character_id
                    },
                    key=str,
                )
                for identity in libraries:
                    source_info, _ = engine.maintenance_store(identity)
                    if source_info.kind != "library":
                        raise AccessDeniedError()
                    target = stage / _directory(source_info)
                    target.mkdir(parents=True)
                    with TemporaryDirectory(prefix="ember-evidence-snapshot-") as temporary:
                        with SqliteDatabase(
                            Path(temporary) / "memory.db",
                            identity,
                            "library",
                            source_info.namespace,
                            engine.clock,
                        ) as projection:
                            self._materialize(
                                projection,
                                tuple(
                                    item
                                    for item in closure
                                    if item.evidence.source.source_store_id == identity
                                ),
                                "evidence_only",
                            )
                        _copy_file(Path(temporary) / "memory.db", target / "memory.db")
                    stores.append(StoreSnapshot(store=source_info, coverage="evidence_only"))
                remove_locks(stage)
                return _manifest(
                    stage,
                    engine,
                    kind="character",
                    mode=mode,
                    stores=tuple(stores),
                    selection=selection,
                    closure=closure,
                )
        except (OSError, sqlite3.Error, ValidationError) as error:
            raise BundleError() from error

    def _outside_root(self, output: Path) -> None:
        destination, root = output.resolve(), self.root.resolve()
        if destination.is_relative_to(root) or root.is_relative_to(destination):
            raise BundleError()

    @staticmethod
    def _coverage(
        database: SqliteDatabase,
    ) -> Literal["full", "evidence_only", "selected_character"]:
        value = database.read(
            lambda connection: row_text(
                connection.execute("SELECT coverage FROM store_coverage").fetchone(), "coverage"
            )
        )
        if value not in ("full", "evidence_only", "selected_character"):
            raise CorruptStoreError()
        return value

    @staticmethod
    def _memory_ids(connection: sqlite3.Connection) -> Iterator[CanonicalId]:
        from uuid import UUID

        for row in connection.execute("SELECT memory_id FROM memories ORDER BY memory_id"):
            yield UUID(row_text(row, "memory_id"))

    @staticmethod
    def _dependencies(database: SqliteDatabase) -> tuple[CanonicalId, ...]:
        from uuid import UUID

        def read(connection: sqlite3.Connection) -> tuple[CanonicalId, ...]:
            rows = connection.execute(
                "SELECT source_store_id FROM memory_evidence "
                "UNION SELECT source_store_id FROM source_grants"
            )
            return tuple(
                sorted(
                    {
                        UUID(row_text(row, "source_store_id"))
                        for row in rows
                        if row_text(row, "source_store_id") != str(database.store_id)
                    },
                    key=str,
                )
            )

        return database.read(read)

    @staticmethod
    def _selected(
        connection: sqlite3.Connection,
        selection: tuple[CanonicalId, ...],
        read: Callable[..., Memory],
    ) -> tuple[Memory, ...]:
        result: list[Memory] = []
        size = 0
        for identity in selection:
            # Trusted backup preserves forgotten history/suppression, but evidence
            # closure below still enforces current source grants and suppression.
            latest = read(connection, identity, allow_suppressed=True)
            for revision in range(1, latest.revision + 1):
                memory = read(connection, identity, revision, allow_suppressed=True)
                size += len(memory.model_dump_json().encode("utf-8"))
                if size > 64 * 1024 * 1024 or len(result) >= 100000:
                    raise BundleError()
                result.append(memory)
        return tuple(result)

    @staticmethod
    def _closure(
        engine: CanonicalEngine, character_id: CanonicalId, memories: tuple[Memory, ...]
    ) -> tuple[MemorySource, ...]:
        state = engine.character(character_id).state

        def read(connection: sqlite3.Connection) -> tuple[MemorySource, ...]:
            epoch = state.policy_epoch(connection)
            result: dict[CanonicalId, MemorySource] = {}
            size = 0
            for memory in memories:
                assertions = list(memory.content.evidence)
                for support_row in connection.execute(
                    "SELECT * FROM memory_supports WHERE memory_id=? LIMIT 100001",
                    (str(memory.memory_id),),
                ):
                    support = load_model(support_row, MemoryRelationship)
                    assertions.extend(support.evidence)
                    if len(assertions) > 100000:
                        raise BundleError()
                for edge_row in connection.execute(
                    (
                        "SELECT * FROM memory_relationships WHERE source_id=? OR "
                        "target_id=? LIMIT 100001"
                    ),
                    (str(memory.memory_id), str(memory.memory_id)),
                ):
                    edge = load_model(edge_row, MemoryRelationship)
                    assertions.extend(edge.evidence)
                    if len(assertions) > 100000:
                        raise BundleError()
                for assertion in assertions:
                    evidence = assertion.evidence
                    if not state.source_permitted(connection, evidence.source, "evidence_read"):
                        # Do not export revoked/forgotten source bytes. The belief
                        # keeps its logical evidence ref and explicit unavailable state.
                        continue
                    quote = engine.validate(evidence)
                    item = MemorySource(evidence=evidence, availability="available", quote=quote)
                    if evidence.evidence_id in result and result[evidence.evidence_id] != item:
                        raise BundleError()
                    if evidence.evidence_id not in result:
                        size += len(quote.encode("utf-8"))
                        if size > 64 * 1024 * 1024 or len(result) >= 100000:
                            raise BundleError()
                    result[evidence.evidence_id] = item
            state.check_policy(connection, epoch)
            if any(
                not state.source_permitted(connection, item.evidence.source, "evidence_read")
                for item in result.values()
            ):
                raise AccessDeniedError()
            return tuple(result.values())

        return state.database.read(read)

    @staticmethod
    def _materialize(
        database: SqliteDatabase,
        closure: tuple[MemorySource, ...],
        coverage: Literal["evidence_only", "selected_character"],
    ) -> None:
        def write(connection: sqlite3.Connection) -> None:
            with database.transaction(connection):
                connection.execute("UPDATE store_coverage SET coverage=?", (coverage,))
                for item in closure:
                    payload = model_bytes(item)
                    connection.execute(
                        "INSERT INTO materialized_evidence VALUES(?,?,?)",
                        (str(item.evidence.evidence_id), payload, payload_hash(payload)),
                    )

        database.write(write)

    @staticmethod
    def _selected_config(original: Path, target: Path, memories: tuple[Memory, ...]) -> None:
        # The runtime's stable human subject is identity/configuration context,
        # even when selected imported beliefs refer to a separately seeded entity.
        source = original / "config" / "subject.json"
        if source.is_file():
            (target / "config").mkdir(exist_ok=True)
            _copy_file(source, target / "config" / "subject.json")
        # Personality is character-owned; provider deployment config is elsewhere.
        for name in ("personality.json", "personality.toml"):
            source = original / "config" / name
            if source.is_file():
                (target / "config").mkdir(exist_ok=True)
                _copy_file(source, target / "config" / name)

    @staticmethod
    def _selected_database(
        source: SqliteDatabase,
        path: Path,
        memories: tuple[Memory, ...],
        closure: tuple[MemorySource, ...],
    ) -> None:
        # Read/copy on the source owner with an isolated output connection. Only
        # canonical rows associated with selected IDs are transferred.
        from .schema import IMMUTABLE_TABLES, PHASE_FOUR_IMMUTABLE, PHASE_FOUR_SQL, SCHEMA_SQL

        selected = {str(memory.memory_id) for memory in memories}
        entities = {
            str(reference.entity_id)
            for memory in memories
            for reference in entity_references(MemoryDraft(content=memory.content))
        }

        subject_file = source.path.parent / "config" / "subject.json"
        if subject_file.is_file():
            from .authoring import SubjectBinding

            with subject_file.open("rb") as stream:
                data = stream.read(4097)
            if len(data) > 4096:
                raise BundleError()
            binding = SubjectBinding.model_validate_json(data)
            if binding.subject.store_id != source.store_id:
                raise BundleError()
            entities.add(str(binding.subject.entity_id))

        def copy(connection: sqlite3.Connection) -> None:
            policy = connection.execute("SELECT subject_id FROM profile_policy").fetchone()
            if policy and policy["subject_id"]:
                entities.add(row_text(policy, "subject_id"))
            with closing(sqlite3.connect(path)) as target:
                target.executescript(
                    SCHEMA_SQL
                    + PHASE_FOUR_SQL
                    + PHASE_FIVE_SQL
                    + PHASE_SIX_SQL
                    + PHASE_SEVEN_SQL
                    + PHASE_EIGHT_SQL
                )

                def transfer(
                    table: str, permitted: Callable[[sqlite3.Row], bool] | None = None
                ) -> None:
                    for row in connection.execute(f"SELECT * FROM {table}"):
                        if permitted is None or permitted(row):
                            target.execute(
                                f"INSERT INTO {table} VALUES(" + ",".join("?" for _ in row) + ")",
                                tuple(row),
                            )

                target.execute("DELETE FROM profile_policy")
                transfer("profile_policy")
                transfer("profile_exclusions")
                target.execute("DELETE FROM recall_policy")
                transfer("recall_policy")
                transfer("recall_libraries")
                target.execute("DELETE FROM capture_policy")
                transfer("capture_policy")
                for table in (
                    "store_metadata",
                    "schema_versions",
                    "predicate_definitions",
                    "suppression_targets",
                ):
                    transfer(table)
                references = tuple(
                    provenance.specification
                    for memory in memories
                    for provenance in memory.content.provenance
                )
                extra_specs: list[ProcessingSpecRef] = []
                for support_row in connection.execute("SELECT * FROM memory_supports"):
                    if row_text(support_row, "memory_id") in selected:
                        support = load_model(support_row, MemoryRelationship)
                        extra_specs.extend(p.specification for p in support.provenance)
                references = (*references, *extra_specs)
                native_rows: list[sqlite3.Row] = []
                for decision_row in connection.execute("SELECT * FROM native_acceptance"):
                    receipt = load_model(decision_row, NativeAcceptanceReceipt)
                    if receipt.memory and str(receipt.memory.memory_id) in selected:
                        native_rows.append(decision_row)
                        references = (*references, receipt.policy)
                for native_row in native_rows:
                    target.execute(
                        "INSERT INTO native_acceptance VALUES(?,?,?,?,?,?,?,?)", tuple(native_row)
                    )
                for definition in specification_rows(connection, references):
                    target.execute("INSERT INTO processing_specs VALUES(?,?,?)", tuple(definition))
                for table in (
                    "memories",
                    "memory_revisions",
                    "memory_evidence",
                    "memory_dependencies",
                    "pins",
                    "access_stats",
                    "claim_projection",
                    "memory_supports",
                ):
                    transfer(table, lambda row: row_text(row, "memory_id") in selected)
                transfer(
                    "memory_relationships",
                    lambda row: (
                        row_text(row, "source_id") in selected
                        and (row["target_id"] is None or row_text(row, "target_id") in selected)
                    ),
                )
                transfer(
                    "memory_supersessions",
                    lambda row: (
                        row_text(row, "old_memory_id") in selected
                        and row_text(row, "new_memory_id") in selected
                    ),
                )
                for table in ("entities", "entity_aliases", "entity_origins"):
                    transfer(table, lambda row: row_text(row, "entity_id") in entities)
                for table in ("mutation_keys", "maintenance_receipts"):

                    def allowed(row: sqlite3.Row, table: str = table) -> bool:
                        if table == "maintenance_receipts" and row_text(row, "operation") != "seed":
                            return False
                        receipt = MutationReceipt.model_validate_json(row_text(row, "receipt_json"))
                        return bool(receipt.affected) and all(
                            str(item.memory_id) in selected for item in receipt.affected
                        )

                    transfer(table, allowed)
                transfer(
                    "audit_events",
                    lambda row: row["record_id"] is None or row["record_id"] in selected,
                )
                transfer(
                    "index_outbox",
                    lambda row: row["record_id"] is None or row["record_id"] in selected,
                )
                permitted = {
                    (
                        str(ref.source_store_id),
                        ref.record_kind,
                        str(ref.record_id),
                        ref.record_revision,
                        str(ref.part_id) if ref.part_id else "",
                    )
                    for item in closure
                    for ref in (item.evidence.source,)
                }
                whole = {(*ref[:4], "") for ref in permitted}

                def allowed_grant(row: sqlite3.Row) -> bool:
                    key = (
                        row_text(row, "source_store_id"),
                        row_text(row, "record_kind"),
                        row_text(row, "record_id"),
                        row["record_revision"],
                        row_text(row, "part_id"),
                    )
                    return row_text(row, "capability") == "evidence_read" and (
                        key in permitted or key in whole
                    )

                transfer("source_grants", allowed_grant)
                target.execute("UPDATE store_coverage SET coverage='selected_character'")
                for item in closure:
                    if item.evidence.source.source_store_id == source.store_id:
                        payload = model_bytes(item)
                        target.execute(
                            "INSERT INTO materialized_evidence VALUES(?,?,?)",
                            (str(item.evidence.evidence_id), payload, payload_hash(payload)),
                        )
                for table in (
                    *IMMUTABLE_TABLES,
                    *PHASE_FOUR_IMMUTABLE,
                    *PHASE_SIX_IMMUTABLE,
                    *PHASE_SEVEN_IMMUTABLE,
                ):
                    for action in ("UPDATE", "DELETE"):
                        target.execute(
                            f"CREATE TRIGGER immutable_{table}_{action.lower()} "
                            f"BEFORE {action} ON {table} BEGIN "
                            "SELECT RAISE(ABORT, 'immutable canonical revision'); END"
                        )
                target.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                target.commit()
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise CorruptStoreError()

        source.read(copy)


def remove_locks(stage: Path) -> None:
    """Remove only closed staging-store lock/WAL sidecars before publication."""
    for path in _files(stage):
        if path.name.endswith(".owner.lock") or path.name.endswith(("-wal", "-shm")):
            path.unlink()


def _read_manifest(bundle: Path) -> BundleManifest:
    if any(part.is_symlink() or part.is_junction() for part in (bundle, *bundle.parents)):
        raise BundleError()
    manifest_file = bundle / "manifest.json"
    if manifest_file.is_symlink() or manifest_file.is_junction():
        raise BundleError()
    with manifest_file.open("rb") as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise BundleError()
    manifest = BundleManifest.model_validate_json(data)
    expected = {item.path for item in manifest.files} | {"manifest.json"}
    actual = {item.relative_to(bundle).as_posix() for item in _files(bundle)}
    if expected != actual:
        raise BundleError()
    prefixes = {_directory(item.store) for item in manifest.stores}
    for member in manifest.files:
        if member.path != "registry.db" and not any(
            member.path.startswith(prefix + "/") for prefix in prefixes
        ):
            raise BundleError()
        if member.path.endswith(("-wal", "-shm", ".owner.lock")):
            raise BundleError()
        if _hash(bundle / member.path) != (member.size, member.sha256):
            raise BundleError()
    if "registry.db" not in expected:
        raise BundleError()
    with closing(
        sqlite3.connect((bundle / "registry.db").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as registry:
        ids = {row[0] for row in registry.execute("SELECT store_id FROM registry_stores")}
        if (
            ids
            != {
                *(str(item.store.store_id) for item in manifest.stores),
                *(str(item) for item in manifest.dependencies),
            }
            or registry.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            or registry.execute("PRAGMA user_version").fetchone()[0] not in {2, 3, SCHEMA_VERSION}
        ):
            raise BundleError()
    for snapshot in manifest.stores:
        path = bundle / _directory(snapshot.store) / "memory.db"
        if path.relative_to(bundle).as_posix() not in expected:
            raise BundleError()
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as connection:
            connection.row_factory = sqlite3.Row
            metadata = connection.execute("SELECT * FROM store_metadata").fetchone()
            if metadata is None or (
                row_text(metadata, "store_id"),
                row_text(metadata, "kind"),
                row_text(metadata, "namespace"),
            ) != (str(snapshot.store.store_id), snapshot.store.kind, snapshot.store.namespace):
                raise BundleError()
            if (
                connection.execute("PRAGMA user_version").fetchone()[0]
                not in {2, 3, SCHEMA_VERSION}
                or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                or connection.execute("SELECT coverage FROM store_coverage").fetchone()[0]
                != snapshot.coverage
            ):
                raise BundleError()
    return manifest


def restore_bundles(bundles: tuple[Path, ...], root: Path) -> tuple[StoreInfo, ...]:
    """Validate a complete linked set, then atomically publish a new provider-disabled root.

    Never merge/overwrite live state. Hash/identity/dependency failure publishes
    nothing. Libraries are copied once; contradictory duplicates fail closed.
    """
    destination = validate_local_root(root)
    if not bundles or destination.exists():
        raise BundleError()
    try:
        manifests = tuple(_read_manifest(path.absolute()) for path in bundles)
        available: dict[CanonicalId, tuple[StoreSnapshot, Path, str]] = {}
        for path, manifest in zip(bundles, manifests, strict=True):
            for snapshot in manifest.stores:
                prefix = _directory(snapshot.store)
                fingerprint = sha256(
                    "".join(
                        item.model_dump_json()
                        for item in manifest.files
                        if item.path.startswith(prefix + "/")
                    ).encode()
                ).hexdigest()
                previous = available.get(snapshot.store.store_id)
                if previous is not None and (previous[0] != snapshot or previous[2] != fingerprint):
                    raise BundleError()
                available[snapshot.store.store_id] = (
                    snapshot,
                    path.absolute() / prefix,
                    fingerprint,
                )
        for manifest in manifests:
            if any(
                identity not in available
                or available[identity][0].store.kind != "library"
                or available[identity][0].coverage != "full"
                for identity in manifest.dependencies
            ):
                raise BundleError()
        # Verify runtime references against the declared set; a rewritten manifest
        # cannot hide a linked character dependency.
        for snapshot, path, _ in available.values():
            if snapshot.store.kind != "character" or snapshot.coverage != "full":
                continue
            with closing(
                sqlite3.connect((path / "memory.db").as_uri() + "?mode=ro&immutable=1", uri=True)
            ) as connection:
                for (identity,) in connection.execute(
                    "SELECT source_store_id FROM memory_evidence "
                    "UNION SELECT source_store_id FROM source_grants"
                ):
                    if identity != str(snapshot.store.store_id) and identity not in {
                        str(item) for item in available
                    }:
                        raise BundleError()
        with _publication(destination) as stage:
            with CanonicalEngine(stage) as engine:
                for snapshot, path, _ in available.values():
                    info = snapshot.store
                    folder = stage / _directory(info)
                    folder.mkdir(parents=True)
                    _copy_tree(path, folder)

                    def register(connection: sqlite3.Connection, info: StoreInfo = info) -> None:
                        with engine.registry.transaction(connection):
                            connection.execute(
                                "INSERT INTO registry_stores VALUES(?,?,?)",
                                (str(info.store_id), info.kind, info.namespace),
                            )

                    engine.registry.write(register)
            # Hash the actual copied members before SQLite can change headers/WAL.
            for manifest in manifests:
                for member in manifest.files:
                    if member.path == "registry.db":
                        continue
                    if _hash(stage / member.path) != (member.size, member.sha256):
                        raise BundleError()
            # Open every restored store and validate granted evidence closure before
            # publication. A full library backup from before a seed is insufficient.
            with CanonicalEngine(stage) as restored:
                for snapshot, _, _ in available.values():
                    if snapshot.store.kind != "character":
                        continue
                    state = restored.character(snapshot.store.store_id).state

                    def validate(
                        connection: sqlite3.Connection,
                        state: CharacterState = state,
                        full: bool = snapshot.coverage == "full",
                    ) -> None:
                        for row in connection.execute("SELECT * FROM memory_revisions"):
                            memory = load_model(row, Memory)
                            if memory.origin is not None and full:
                                origin = memory.origin
                                try:
                                    LibraryService(restored).knowledge(
                                        origin.origin_source_store_id,
                                        KnowledgeSelection(
                                            knowledge_id=origin.origin_memory_id,
                                            revision=origin.origin_memory_revision,
                                        ),
                                    )
                                except (NotFoundError, SourceUnavailableError) as error:
                                    raise BundleError() from error
                            if state.memory_suppressed(
                                connection, memory.memory_id, memory.revision
                            ):
                                continue
                            for assertion in memory.content.evidence:
                                if state.source_permitted(
                                    connection, assertion.evidence.source, "evidence_read"
                                ):
                                    try:
                                        restored.validate(assertion.evidence)
                                    except SourceUnavailableError as error:
                                        raise BundleError() from error

                    state.database.read(validate)
            (stage / "restore.json").write_text(
                '{"providers_disabled":true,"bundle_version":"0.1.0"}\n', encoding="utf-8"
            )
            remove_locks(stage)
        return tuple(item[0].store for item in available.values())
    except (OSError, sqlite3.Error, ValidationError) as error:
        raise BundleError() from error
