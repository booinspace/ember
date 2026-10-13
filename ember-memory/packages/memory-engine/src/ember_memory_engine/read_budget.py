"""Request-scoped SQL/resource guards; ordinary readers do not install instrumentation."""

import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from threading import Lock
from time import monotonic

from ember_memory_abi.errors import MemoryError
from ember_memory_abi.read_profiles import ReadUsage


class ReadBudgetExceededError(MemoryError):
    """A deep read cannot exceed its trusted execution envelope."""

    code = "read_budget_exceeded"
    safe_message = "The bounded read reached its resource or time limit."


class ForegroundDeferredError(MemoryError):
    """Unmeasured concurrent deep reads yield to an actual foreground native job."""

    code = "deep_deferred_for_foreground"
    safe_message = "Deep recall was deferred to preserve foreground latency."


class ReadBudget:
    """One composition-owned meter shared by canonical, source and native vector work."""

    def __init__(
        self,
        *,
        deadline: float,
        statements: int = 4096,
        rows: int = 2048,
        materialized_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if statements < 1 or statements > 4096 or rows < 1 or rows > 2048:
            raise ValueError("Read limits must stay within the supported envelope.")
        if not 1 <= materialized_bytes <= 16 * 1024 * 1024:
            raise ValueError("Read allocation limit exceeds the supported envelope.")
        self.deadline = deadline
        self.statement_limit = statements
        self.row_limit = rows
        self.byte_limit = materialized_bytes
        self._guard = Lock()
        self._statements = 0
        self._rows = 0
        self._bytes = 0
        self._limits: set[str] = set()
        self.foreground_busy: Callable[[], bool] | None = None

    def usage(self) -> ReadUsage:
        """Snapshot counters; SQL text and private field values are never retained."""
        with self._guard:
            return ReadUsage(
                sql_statements=self._statements,
                materialized_rows=self._rows,
                materialized_bytes=self._bytes,
                limits_hit=tuple(sorted(self._limits)),
            )

    def exhausted(self) -> bool:
        """Signal a native progress abort without retaining SQL or private values."""
        foreground = self.foreground_busy is not None and self.foreground_busy()
        with self._guard:
            if foreground:
                self._limits.add("foreground_active")
            if monotonic() >= self.deadline:
                self._limits.add("read_deadline")
            if self._statements > self.statement_limit:
                self._limits.add("sql_statement_budget")
            if self._rows > self.row_limit:
                self._limits.add("materialized_row_budget")
            if self._bytes > self.byte_limit:
                self._limits.add("materialized_byte_budget")
            return bool(self._limits)

    def check(self) -> None:
        """Fail closed before/after native work and before returning any result."""
        if self.exhausted():
            if "foreground_active" in self.usage().limits_hit:
                raise ForegroundDeferredError()
            raise ReadBudgetExceededError()

    def _trace(self, _statement: str) -> None:
        with self._guard:
            self._statements += 1

    def _row(self, cursor: sqlite3.Cursor, values: tuple[object, ...]) -> sqlite3.Row:
        size = sum(
            len(value.encode("utf-8"))
            if isinstance(value, str)
            else len(value)
            if isinstance(value, bytes)
            else 8
            for value in values
        )
        with self._guard:
            self._rows += 1
            self._bytes += size
        self.check()
        return sqlite3.Row(cursor, values)

    @contextmanager
    def bind(self, connection: sqlite3.Connection) -> Generator[None]:
        """Use reserved callbacks only on an owned deep connection, then restore its defaults.

        SQLite limits reject oversized values before Python row materialization.
        Counted bytes are cumulative, a conservative bound on working allocation.
        Accepted native work remains owned even when its original caller cancels.
        """
        self.check()
        factory = connection.row_factory
        previous_length = connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 256 * 1024)
        connection.row_factory = self._row
        connection.set_trace_callback(self._trace)
        connection.set_progress_handler(lambda: int(self.exhausted()), 1000)
        previous_cache: int | None = None
        try:
            cache_value = connection.execute("PRAGMA cache_size").fetchone()[0]
            if not isinstance(cache_value, int):
                raise ReadBudgetExceededError()
            previous_cache = cache_value
            connection.execute("PRAGMA cache_size=-1024")
            yield
            self.check()
        finally:
            connection.row_factory = factory
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous_length)
            connection.set_trace_callback(None)
            connection.set_progress_handler(None, 0)
            if previous_cache is not None:
                connection.execute(f"PRAGMA cache_size={previous_cache}")


@contextmanager
def bounded_connection(
    connection: sqlite3.Connection, budget: ReadBudget | None
) -> Generator[None]:
    """Optional instrumentation keeps ordinary connection behavior unchanged."""
    if budget is None:
        yield
    else:
        with budget.bind(connection):
            yield
