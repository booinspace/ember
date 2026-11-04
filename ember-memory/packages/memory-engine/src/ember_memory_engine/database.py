"""Thread-owned SQLite connections, version checks, transactions and store ownership."""

import sqlite3
from collections.abc import Callable, Generator
from concurrent.futures import Future
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Lock, get_ident
from typing import Literal

from pydantic import BaseModel, ValidationError

from ember_memory_abi.common import (
    CanonicalId,
    timestamp_from_datetime,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.errors import DeadlineExceededError, UnsupportedVersionError

from .errors import CorruptStoreError, EngineBusyError, StorageError
from .models import Clock
from .ownership import StoreLock, validate_local_root
from .schema import (
    CONVERSATION_SQL,
    IMMUTABLE_TABLES,
    PHASE_EIGHT_SQL,
    PHASE_FIVE_SQL,
    PHASE_FOUR_IMMUTABLE,
    PHASE_FOUR_SQL,
    PHASE_SEVEN_IMMUTABLE,
    PHASE_SEVEN_SQL,
    PHASE_SIX_IMMUTABLE,
    PHASE_SIX_SQL,
    SCHEMA_SQL,
    SCHEMA_VERSION,
)
from .workers import OwnedWorkers


def row_text(row: sqlite3.Row, column: str) -> str:
    """Narrow SQLite's untyped row values without exposing corrupt values in errors."""
    value: object = row[column]
    if not isinstance(value, str):
        raise CorruptStoreError()
    return value


def row_int(row: sqlite3.Row, column: str) -> int:
    """Require actual integer canonical storage fields."""
    value: object = row[column]
    if not isinstance(value, int) or isinstance(value, bool):
        raise CorruptStoreError()
    return value


def model_bytes(model: BaseModel) -> str:
    """Snapshot typed contracts into exact persisted JSON bytes, never repr/log output."""
    return model.model_dump_json()


def payload_hash(payload: str) -> str:
    """Protect exact persisted bytes, separately from canonical request/equivalence hashes."""
    return sha256(payload.encode("utf-8")).hexdigest()


def load_model[T: BaseModel](row: sqlite3.Row, model: type[T]) -> T:
    """Verify immutable stored bytes then parse the declared contract, with safe errors."""
    payload = row_text(row, "payload_json")
    if payload_hash(payload) != row_text(row, "payload_sha256"):
        raise CorruptStoreError()
    try:
        return model.model_validate_json(payload)
    except ValidationError as error:
        raise CorruptStoreError() from error


def check_deadline(clock: Clock, deadline: str | None) -> None:
    """Reject expired work before admission/execution/commit without rewriting source clocks."""
    if deadline is not None and timestamp_from_datetime(clock.now()) >= deadline:
        raise DeadlineExceededError()


class SqliteDatabase:
    """One owned writer/two foreground readers; connections never cross owner threads."""

    def __init__(
        self,
        path: Path,
        store_id: CanonicalId,
        kind: Literal["registry", "library", "character"],
        namespace: str,
        clock: Clock,
        *,
        max_pending: int = 16,
    ) -> None:
        validate_local_root(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.store_id = store_id
        self.clock = clock
        self._owner = StoreLock(path.with_suffix(".owner.lock"))
        self.writer = OwnedWorkers(1, max_pending=max_pending, name=f"ember-write-{store_id}")
        self.readers = OwnedWorkers(2, max_pending=max_pending, name=f"ember-read-{store_id}")
        self.deep_readers = OwnedWorkers(1, max_pending=4, name=f"ember-deep-{store_id}")
        self._connections: dict[int, sqlite3.Connection] = {}
        self._guard = Lock()
        self._closed = False
        self.on_write: Callable[[], None] | None = None
        try:
            self.writer.submit(lambda: self._initialize(kind, namespace)).result()
        except sqlite3.Error as error:
            self.close()
            raise StorageError() from error
        except Exception:
            self.close()
            raise

    def _connection(self, read_only: bool) -> sqlite3.Connection:
        identity = get_ident()
        with self._guard:
            connection = self._connections.get(identity)
            if connection is not None:
                return connection
            try:
                connection = sqlite3.connect(self.path, isolation_level=None, timeout=2)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=2000")
                if read_only:
                    connection.execute("PRAGMA query_only=ON")
                self._connections[identity] = connection
            except sqlite3.Error as error:
                raise StorageError() from error
        return connection

    def _initialize(self, kind: str, namespace: str) -> None:
        connection = self._connection(False)
        version_value: object = connection.execute("PRAGMA user_version").fetchone()[0]
        if not isinstance(version_value, int):
            raise CorruptStoreError()
        if version_value not in {0, 1, 2, 3, 4, 5, 6, SCHEMA_VERSION}:
            raise UnsupportedVersionError()
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        if version_value == 0:
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()
            if existing is not None:
                raise UnsupportedVersionError()
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + (
                        SCHEMA_SQL.split("CREATE TABLE source_records")[0]
                        if kind == "registry"
                        else SCHEMA_SQL
                        + PHASE_FOUR_SQL
                        + PHASE_FIVE_SQL
                        + PHASE_SIX_SQL
                        + PHASE_SEVEN_SQL
                        + PHASE_EIGHT_SQL
                        + CONVERSATION_SQL
                    )
                )
                for table in (
                    ()
                    if kind == "registry"
                    else (
                        *IMMUTABLE_TABLES,
                        *PHASE_FOUR_IMMUTABLE,
                        *PHASE_SIX_IMMUTABLE,
                        *PHASE_SEVEN_IMMUTABLE,
                    )
                ):
                    for action in ("UPDATE", "DELETE"):
                        connection.execute(
                            f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} "
                            f"BEFORE {action} ON {table} BEGIN "
                            "SELECT RAISE(ABORT, 'immutable canonical revision'); END"
                        )
                connection.execute(
                    "INSERT INTO store_metadata(store_id,kind,namespace) VALUES(?,?,?)",
                    (str(self.store_id), kind, namespace),
                )
                connection.execute(
                    "INSERT INTO schema_versions VALUES(?,?)",
                    (SCHEMA_VERSION, timestamp_from_datetime(self.clock.now())),
                )
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        if version_value in {1, 2, 3, 4, 5, 6}:
            row = connection.execute(
                "SELECT store_id,kind,namespace FROM store_metadata"
            ).fetchone()
            if row is None or (
                row_text(row, "store_id"),
                row_text(row, "kind"),
                row_text(row, "namespace"),
            ) != (str(self.store_id), kind, namespace):
                raise CorruptStoreError()
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise CorruptStoreError()
            # The OS-held store lock is the deletion/migration barrier. Preserve a
            # consistent pre-upgrade image before any schema write; never overwrite it.
            backup_path = self.path.with_suffix(f".schema-{version_value}.backup.db")
            if backup_path.exists():
                from uuid import uuid4

                backup_path = self.path.with_suffix(f".schema-{version_value}-{uuid4()}.backup.db")
            self.backup(backup_path, connection=connection)
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + (
                        (
                            (PHASE_FOUR_SQL if version_value == 1 else "")
                            + PHASE_FIVE_SQL
                            + (PHASE_SIX_SQL if version_value < 4 else "")
                            + PHASE_SEVEN_SQL
                            + PHASE_EIGHT_SQL
                            + CONVERSATION_SQL
                        )
                        if kind != "registry"
                        else ""
                    )
                )
                for table in (
                    ()
                    if kind == "registry"
                    else (
                        (*PHASE_FOUR_IMMUTABLE, *PHASE_SIX_IMMUTABLE, *PHASE_SEVEN_IMMUTABLE)
                        if version_value == 1
                        else (*PHASE_SIX_IMMUTABLE, *PHASE_SEVEN_IMMUTABLE)
                    )
                ):
                    for action in ("UPDATE", "DELETE"):
                        connection.execute(
                            f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} "
                            f"BEFORE {action} ON {table} BEGIN "
                            "SELECT RAISE(ABORT, 'immutable canonical revision'); END"
                        )
                if kind != "registry" and version_value < 5:
                    from ember_memory_abi.memory import Memory

                    from .claim_projection import backfill_supersessions, project

                    cursor = connection.execute("SELECT * FROM memory_revisions")
                    while rows := cursor.fetchmany(256):
                        for historical in rows:
                            project(connection, load_model(historical, Memory))
                    backfill_supersessions(connection)
                connection.execute(
                    "INSERT INTO schema_versions VALUES(?,?)",
                    (SCHEMA_VERSION, timestamp_from_datetime(self.clock.now())),
                )
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise CorruptStoreError()
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise
        row = connection.execute("SELECT store_id,kind,namespace FROM store_metadata").fetchone()
        if row is None or (
            row_text(row, "store_id"),
            row_text(row, "kind"),
            row_text(row, "namespace"),
        ) != (str(self.store_id), kind, namespace):
            raise CorruptStoreError()
        integrity: object = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise CorruptStoreError()

    def backup(self, destination: Path, *, connection: sqlite3.Connection | None = None) -> None:
        """Create a consistent new SQLite image, never a live DB/WAL file copy.

        An explicit connection is only supplied by the owning migration thread.
        Ordinary maintenance runs the SQLite backup on an owned reader.
        """

        def copy(source: sqlite3.Connection) -> None:
            with destination.open("xb"):
                pass
            try:
                with closing(sqlite3.connect(destination)) as target:
                    source.backup(target, pages=256)
                    if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise CorruptStoreError()
            except BaseException:
                destination.unlink(missing_ok=True)
                raise

        if connection is None:
            self.read(copy)
        else:
            copy(connection)

    def _run[T](self, work: Callable[[sqlite3.Connection], T], read_only: bool) -> T:
        try:
            result = work(self._connection(read_only))
            if not read_only and self.on_write is not None:
                self.on_write()
            return result
        except sqlite3.Error as error:
            if getattr(error, "sqlite_errorcode", None) in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }:
                raise EngineBusyError() from error
            raise StorageError() from error

    def submit[T](
        self, work: Callable[[sqlite3.Connection], T], *, write: bool = False, deep: bool = False
    ) -> Future[T]:
        """Submit bounded work to the appropriate owner group; no inline SQLite on callers."""
        group = self.writer if write else (self.deep_readers if deep else self.readers)
        return group.submit(lambda: self._run(work, not write))

    def read[T](self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Blocking maintenance/read API, unsuitable for a realtime loop."""
        return self.submit(work).result()

    def write[T](self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Run a synchronous canonical mutation on the one dedicated writer."""
        return self.submit(work, write=True).result()

    @contextmanager
    def transaction(self, connection: sqlite3.Connection) -> Generator[None]:
        """Commit one short canonical transaction, rolling back every failed operation."""
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    def recorded_stamp(self, connection: sqlite3.Connection) -> tuple[str, int]:
        """Monotonic recorded clock per store; this never changes historical source clocks."""
        row = connection.execute("SELECT recorded_clock_us FROM store_metadata").fetchone()
        if row is None:
            raise CorruptStoreError()
        value = max(
            timestamp_to_unix_microseconds(timestamp_from_datetime(self.clock.now())),
            row_int(row, "recorded_clock_us") + 1,
        )
        connection.execute("UPDATE store_metadata SET recorded_clock_us=?", (value,))
        from ember_memory_abi.common import timestamp_from_unix_microseconds

        return timestamp_from_unix_microseconds(value), value

    def _close_connection(self) -> None:
        with self._guard:
            connection = self._connections.pop(get_ident(), None)
        if connection is not None:
            connection.close()

    def close(self) -> None:
        """Stop admission, drain jobs, close each connection on its owner thread, then unlock."""
        if self._closed:
            return
        self._closed = True
        self.writer.stop_admission()
        self.readers.stop_admission()
        self.deep_readers.stop_admission()
        self.deep_readers.executor.submit(self._close_connection).result()
        self.writer.executor.submit(self._close_connection).result()
        barrier = Barrier(2)

        def close_reader() -> None:
            barrier.wait()
            self._close_connection()

        cleanup = [self.readers.executor.submit(close_reader) for _ in range(2)]
        for job in cleanup:
            job.result()
        self.writer.shutdown()
        self.readers.shutdown()
        self.deep_readers.shutdown()
        self._owner.close()

    def __enter__(self) -> "SqliteDatabase":
        """Own an explicitly scoped maintenance database."""
        return self

    def __exit__(self, *_: object) -> None:
        """Drain workers before releasing the store lock."""
        self.close()
