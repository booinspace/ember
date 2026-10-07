"""Owned bounded in-process async facade over the synchronous canonical store."""

import asyncio
import sqlite3
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from threading import Lock
from typing import cast

from ember_memory_abi.common import (
    CanonicalId,
    timestamp_from_datetime,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.errors import DeadlineExceededError, ErrorDetails, ErrorInfo, MemoryError
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ArchiveSearchResponse,
    ContextRequest,
    DisableRequest,
    ForgetRequest,
    MemoryCapabilities,
    MemoryQuery,
    MutationReceipt,
    PageRequest,
    PinRequest,
    RecordAccessRequest,
    RememberRequest,
    RequestOptions,
    RetrievalContext,
    SearchResponse,
    SupersedeRequest,
    UnpinRequest,
    UpdateRequest,
)
from ember_memory_abi.memory import Memory, MemorySource, PinnedMemory

from . import mutations, retrieval
from .character import SqliteMemoryStore
from .database import check_deadline
from .errors import EngineClosedError, WriteOutcomeUnknownError


class InProcessAsyncMemoryClient:
    """Implement AsyncMemoryClient using existing store-owned bounded workers.

    Cancelling a queued future can prevent work. Running work stays owned until it
    completes; cancelled writes must be resolved through the identical idempotent
    request. The facade never calls blocking Future.result() on the realtime loop.
    """

    def __init__(self, store: SqliteMemoryStore) -> None:
        self._store = store
        self._guard = Lock()
        self._closed = False
        self._jobs: set[Future[object]] = set()
        self._errors: deque[ErrorInfo] = deque(maxlen=128)
        self._error_count = 0

    @property
    def character_id(self) -> CanonicalId:
        """Trusted character binding shared with the injected synchronous store."""
        return self._store.character_id

    def _completed[T](self, future: Future[T]) -> None:
        error = None if future.cancelled() else future.exception()
        with self._guard:
            self._jobs.discard(cast(Future[object], future))
            if error is not None:
                self._error_count += 1
                self._errors.append(
                    error.info
                    if isinstance(error, MemoryError)
                    else ErrorInfo(code="worker_failed", details=ErrorDetails())
                )

    async def _invoke[T](
        self,
        work: Callable[[sqlite3.Connection], T],
        *,
        deadline: str | None = None,
        write: bool = False,
    ) -> T:
        clock = self._store.database.clock
        check_deadline(clock, deadline)
        with self._guard:
            if self._closed:
                raise EngineClosedError()
            future = self._store.submit(work, write=write)
            self._jobs.add(cast(Future[object], future))
        future.add_done_callback(self._completed)
        wrapped = asyncio.wrap_future(future)

        def consume(completed: asyncio.Future[T]) -> None:
            if not completed.cancelled():
                completed.exception()

        wrapped.add_done_callback(consume)
        seconds = (
            None
            if deadline is None
            else max(
                0.0,
                (
                    timestamp_to_unix_microseconds(deadline)
                    - timestamp_to_unix_microseconds(timestamp_from_datetime(clock.now()))
                )
                / 1_000_000,
            )
        )
        try:
            async with asyncio.timeout(seconds):
                return await asyncio.shield(wrapped)
        except TimeoutError as error:
            prevented = future.cancel()
            if write and not prevented:
                raise WriteOutcomeUnknownError() from error
            raise DeadlineExceededError() from error
        except asyncio.CancelledError:
            future.cancel()
            raise

    async def capabilities(self) -> MemoryCapabilities:
        """Return truthful support from an owned read worker."""
        return await self._invoke(lambda _: self._store.capabilities())

    async def search(self, query: MemoryQuery) -> SearchResponse:
        """Run scoped structured/literal canonical fallback off the realtime loop."""
        return await self._invoke(
            lambda connection: retrieval.search(self._store.state, connection, query),
            deadline=query.options.deadline_at,
        )

    async def search_archive(self, query: ArchiveQuery) -> ArchiveSearchResponse:
        """Search exact currently permitted snapshots through bounded native work."""
        return await self._invoke(
            lambda connection: retrieval.search_archive(self._store.state, connection, query),
            deadline=query.options.deadline_at,
        )

    async def retrieve_context(self, request: ContextRequest) -> RetrievalContext:
        """Retrieve budgeted context without event-loop filesystem/SQLite work."""
        return await self._invoke(
            lambda connection: retrieval.retrieve_context(self._store.state, connection, request),
            deadline=request.options.deadline_at,
        )

    async def get(
        self,
        memory_id: CanonicalId,
        *,
        revision: int | None = None,
        options: RequestOptions | None = None,
    ) -> Memory:
        """Read exact/latest owned history with current suppression enforcement."""
        return await self._invoke(
            lambda connection: self._store.state.read_memory(
                connection, memory_id, revision, options
            ),
            deadline=options.deadline_at if options else None,
        )

    async def get_sources(
        self,
        memory_id: CanonicalId,
        *,
        revision: int | None = None,
        options: RequestOptions | None = None,
    ) -> tuple[MemorySource, ...]:
        """Resolve exact original quotes, rechecking source access and policy."""
        return await self._invoke(
            lambda connection: self._store.state.read_sources(
                connection, memory_id, revision, options
            ),
            deadline=options.deadline_at if options else None,
        )

    async def get_recent(self, request: PageRequest) -> SearchResponse:
        """Retrieve active recent canonical beliefs using bounded workers."""
        return await self._invoke(
            lambda connection: retrieval.recent(self._store.state, connection, request),
            deadline=request.options.deadline_at,
        )

    async def get_pinned(self, request: PageRequest) -> tuple[PinnedMemory, ...]:
        """Return only current active unsuppressed pins."""
        return await self._invoke(
            lambda connection: retrieval.get_pinned(self._store.state, connection, request),
            deadline=request.options.deadline_at,
        )

    async def remember(self, request: RememberRequest) -> MutationReceipt:
        """Perform an explicitly authorized idempotent canonical mutation."""
        return await self._invoke(
            lambda connection: mutations.mutate(self._store.state, connection, "remember", request),
            deadline=request.options.deadline_at,
            write=True,
        )

    async def update(self, request: UpdateRequest) -> MutationReceipt:
        """Append a compare-and-swap interpretation revision."""
        return await self._invoke(
            lambda connection: mutations.mutate(self._store.state, connection, "update", request),
            deadline=request.options.deadline_at,
            write=True,
        )

    async def supersede(self, request: SupersedeRequest) -> MutationReceipt:
        """Persist explicit transition evidence and independent replacement identity atomically."""
        return await self._invoke(
            lambda connection: mutations.mutate(
                self._store.state, connection, "supersede", request
            ),
            deadline=request.options.deadline_at,
            write=True,
        )

    async def disable(self, request: DisableRequest) -> MutationReceipt:
        """Remove recall eligibility without erasing source/history."""
        return await self._invoke(
            lambda connection: mutations.mutate(self._store.state, connection, "disable", request),
            deadline=request.options.deadline_at,
            write=True,
        )

    async def forget(self, request: ForgetRequest) -> MutationReceipt:
        """Commit suppression and local pin/outbox invalidation together."""
        return await self._invoke(
            lambda connection: mutations.mutate(self._store.state, connection, "forget", request),
            deadline=request.options.deadline_at,
            write=True,
        )

    async def pin(self, request: PinRequest) -> MutationReceipt:
        """Pin an authorized active revision idempotently."""
        return await self._invoke(
            lambda connection: mutations.mutate(self._store.state, connection, "pin", request),
            deadline=request.options.deadline_at,
            write=True,
        )

    async def unpin(self, request: UnpinRequest) -> MutationReceipt:
        """Remove a pin with expected revision and idempotency validation."""
        return await self._invoke(
            lambda connection: mutations.mutate(self._store.state, connection, "unpin", request),
            deadline=request.options.deadline_at,
            write=True,
        )

    async def record_access(self, request: RecordAccessRequest) -> MutationReceipt:
        """Record exact permitted access observations without modifying beliefs."""
        return await self._invoke(
            lambda connection: mutations.mutate(
                self._store.state, connection, "record_access", request
            ),
            deadline=request.options.deadline_at,
            write=True,
        )

    async def aclose(self) -> tuple[ErrorInfo, ...]:
        """Stop admission and drain jobs; return the last 128 structured failures.

        Earlier failures are signaled by a completion_errors_truncated marker.

        Other clients may share the store. Composition still closes the parent
        CanonicalEngine off the realtime loop after closing all facades.
        """
        with self._guard:
            self._closed = True
            pending = tuple(self._jobs)
        await asyncio.gather(*(asyncio.wrap_future(job) for job in pending), return_exceptions=True)
        with self._guard:
            omitted = self._error_count - len(self._errors)
            overflow = (
                (ErrorInfo(code="completion_errors_truncated", details=ErrorDetails()),)
                if omitted
                else ()
            )
            return (*overflow, *self._errors)
