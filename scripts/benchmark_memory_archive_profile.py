"""Opt-in, process-local archive benchmark probes. Never installed by the runtime."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache, partial, wraps
from threading import Lock
from time import perf_counter_ns
from typing import TYPE_CHECKING
from unittest.mock import patch

from ember_memory_engine import CanonicalEngine
from ember_memory_engine.search_contracts import Encoder
from ember_memory_engine.workers import OwnedWorkers

if TYPE_CHECKING:
    from sqlite3 import _Parameters  # pyright: ignore[reportPrivateUsage]


@dataclass
class Stage:
    """Elapsed work, invocations and returned rows (not SQLite rows visited)."""

    ns: int = 0
    calls: int = 0
    rows: int = 0


@dataclass
class Trace:
    """One request, including worker work that finishes after a soft deadline."""

    stages: dict[str, Stage] = field(default_factory=dict[str, Stage])
    _lock: Lock = field(default_factory=Lock)

    def record(self, stage: str, start: int, *, calls: int = 0, rows: int = 0) -> None:
        """Accumulate work on independent threads; elapsed times can overlap."""
        elapsed = perf_counter_ns() - start
        with self._lock:
            metric = self.stages.get(stage)
            if metric is None:
                self.stages[stage] = Stage(elapsed, calls, rows)
            else:
                metric.ns += elapsed
                metric.calls += calls
                metric.rows += rows


current: ContextVar[Trace | None] = ContextVar("archive_benchmark_trace", default=None)


@lru_cache(maxsize=512)
def _sql_stage(sql: str) -> str | None:
    sql = " ".join(sql.lower().split())
    if "vec_documents" in sql and " match " in sql:
        return "knn_sql"
    if "document_fts" in sql and " match " in sql:
        return "fts_sql"
    payload = "payload_json" in sql or "select * from" in sql or "select d.*" in sql
    if payload and ("source_records" in sql or "materialized_evidence" in sql):
        return "source_payload_sql"
    if payload and "documents" in sql:
        return "projection_payload_sql"
    if any(
        table in sql for table in ("source_grants", "suppression_targets", "chunk_dependencies")
    ):
        return "scope_sql"
    return None


class _Cursor(sqlite3.Cursor):
    trace: Trace
    stage: str

    def fetchone(self) -> object:
        start = perf_counter_ns()
        row: object = None
        try:
            row = super().fetchone()
            return row
        finally:
            self.trace.record(self.stage, start, rows=int(row is not None))

    def fetchall(self) -> list[object]:
        start = perf_counter_ns()
        rows: list[object] = []
        try:
            rows = super().fetchall()
            return rows
        finally:
            self.trace.record(self.stage, start, rows=len(rows))


class _Connection(sqlite3.Connection):
    def execute(self, sql: str, parameters: _Parameters = (), /) -> sqlite3.Cursor:
        trace = current.get()
        stage = _sql_stage(sql) if trace is not None else None
        if stage is None or trace is None:
            return super().execute(sql, parameters)
        cursor = self.cursor(factory=_Cursor)
        cursor.trace, cursor.stage = trace, stage
        start = perf_counter_ns()
        try:
            return cursor.execute(sql, parameters)
        finally:
            trace.record(stage, start, calls=1)


@contextmanager
def profile(encoder: Encoder) -> Generator[None]:
    """Probe actual native worker, SQL and evidence calls, only in diagnostic process.

    Ponytail: classify Connection.execute shapes used by archive paths; extend when SQL
    moves to another API. No trace callback per SQLite internal operation.
    """
    submit = OwnedWorkers.submit
    encode = encoder.encode
    validate = CanonicalEngine.validate

    def queued[T](workers: OwnedWorkers, work: Callable[[], T]) -> Future[T]:
        trace = current.get()
        if trace is None:
            return submit(workers, work)
        name = workers.executor._thread_name_prefix or ""
        queue = (
            "encoding_queue"
            if name == "ember-query-encoding"
            else "vector_queue"
            if name == "ember-deep-vectors"
            else "archive_queue"
            if name.startswith("ember-deep-")
            else "other_queue"
        )
        start = perf_counter_ns()

        def run() -> T:
            trace.record(queue, start, calls=1)
            token = current.set(trace)
            try:
                return work()
            finally:
                current.reset(token)

        return submit(workers, run)

    def timed[**P, T](fn: Callable[P, T], stage: str) -> Callable[P, T]:
        @wraps(fn)
        def run(*args: P.args, **kwargs: P.kwargs) -> T:
            trace = current.get()
            if trace is None:
                return fn(*args, **kwargs)
            start = perf_counter_ns()
            try:
                return fn(*args, **kwargs)
            finally:
                trace.record(stage, start, calls=1)

        return run

    with (
        patch.object(sqlite3, "connect", partial(sqlite3.connect, factory=_Connection)),
        patch.object(OwnedWorkers, "submit", queued),
        patch.object(encoder, "encode", timed(encode, "encoding")),
        patch.object(CanonicalEngine, "validate", timed(validate, "evidence_validation")),
    ):
        yield
