"""Owned native KNN jobs: soft caller timeout never frees a running SQLite slot."""

import sqlite3
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from time import monotonic

from .database import row_int
from .errors import IndexError
from .index_storage import connect
from .read_budget import ReadBudget, bounded_connection
from .workers import OwnedWorkers


@dataclass(frozen=True)
class VectorRequest:
    """Trusted compiled SQL, immutable generation and bound canonical policy origin."""

    generation: Path
    canonical: Path
    statement: str
    parameters: tuple[str | int, ...]
    encoding: Future[bytes]
    release: Callable[[], None]
    budget: ReadBudget | None = None


@dataclass(frozen=True, slots=True)
class VectorHit:
    document_id: int
    distance: float


class VectorPool:
    """One native owner with one cached connection; foreground and deep pools are separate."""

    def __init__(self, *, name: str, cache_mib: int) -> None:
        self.workers = OwnedWorkers(1, max_pending=2, name=name)
        self.cache_mib = cache_mib
        self._identity: tuple[Path, Path] | None = None
        self._connection: sqlite3.Connection | None = None
        self.error_count = 0

    def submit(self, request: VectorRequest) -> Future[tuple[VectorHit, ...]]:
        """Queue without blocking; reject full admission before accepting more native work."""
        future = self.workers.submit(lambda: self._search(request))

        def completed(job: Future[tuple[VectorHit, ...]]) -> None:
            request.release()
            if not job.cancelled() and job.exception() is not None:
                self.error_count += 1

        future.add_done_callback(completed)
        return future

    def _search(self, request: VectorRequest) -> tuple[VectorHit, ...]:
        # Encoding may outlive its original caller. Both pools retain their slots
        # until completion; neither detaches native work on timeout/cancellation.
        vector = request.encoding.result()
        try:
            identity = (request.generation, request.canonical)
            if identity != self._identity:
                self._close_connection()
                connection = connect(request.generation, immutable=True)
                self._connection = connection
                connection.execute(f"PRAGMA cache_size=-{self.cache_mib * 1024}")
                connection.execute(
                    "ATTACH DATABASE ? AS canonical", (request.canonical.as_uri() + "?mode=ro",)
                )
                connection.execute("PRAGMA canonical.cache_size=-16384")
                self._connection = connection
                self._identity = identity
            connection = self._connection
            if connection is None:
                raise IndexError()
        except sqlite3.Error as error:
            self._close_connection()
            raise IndexError() from error
        hard_deadline = monotonic() + 2.0
        connection.set_progress_handler(
            lambda: int(
                monotonic() > hard_deadline
                or (request.budget is not None and request.budget.exhausted())
            ),
            1000,
        )
        try:
            with bounded_connection(connection, request.budget):
                connection.set_progress_handler(
                    lambda: int(
                        monotonic() > hard_deadline
                        or (request.budget is not None and request.budget.exhausted())
                    ),
                    1000,
                )
                rows = connection.execute(
                    request.statement, (vector, *request.parameters)
                ).fetchall()
            hits: dict[int, VectorHit] = {}
            for row in rows:
                identity = row_int(row, "document_id")
                distance: object = row["distance"]
                if not isinstance(distance, (int, float)) or not isfinite(distance) or distance < 0:
                    raise IndexError()
                hits.setdefault(identity, VectorHit(identity, distance))
            return tuple(hits.values())
        except sqlite3.Error as error:
            raise IndexError() from error
        finally:
            connection.set_progress_handler(None, 0)

    def _close_connection(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._connection = None
        self._identity = None

    def close(self) -> None:
        """Stop admission, drain, close on the native owner thread, then join off-loop."""
        self.workers.stop_admission()
        with suppress(RuntimeError):
            self.workers.executor.submit(self._close_connection).result()
        self.workers.shutdown()
