"""Disposable immutable derived databases and an atomic SQLite generation catalog."""

import importlib
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import cast
from uuid import UUID

from pydantic import ValidationError

from ember_memory_abi.common import CanonicalId

from .database import row_text
from .embedding import file_sha256
from .errors import IndexError
from .search_contracts import IndexGeneration

INDEX_SQL = """
CREATE TABLE metadata (payload_json TEXT NOT NULL);
CREATE TABLE documents (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target_id TEXT NOT NULL, revision INTEGER NOT NULL,
    text TEXT NOT NULL, payload_json TEXT, source_store_id TEXT NOT NULL,
    record_kind TEXT, record_id TEXT, part_id TEXT, conversation_id TEXT,
    branch TEXT, position INTEGER, role TEXT, source_at_us INTEGER, recorded_at_us INTEGER,
    source_key TEXT, whole_source_key TEXT,
    recall_eligible INTEGER NOT NULL DEFAULT 0, is_current INTEGER NOT NULL DEFAULT 0,
    memory_kind TEXT, lifecycle TEXT, modality TEXT, valid_from_us INTEGER, valid_until_us INTEGER,
    open_end_known INTEGER, predicate TEXT, entity_keys TEXT, payload_key TEXT,
    UNIQUE(kind,target_id,revision,part_id)
);
CREATE INDEX doc_target ON documents(kind,target_id,revision);
CREATE INDEX doc_memory_scope ON documents(
    kind,recall_eligible,is_current,memory_kind,modality,lifecycle,valid_from_us,valid_until_us,
    open_end_known,predicate,entity_keys,target_id,revision
);
CREATE INDEX doc_source ON documents(kind,source_store_id,record_kind,record_id,revision,part_id);
CREATE INDEX doc_conversation ON documents(conversation_id,branch,position);
CREATE VIRTUAL TABLE document_fts USING fts5(
    text,content='documents',content_rowid='id',tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER document_insert AFTER INSERT ON documents BEGIN
    INSERT INTO document_fts(rowid,text) VALUES(new.id,new.text);
END;
CREATE TRIGGER document_delete AFTER DELETE ON documents BEGIN
    INSERT INTO document_fts(document_fts,rowid,text) VALUES('delete',old.id,old.text);
END;
CREATE TRIGGER document_text_update AFTER UPDATE OF text ON documents BEGIN
    INSERT INTO document_fts(document_fts,rowid,text) VALUES('delete',old.id,old.text);
    INSERT INTO document_fts(rowid,text) VALUES(new.id,new.text);
END;
CREATE TABLE chunk_dependencies (
    document_id INTEGER NOT NULL, source_store_id TEXT NOT NULL, record_kind TEXT NOT NULL,
    record_id TEXT NOT NULL, revision INTEGER NOT NULL, part_id TEXT NOT NULL,
    source_key TEXT NOT NULL, whole_source_key TEXT NOT NULL,
    PRIMARY KEY(document_id,source_store_id,record_kind,record_id,revision,part_id)
);
CREATE TABLE embedding_records (
    id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, input_sha256 TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, target_revision INTEGER NOT NULL, mode TEXT NOT NULL,
    encoded_at TEXT NOT NULL
);
CREATE INDEX vector_input ON embedding_records(input_sha256);
CREATE INDEX vector_target ON embedding_records(document_id);
CREATE VIRTUAL TABLE vec_documents USING vec0(
    embedding float[384] distance_metric=l2,active INTEGER,valid_from INTEGER,valid_until INTEGER,
    +document_id INTEGER
);
"""


def connect(
    path: Path, *, immutable: bool = False, require_extension: bool = True
) -> sqlite3.Connection:
    """Load the installed trusted extension and immediately disable extension loading."""
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            path.as_uri() + "?mode=ro&immutable=1" if immutable else path.as_uri(),
            uri=True,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA cache_size=-32768")
        try:
            loader = cast(
                Callable[[sqlite3.Connection], None],
                importlib.import_module("sqlite_vec").load,
            )

            connection.enable_load_extension(True)
            try:
                loader(connection)
            finally:
                connection.enable_load_extension(False)
        except (sqlite3.Error, ImportError):
            if require_extension:
                raise
        return connection
    except (sqlite3.Error, ImportError) as error:
        if connection is not None:
            connection.close()
        raise IndexError() from error


