"""Synthetic end-to-end investigations, cancellation and inspector policy revalidation."""

import asyncio
import sqlite3
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import pytest

from ember_agent.memory.deep_admission import DeepAdmission
from ember_agent.memory.deep_contracts import DeepRecallRequest, DeepRecallResult
from ember_agent.memory.deep_recall import DeepRecallService
from ember_memory_abi.interfaces import RememberRequest
from ember_memory_abi.memory import MemoryDraft
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.read_budget import ReadBudget, ReadBudgetExceededError

from .conftest import STAMP


def request(character_id: UUID) -> DeepRecallRequest:
    return DeepRecallRequest(
        request_id=uuid4(),
        character_id=character_id,
        context_id=uuid4(),
        query="primary GPU SYNTHETIC_A",
        reference_at=STAMP,
    )


def test_investigation_uses_exact_sources_and_invalidates_inspection(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    accepted = store.remember(RememberRequest(idempotency_key="accepted", draft=draft)).affected[0]

    async def exercise() -> None:
        foreground = InProcessAsyncMemoryClient(store)
        handles = []

        def factory(deadline: float) -> InProcessAsyncMemoryClient:
            budget = ReadBudget(deadline=deadline)
            handle = InProcessAsyncMemoryClient(
                store,
                deep_sources=DeepSources(engine, budget),
                read_budget=budget,
            )
            handles.append(handle)
            return handle

        service = DeepRecallService(store.character_id, factory, validation=foreground)
        query = request(store.character_id)
        try:
            result = await service.investigate(query)
            assert result.outcome in {"partial", "complete_within_budget"}, result.reasons
            assert [m.memory.memory_id for m in result.memories] == [accepted.memory_id], result
            assert result.observations[0].source.quote
            assert result.clusters[0].strength == "independent"
            assert result.counters.sql_statements > 0
            assert result.counters.materialized_bytes > 0
            assert await service.inspect(query.request_id, uuid4()) is None
            assert await service.inspect(query.request_id, query.context_id) == result
            store.configure_recall(RecallSelection(local_enabled=False, import_mode="none"))
            assert await service.inspect(query.request_id, query.context_id) is None
        finally:
            await service.aclose()
            await foreground.aclose()

    asyncio.run(exercise())


def test_native_meter_aborts_actual_materialization_and_restores_connection() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE records(value TEXT)")
        connection.executemany("INSERT INTO records VALUES(?)", [("synthetic",)] * 10)
        budget = ReadBudget(deadline=monotonic() + 5, rows=2)
        with pytest.raises(ReadBudgetExceededError):
            with budget.bind(connection):
                connection.execute("SELECT value FROM records").fetchall()
        assert budget.usage().materialized_rows == 3
        assert budget.usage().limits_hit == ("materialized_row_budget",)
        assert len(connection.execute("SELECT value FROM records").fetchall()) == 10


def test_cancellation_retains_admission_until_native_cleanup_drains() -> None:
    async def exercise() -> None:
        admission = DeepAdmission()
        first = request(uuid4())
        second = request(uuid4())
        entered = asyncio.Event()
        draining = asyncio.Event()
        release = asyncio.Event()
        next_entered = asyncio.Event()

        async def work() -> DeepRecallResult:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                draining.set()
                await release.wait()

        async def next_work() -> DeepRecallResult:
            next_entered.set()
            return DeepRecallResult(
                request_id=second.request_id,
                character_id=second.character_id,
                context_id=second.context_id,
                outcome="complete_within_budget",
            )

        task = asyncio.create_task(admission.submit(first, work))
        await entered.wait()
        next_task = asyncio.create_task(admission.submit(second, next_work))
        assert admission.cancel(first.request_id, first.character_id)
        await draining.wait()
        await asyncio.sleep(0)
        assert not next_entered.is_set()
        release.set()
        assert (await task).outcome == "cancelled"
        assert (await next_task).outcome == "complete_within_budget"
        await admission.aclose()

    asyncio.run(exercise())


def test_meter_setup_failure_does_not_poison_a_reused_native_connection() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        factory = connection.row_factory
        cache = connection.execute("PRAGMA cache_size").fetchone()[0]
        budget = ReadBudget(deadline=monotonic() + 5, materialized_bytes=1)
        with pytest.raises(ReadBudgetExceededError):
            with budget.bind(connection):
                pytest.fail("A failed setup cannot execute private reads.")
        assert connection.row_factory is factory
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        assert connection.execute("PRAGMA cache_size").fetchone()[0] == cache


def test_deep_handle_defers_when_an_actual_foreground_reader_is_owned(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
) -> None:
    import threading

    entered = threading.Event()
    released = threading.Event()

    def blocked(_connection: sqlite3.Connection) -> None:
        entered.set()
        assert released.wait(5)

    future = store.submit(blocked)
    assert entered.wait(2)

    async def exercise() -> None:
        foreground = InProcessAsyncMemoryClient(store)

        def factory(deadline: float) -> InProcessAsyncMemoryClient:
            budget = ReadBudget(deadline=deadline)
            return InProcessAsyncMemoryClient(
                store, deep_sources=DeepSources(engine, budget), read_budget=budget
            )

        service = DeepRecallService(store.character_id, factory, validation=foreground)
        query = request(store.character_id)
        try:
            result = await service.investigate(query)
            assert result.outcome == "deferred"
            assert result.reasons == ("deep_deferred_for_foreground",)
            assert result.counters.abi_calls == 1
            assert not result.memories and not result.observations
            assert await service.inspect(query.request_id, query.context_id) == result
        finally:
            released.set()
            await service.aclose()
            await foreground.aclose()

    try:
        asyncio.run(exercise())
    finally:
        released.set()
        future.result(timeout=5)


def test_conflict_is_visible_without_joining_independent_incidents(
    store: SqliteMemoryStore, draft: MemoryDraft
) -> None:
    from ember_agent.memory.deep_graph import connections, reference
    from ember_memory_abi.interfaces import MemoryResult
    from ember_memory_abi.related import MemoryRelationship

    original = store.remember(RememberRequest(idempotency_key="graph", draft=draft)).affected[0]
    memory = store.get(original.memory_id)
    other = memory.model_copy(
        update={
            "memory_id": uuid4(),
            "content": memory.content.model_copy(update={"evidence": ()}),
        }
    )
    results = (MemoryResult(memory=memory, score=0.8), MemoryResult(memory=other, score=0.8))
    edge = MemoryRelationship(
        relationship_id=uuid4(), kind="conflicts", source=reference(memory), target=reference(other)
    )
    associations, clusters = connections(results, (edge,), uuid4())
    assert len(associations) == 1 and associations[0].reason == "conflicting_claims"
    assert len(clusters) == 2 and all(cluster.strength == "independent" for cluster in clusters)
    transition = edge.model_copy(update={"kind": "supersedes"})
    _, corrected = connections(results, (transition,), uuid4())
    assert len(corrected) == 1 and corrected[0].strength == "explicit"


def test_explicit_alias_recalls_entity_valued_facts_without_literal_payload_names(
    tmp_path: Path,
) -> None:
    from ember_memory_abi.memory import Entity, EntityRef, EntityValue, FactPayload
    from ember_memory_engine import MutationAuthority
    from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject

    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        subject = ensure_subject(engine, character)
        acquaintance = EntityRef(store_id=character, entity_id=uuid4())
        engine.put_entity(
            character, Entity(reference=acquaintance, revision=1, name="Milo", aliases=("milo",))
        )
        author = MemoryAuthoringService(
            engine, subject, authority=MutationAuthority(mode="confirmed_user")
        )
        expected = set()
        for number in range(2):
            receipt = author.create(
                NewMemoryInput(
                    idempotency_key="entity:" + str(number),
                    text="I know Milo from the synthetic homelab club " + str(number),
                    payload=FactPayload(
                        subject=subject,
                        predicate="person.knows",
                        definition_version="0.1.0",
                        value=EntityValue(value=acquaintance),
                    ),
                )
            )
            expected.add(receipt.affected[0].memory_id)
        store = engine.character(character)
        assert all(
            "Milo" not in store.get(identity).content.payload.model_dump_json()
            for identity in expected
        )

        async def exercise() -> None:
            foreground = InProcessAsyncMemoryClient(store)

            def factory(deadline: float) -> InProcessAsyncMemoryClient:
                budget = ReadBudget(deadline=deadline)
                return InProcessAsyncMemoryClient(
                    store, deep_sources=DeepSources(engine, budget), read_budget=budget
                )

            service = DeepRecallService(character, factory, validation=foreground)
            query = request(character).model_copy(update={"query": "Milo"})
            try:
                result = await service.investigate(query)
                assert {item.memory.memory_id for item in result.memories} == expected
                assert all(item.ranking for item in result.memories)
                assert all(len(cluster.members) == 1 for cluster in result.clusters)
            finally:
                await service.aclose()
                await foreground.aclose()

        asyncio.run(exercise())


def test_task_deep_recall_revalidates_source_selection(
    tmp_path: Path,
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    from datetime import datetime

    from ember_agent.runtime.task_router import (
        RecallTask,
        TaskRouter,
        TextInspectionProvider,
        TextInspectionTask,
        ToolRouter,
    )

    accepted = store.remember(RememberRequest(idempotency_key="task-source", draft=draft)).affected[
        0
    ]

    async def exercise() -> None:
        foreground = InProcessAsyncMemoryClient(store)

        def factory(deadline: float) -> InProcessAsyncMemoryClient:
            budget = ReadBudget(deadline=deadline)
            return InProcessAsyncMemoryClient(
                store, deep_sources=DeepSources(engine, budget), read_budget=budget
            )

        service = DeepRecallService(store.character_id, factory, validation=foreground)
        router = TaskRouter(
            tmp_path / "runtime",
            store.character_id,
            uuid4(),
            TextInspectionTask(ToolRouter(TextInspectionProvider(), store.character_id)),
            recall=RecallTask(service, lambda: datetime.fromisoformat(STAMP), "UTC"),
        )
        await router.start()
        try:
            task = await router.submit("deep", "primary GPU SYNTHETIC_A", operation="recall")
            async with asyncio.timeout(5):
                while True:
                    status = await router.status(task.task_id)
                    if status is not None and status.state in {"succeeded", "failed"}:
                        break
                    await asyncio.sleep(0.01)
            assert status.state == "succeeded"
            result = await service.inspect(task.task_id, task.task_id)
            assert result is not None
            assert [m.memory.memory_id for m in result.memories] == [accepted.memory_id]
            assert await service.inspect(task.task_id, uuid4()) is None
            store.configure_recall(RecallSelection(local_enabled=False, import_mode="none"))
            assert await service.inspect(task.task_id, task.task_id) is None
        finally:
            await router.aclose()
            await service.aclose()
            await foreground.aclose()

    asyncio.run(exercise())
