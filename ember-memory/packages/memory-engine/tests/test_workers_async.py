"""Worker admission, realtime-loop responsiveness and uncertain-write lifecycle acceptance."""

import asyncio
import sqlite3
from collections.abc import Callable
from concurrent.futures import Future
from threading import Event

import pytest

from ember_memory_abi.errors import DeadlineExceededError
from ember_memory_abi.interfaces import MemoryQuery, RememberRequest, RequestOptions
from ember_memory_abi.memory import MemoryDraft
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.errors import EngineBusyError, EngineClosedError, WriteOutcomeUnknownError


async def _wait_started(event: Event) -> None:
    async with asyncio.timeout(3):
        while not event.is_set():
            await asyncio.sleep(0.001)


def test_busy_writer_rejects_admission_and_connections_stay_thread_owned(
    store: SqliteMemoryStore,
) -> None:
    started, release = Event(), Event()

    def block(connection: sqlite3.Connection) -> int:
        started.set()
        assert release.wait(5)
        return connection.execute("SELECT 1").fetchone()[0]

    active = store.submit(block, write=True)
    assert started.wait(3)
    queued: list[Future[int]] = []
    try:
        queued = [store.submit(lambda _: 1, write=True) for _ in range(15)]
        with pytest.raises(EngineBusyError):
            store.submit(lambda _: 1, write=True)
        assert store.database.writer.pending == 16
    finally:
        release.set()
    assert active.result(timeout=3) == 1
    assert all(job.result(timeout=3) == 1 for job in queued)
    owner_connection = store.submit(lambda connection: connection).result(timeout=3)
    with pytest.raises(sqlite3.ProgrammingError):
        owner_connection.execute("SELECT 1")


def test_cancelled_running_read_keeps_worker_owned_and_drain_is_async(
    store: SqliteMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = Event(), Event()
    original_submit = store.submit

    def submit[T](work: Callable[[sqlite3.Connection], T], *, write: bool = False) -> Future[T]:
        def delayed(connection: sqlite3.Connection) -> T:
            started.set()
            assert release.wait(5)
            return work(connection)

        return original_submit(delayed, write=write)

    monkeypatch.setattr(store, "submit", submit)
    client = InProcessAsyncMemoryClient(store)

    async def scenario() -> None:
        request = asyncio.create_task(client.search(MemoryQuery()))
        try:
            await _wait_started(started)
            # Several loop turns run while the owned SQLite worker is blocked.
            heartbeat = 0
            for _ in range(10):
                await asyncio.sleep(0)
                heartbeat += 1
            assert heartbeat == 10 and not request.done()
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert store.database.readers.pending == 1
            drain = asyncio.create_task(client.aclose())
            await asyncio.sleep(0)
            assert not drain.done()
            with pytest.raises(EngineClosedError):
                await client.search(MemoryQuery())
            release.set()
            assert await drain == ()
            assert store.database.readers.pending == 0
        finally:
            release.set()
            await client.aclose()

    asyncio.run(scenario())


def test_queued_mutation_cancelled_before_execution_never_commits(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    started, release = Event(), Event()

    def block(_: sqlite3.Connection) -> None:
        started.set()
        assert release.wait(5)

    occupied = store.submit(block, write=True)
    client = InProcessAsyncMemoryClient(store)

    async def scenario() -> None:
        await _wait_started(started)
        task = asyncio.create_task(
            client.remember(RememberRequest(idempotency_key="queued", draft=draft))
        )
        try:
            await asyncio.sleep(0)
            assert store.database.writer.pending == 2
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert store.database.writer.pending == 1
            assert await client.aclose() == ()
        finally:
            release.set()

    asyncio.run(scenario())
    occupied.result(timeout=3)
    assert store.search(MemoryQuery()).results == ()


def test_write_timeout_after_commit_resolves_with_identical_key(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    committed, release = Event(), Event()
    original_submit = store.submit

    def submit[T](work: Callable[[sqlite3.Connection], T], *, write: bool = False) -> Future[T]:
        def delayed(connection: sqlite3.Connection) -> T:
            result = work(connection)
            committed.set()
            assert release.wait(5)
            return result

        return original_submit(delayed, write=write)

    monkeypatch.setattr(store, "submit", submit)
    client = InProcessAsyncMemoryClient(store)
    request = RememberRequest(
        idempotency_key="uncertain",
        draft=draft,
        options=RequestOptions(deadline_at="2026-10-07T12:00:00.050000Z"),
    )

    async def scenario() -> None:
        try:
            with pytest.raises(WriteOutcomeUnknownError):
                await client.remember(request)
            assert committed.is_set()
            assert store.database.writer.pending == 1
        finally:
            release.set()
            assert await client.aclose() == ()

    asyncio.run(scenario())
    replay = store.remember(RememberRequest(idempotency_key="uncertain", draft=draft))
    assert replay.outcome == "already_applied"
    assert len(store.search(MemoryQuery()).results) == 1


def test_expired_admission_and_structured_worker_failures(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    from ember_memory_abi.errors import AccessDeniedError
    from ember_memory_engine.engine import CanonicalEngine

    client = InProcessAsyncMemoryClient(store)

    async def scenario() -> None:
        with pytest.raises(DeadlineExceededError):
            await client.search(
                MemoryQuery(options=RequestOptions(deadline_at="2026-10-07T11:59:59.000000Z"))
            )
        assert store.database.readers.pending == 0
        # A readonly facade's failure is retained safely for its owner at drain.
        engine = store.state.resolver
        assert isinstance(engine, CanonicalEngine)
        readonly = InProcessAsyncMemoryClient(engine.character(store.character_id))
        with pytest.raises(AccessDeniedError):
            await readonly.remember(RememberRequest(idempotency_key="readonly", draft=draft))
        failures = await readonly.aclose()
        assert len(failures) == 1 and failures[0].code == "access_denied"
        assert await client.aclose() == ()

    asyncio.run(scenario())