class IndexStore:
    """Store-scoped catalog; missing/corrupt catalogs never create canonical replacement state."""

    def __init__(self, canonical_path: Path, store_id: CanonicalId) -> None:
        self.directory = canonical_path.parent / "indexes"
        self.canonical_path = canonical_path
        self.store_id = store_id
        self._guard = RLock()
        self._leases: dict[Path, int] = {}
        self._certificate: tuple[str, str, int, int] | None = None

    def active(self) -> tuple[IndexGeneration, Path]:
        """Read the committed pointer and verify logical generation metadata; no model loading."""
        path = self.directory / "search.db"
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    "SELECT payload_json FROM active_generation WHERE singleton=1"
                ).fetchone()
                if row is None:
                    raise IndexError()
                generation = IndexGeneration.model_validate_json(row_text(row, "payload_json"))
            if generation.store_id != self.store_id:
                raise IndexError()
            target = self.directory / "generations" / (str(generation.generation) + ".db")
            if not target.is_file() or target.is_symlink() or target.is_junction():
                raise IndexError()
            return generation, target
        except (OSError, sqlite3.Error, ValidationError) as error:
            raise IndexError() from error

    def lease(self, path: Path) -> Callable[[], None]:
        """Retain an immutable file for a foreground/native job until its actual completion."""
        with self._guard:
            if not path.is_file():
                raise IndexError()
            self._leases[path] = self._leases.get(path, 0) + 1

        def release() -> None:
            with self._guard:
                remaining = self._leases.get(path, 0) - 1
                if remaining > 0:
                    self._leases[path] = remaining
                else:
                    self._leases.pop(path, None)

        return release

    def source_coverage(self) -> str:
        """Describe canonical archive completeness independently of derived index health."""
        with closing(
            sqlite3.connect(self.canonical_path.as_uri() + "?mode=ro", uri=True)
        ) as connection:
            connection.row_factory = sqlite3.Row
            return row_text(
                connection.execute("SELECT coverage FROM store_coverage").fetchone(), "coverage"
            )

    def source_pending(self, generation: IndexGeneration) -> bool:
        """Check source-specific outbox events; approval does not change archive text."""
        with closing(
            sqlite3.connect(self.canonical_path.as_uri() + "?mode=ro", uri=True)
        ) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM index_outbox WHERE sequence>? AND kind='source_changed' LIMIT 1",
                    (generation.outbox_sequence,),
                ).fetchone()
                is not None
            )

    def prune(self) -> None:
        """Keep two published generations and leased/open disposable files."""
        active, _ = self.active()
        directory = (self.directory / "generations").resolve()
        with self._guard:
            with closing(sqlite3.connect(self.directory / "search.db")) as connection:
                rows = connection.execute(
                    "SELECT generation FROM generation_history ORDER BY rowid DESC LIMIT 2"
                ).fetchall()
            keep = {str(active.generation), *(str(row[0]) for row in rows)}
            for path in directory.glob("*.db"):
                try:
                    identity = UUID(path.stem)
                except ValueError:
                    continue
                if (
                    identity.version != 4
                    or path.stem in keep
                    or path in self._leases
                    or path.is_symlink()
                    or path.is_junction()
                    or not path.resolve().is_relative_to(directory)
                ):
                    continue
                try:
                    path.unlink()
                except PermissionError:
                    # An idle native connection can still hold a Windows file
                    # handle; the next publication or final drain retries safely.
                    continue

    def certified(self, generation: IndexGeneration, path: Path) -> bool:
        """Trust cached header projections only in an unchanged, hash-verified generation."""
        try:
            stat = path.stat()
            identity = (
                str(generation.generation),
                generation.file_sha256,
                stat.st_size,
                stat.st_mtime_ns,
            )
            if identity != self._certificate:
                if file_sha256(path) != generation.file_sha256 or (
                    path.stat().st_size,
                    path.stat().st_mtime_ns,
                ) != (stat.st_size, stat.st_mtime_ns):
                    raise IndexError()
                self._certificate = identity
            return True
        except OSError as error:
            raise IndexError() from error

    def publish(self, generation: IndexGeneration) -> None:
        """Switch after validation of a separate generation; preserve prior generation files."""
        target = self.directory / "generations" / (str(generation.generation) + ".db")
        self.certified(generation, target)
        catalog = self.directory / "search.db"
        try:
            with closing(sqlite3.connect(catalog, isolation_level=None)) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS active_generation("
                    "singleton INTEGER PRIMARY KEY CHECK(singleton=1),payload_json TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS generation_history("
                    "generation TEXT PRIMARY KEY,payload_json TEXT NOT NULL)"
                )
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute(
                    "SELECT payload_json FROM active_generation WHERE singleton=1"
                ).fetchone()
                if previous is not None:
                    try:
                        saved = IndexGeneration.model_validate_json(previous[0])
                        connection.execute(
                            "INSERT OR IGNORE INTO generation_history VALUES(?,?)",
                            (str(saved.generation), saved.model_dump_json()),
                        )
                    except ValidationError:
                        pass
                connection.execute(
                    "INSERT INTO active_generation VALUES(1,?) ON CONFLICT(singleton) "
                    "DO UPDATE SET payload_json=excluded.payload_json",
                    (generation.model_dump_json(),),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO generation_history VALUES(?,?)",
                    (str(generation.generation), generation.model_dump_json()),
                )
                connection.commit()
        except sqlite3.DatabaseError as error:
            if error.sqlite_errorcode not in {sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CORRUPT}:
                raise IndexError() from error
            # Only corruption permits replacement of a disposable catalog. Busy,
            # permission and disk errors preserve the existing pointer unchanged.
            damaged = catalog.with_suffix(".damaged-" + str(generation.generation))
            catalog.rename(damaged)
            self.publish(generation)
