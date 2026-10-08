"""Concrete maintenance composition; provider adapters depend only on their injected port."""

from pathlib import Path

from ember_memory_abi.archive import (
    ArchiveWriteReceipt,
    ImportCursor,
    NormalizedArchiveBatch,
    SourceRecord,
)
from ember_memory_abi.common import CanonicalId
from ember_memory_engine import CanonicalEngine, SourceKey, SourceKeyAssignment, SqliteArchiveStore
from ember_memory_importers.normalization.contracts import ImportReport, RecordIdentity, RecordKey
from ember_memory_importers.normalization.pipeline import import_json
from ember_memory_importers.normalization.zip_pipeline import import_zip


class EngineImportTarget:
    """Bridge private importer key models to the engine's atomic maintenance contract."""

    def __init__(self, writer: SqliteArchiveStore) -> None:
        self.writer = writer
        self.source_store_id = writer.source_store_id
        self.namespace = writer.namespace

    def identify(self, keys: tuple[RecordKey, ...]) -> tuple[RecordIdentity, ...]:
        """Prepare source identity without an importer dependency on concrete storage."""
        assigned = self.writer.prepare_keys(tuple(SourceKey(**key.model_dump()) for key in keys))
        return tuple(
            RecordIdentity(key=RecordKey(**item.key.model_dump()), record_id=item.record_id)
            for item in assigned
        )

    def latest(self, kind: str, identity: CanonicalId) -> SourceRecord | None:
        """Resolve current immutable source revision for normalization change detection."""
        return self.writer.latest_record(kind, identity)

    def latest_many(
        self, identities: tuple[tuple[str, CanonicalId], ...]
    ) -> dict[tuple[str, CanonicalId], SourceRecord]:
        """Resolve a conversation's revisions through one owned SQLite reader task."""
        return self.writer.latest_records(identities)

    def cursor(self, cursor: ImportCursor) -> ImportCursor | None:
        """Resolve existing canonical import progress before planning source revisions."""
        return self.writer.cursor(cursor)

    def commit(
        self, batch: NormalizedArchiveBatch, identities: tuple[RecordIdentity, ...]
    ) -> ArchiveWriteReceipt:
        """Commit the crosswalk together with its source records, cursor and receipt."""
        keys = tuple(
            SourceKeyAssignment(key=SourceKey(**item.key.model_dump()), record_id=item.record_id)
            for item in identities
        )
        return self.writer.write_batch(batch, keys=keys)


def import_source(root: Path, source: Path, namespace: str, adapter: str) -> ImportReport:
    """Trusted maintenance imports sources only; it never automatically activates beliefs."""
    with CanonicalEngine(root) as engine:
        existing = next(
            (
                info
                for info in engine.stores()
                if info.kind == "library" and info.namespace == namespace
            ),
            None,
        )
        library = existing or engine.create_library(namespace)
        directory = engine.root / "libraries" / str(library.store_id)
        target = EngineImportTarget(engine.archive(library.store_id))
        if adapter == "chatgpt_zip":
            return import_zip(
                source,
                target=target,
                raw_vault=directory / "rawsource",
                manifests=directory / "manifests",
            )
        return import_json(
            source,
            adapter=adapter,
            target=target,
            raw_vault=directory / "rawsource",
            manifests=directory / "manifests",
        )
