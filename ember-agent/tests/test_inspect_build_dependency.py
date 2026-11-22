"""Synthetic checks for durable same-turn Inspect and Build handoffs."""

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ember_agent.runtime.task_router import TaskResult, TaskRouter, ToolContext, ToolReceipt
from ember_agent.runtime.task_store import (
    INSPECT_REFERENCE_BYTES,
    INSPECT_REFERENCE_HEADER,
    MAX_DEPENDENT_GOAL,
    TASK_GOAL_LIMIT,
    TaskConflict,
    TaskHandle,
    TaskState,
    TaskStore,
    TaskSubmission,
)


def _attempts(store: TaskStore) -> set[UUID]:
    with closing(sqlite3.connect(store.path)) as db:
        return {UUID(row[0]) for row in db.execute("SELECT task_id FROM task_attempt")}


def _expire(store: TaskStore, task_id: UUID) -> None:
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("UPDATE task SET deadline_at=0 WHERE id=?", (str(task_id),))


def _requests() -> tuple[TaskSubmission, TaskSubmission]:
    inspect = TaskSubmission(
        character_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        idempotency_key="inspect",
        goal="Inspect synthetic fixture",
        operation="delegate",
    )
    build = inspect.model_copy(
        update={"idempotency_key": "build", "operation": "build", "goal": "Build fixture"}
    )
    return inspect, build


@pytest.mark.parametrize("foreign", ["missing", "character", "turn", "operation"])
def test_dependency_requires_exact_scoped_delegate(tmp_path: Path, foreign: str) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        inspect, build = _requests()
        if foreign == "character":
            inspect = inspect.model_copy(update={"character_id": uuid4()})
        if foreign == "turn":
            inspect = inspect.model_copy(update={"turn_id": uuid4()})
        if foreign == "operation":
            inspect = inspect.model_copy(update={"operation": "inspect"})
        parent = await store.submit(inspect)
        dependency = uuid4() if foreign == "missing" else parent.task_id
        with pytest.raises(TaskConflict, match="inspect_dependency_mismatch"):
            await store.submit(build.model_copy(update={"depends_on": dependency}))
        assert all(item.operation != "build" for item in await store.active(build.character_id))
        assert not _attempts(store)

    asyncio.run(exercise())


def test_reopened_queue_uses_exact_success_and_defers_without_attempt(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        inspect, build = _requests()
        build = build.model_copy(update={"goal": "x" * MAX_DEPENDENT_GOAL})
        unrelated = await store.submit(inspect.model_copy(update={"idempotency_key": "other"}))
        claim = await store.claim(inspect.character_id)
        assert claim is not None and claim[0] == unrelated.task_id
        await store.finish(
            unrelated.task_id,
            "running",
            "succeeded",
            result="UNRELATED",
            receipt="fixture",
            inspected_workspace=True,
        )
        parent, child = await store.submit_inspect_build(inspect, build)
        native = await store.submit(
            inspect.model_copy(update={"operation": "inspect", "idempotency_key": "native"})
        )
        reopened = TaskStore(tmp_path)
        await reopened.initialize()
        await reopened.reconcile(inspect.character_id)
        claim = await reopened.claim(inspect.character_id, realtime_reserved=True)
        assert claim is not None and claim[0] == native.task_id
        waiting = await reopened.status(parent.task_id, inspect.character_id)
        blocked = await reopened.status(child.task_id, inspect.character_id)
        assert waiting and waiting.state == "waiting_for_realtime"
        assert blocked and blocked.state == "queued"
        assert _attempts(reopened) == {native.task_id, unrelated.task_id}
        claim = await reopened.claim(inspect.character_id)
        assert claim is not None and claim[0] == parent.task_id
        summary = 'Synthetic finding "quoted"\x00: ' + "é" * 4096 + "TAIL"
        await reopened.finish(
            parent.task_id,
            "running",
            "succeeded",
            result=summary,
            receipt="NOT_IN_GOAL",
            inspected_workspace=True,
        )
        assert await reopened.claim(inspect.character_id, realtime_reserved=True) is None
        waiting = await reopened.status(child.task_id, inspect.character_id)
        assert waiting and waiting.state == "waiting_for_realtime"
        assert child.task_id not in _attempts(reopened)
        await store.reconcile(inspect.character_id)
        claim = await store.claim(inspect.character_id)
        assert claim is not None and claim[0] == child.task_id
        goal = claim[1].goal
        original, reference = goal.split(INSPECT_REFERENCE_HEADER)
        assert original == build.goal and len(goal) <= TASK_GOAL_LIMIT
        excerpt = json.loads(reference)
        assert excerpt.startswith('Synthetic finding "quoted": ')
        assert len(excerpt.encode()) <= INSPECT_REFERENCE_BYTES
        assert all(value not in goal for value in ("TAIL", "NOT_IN_GOAL", "UNRELATED", "\x00"))
        assert child.task_id in _attempts(store)
        with closing(sqlite3.connect(store.path)) as db:
            row = db.execute("SELECT request_json FROM task WHERE id=?", (str(child.task_id),))
            stored = TaskSubmission.model_validate_json(row.fetchone()[0])
            assert stored.depends_on == parent.task_id and stored.goal == build.goal

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("transition", "outcome"),
    [
        ("failed", "failed"),
        ("cancel_queued", "cancelled"),
        ("cancel_race", "cancelled"),
        ("expire_child", "timed_out"),
        ("expire_parent", "failed"),
        ("expire_success", "failed"),
        ("interrupt", "failed"),
        ("no_receipt", "failed"),
    ],
)
def test_blocked_build_settles_without_attempt(
    tmp_path: Path, transition: str, outcome: str
) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        inspect, build = _requests()
        parent, child = await store.submit_inspect_build(inspect, build)
        if transition == "cancel_queued":
            await store.cancel(parent.task_id, inspect.character_id)
        elif transition == "expire_parent":
            _expire(store, parent.task_id)
        else:
            claim = await store.claim(inspect.character_id)
            assert claim is not None and claim[0] == parent.task_id
            if transition == "failed":
                await store.finish(parent.task_id, "running", "failed")
            elif transition == "cancel_race":
                await store.cancel(parent.task_id, inspect.character_id)
                assert await store.claim(inspect.character_id) is None
                await store.finish(
                    parent.task_id,
                    "running",
                    "succeeded",
                    result="late summary",
                    receipt="late receipt",
                    inspected_workspace=True,
                )
            elif transition == "expire_child":
                _expire(store, child.task_id)
            elif transition == "no_receipt":
                await store.finish(parent.task_id, "running", "succeeded", result="untrusted")
            elif transition == "expire_success":
                await store.finish(
                    parent.task_id,
                    "running",
                    "succeeded",
                    result="stale summary",
                    receipt="fixture",
                    inspected_workspace=True,
                )
                _expire(store, parent.task_id)
        restarted = TaskStore(tmp_path)
        await restarted.reconcile(inspect.character_id)
        assert await restarted.claim(inspect.character_id) is None
        snapshot = await restarted.status(child.task_id, inspect.character_id)
        assert snapshot and snapshot.state == outcome and snapshot.result is None
        assert child.task_id not in _attempts(restarted)
        notifications = await restarted.notifications(inspect.character_id)
        assert [item.state for item in notifications if item.task_id == child.task_id] == [outcome]

    asyncio.run(exercise())


def test_atomic_pair_capacity_and_retries(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        inspect, build = _requests()
        fillers = [
            await store.submit(inspect.model_copy(update={"idempotency_key": f"filler:{index}"}))
            for index in range(31)
        ]
        with pytest.raises(TaskConflict, match="task_queue_full"):
            await store.submit_inspect_build(inspect, build)
        assert {item.task_id for item in await store.active(inspect.character_id)} == {
            item.task_id for item in fillers
        }
        await store.cancel(fillers[0].task_id, inspect.character_id)
        pair, retry = await asyncio.gather(
            store.submit_inspect_build(inspect, build), store.submit_inspect_build(inspect, build)
        )
        assert pair == retry and len(await store.active(inspect.character_id)) == 32
        with pytest.raises(TaskConflict, match="idempotency_key_reused"):
            await store.submit_inspect_build(inspect, build.model_copy(update={"goal": "changed"}))

    asyncio.run(exercise())


def test_detached_submission_publishes_atomic_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        class NoWorker:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                raise AssertionError("Realtime reservation must defer managed work")

        async def reserved() -> bool:
            return True

        worker = NoWorker()
        router = TaskRouter(tmp_path, uuid4(), uuid4(), worker, delegate=worker)
        router.providers["build"] = worker
        router.realtime_reserved = reserved
        entered, release = asyncio.Event(), asyncio.Event()
        commit = router.store.submit_inspect_build

        async def delayed(
            inspect: TaskSubmission, build: TaskSubmission
        ) -> tuple[TaskHandle, TaskHandle]:
            entered.set()
            await release.wait()
            return await commit(inspect, build)

        monkeypatch.setattr(router.store, "submit_inspect_build", delayed)
        await router.start()
        try:
            submit = asyncio.create_task(
                router.submit_inspect_build("Inspect fixture", "Build fixture", uuid4())
            )
            await entered.wait()
            submit.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await submit
            async with asyncio.timeout(5):
                while True:
                    tasks = await router.store.active(router.character_id)
                    if any(task.state == "waiting_for_realtime" for task in tasks):
                        break
                    await asyncio.sleep(0.01)
            assert {task.operation for task in tasks} == {"delegate", "build"}
            assert not _attempts(router.store)
        finally:
            await router.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("qualified", [True, False])
def test_router_handoff_and_public_provenance(tmp_path: Path, qualified: bool) -> None:
    async def exercise() -> None:
        reserved = True
        seen: list[TaskSubmission] = []

        class Worker:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                seen.append(request)
                await progress("waiting_for_tool")
                if request.operation == "delegate":
                    return TaskResult(
                        summary="A synthetic workspace fixture.",
                        receipt=ToolReceipt(
                            context=context,
                            tool="delegate",
                            data={
                                "provider": "opencode",
                                "raw": "DO_NOT_FORWARD",
                                "tools": [
                                    {
                                        "name": "read" if qualified else "shell",
                                        "status": "completed",
                                    }
                                ],
                            },
                        ),
                    )
                return TaskResult(
                    summary="Synthetic Build",
                    receipt=ToolReceipt(
                        context=context,
                        tool="build",
                        data={
                            "artifact": {"sha256": "0" * 64, "size_bytes": 22},
                            "inspect_task_id": "fabricated",
                            "public_sources": [{"url": "https://fabricated.com/"}],
                        },
                    ),
                )

        async def probe() -> bool:
            return reserved

        worker = Worker()
        router = TaskRouter(tmp_path, uuid4(), uuid4(), worker, delegate=worker)
        router.providers["build"] = worker
        router.realtime_reserved = probe
        await router.start()
        try:
            turn = uuid4()
            for unsafe in (
                {"url": "https://example.com/guide?key=123", "title": "Private"},
                {"url": "https://example.com/guide", "title": "Page", "passage": "content"},
            ):
                with pytest.raises(TaskConflict, match="invalid_task_submission"):
                    await router.submit_inspect_build(
                        "Read synthetic fixture", "Build fixture", turn, public_sources=(unsafe,)
                    )
            assert not await router.store.active(router.character_id)
            sources = (
                {
                    "url": "https://example.com/guide",
                    "title": "Guide",
                    "provider": "exa",
                    "source_id": "source-1",
                },
            )
            parent, child = await router.submit_inspect_build(
                "Read synthetic fixture", "Build fixture", turn, public_sources=sources
            )
            async with asyncio.timeout(5):
                while True:
                    status = await router.status(parent.task_id)
                    if status and status.state == "waiting_for_realtime":
                        break
                    await asyncio.sleep(0.005)
                assert not seen and not _attempts(router.store)
                reserved = False
                while True:
                    status = await router.status(child.task_id)
                    if status and status.state in ("failed", "succeeded"):
                        break
                    await asyncio.sleep(0.005)
            if not qualified:
                assert status.state == "failed"
                assert [request.operation for request in seen] == ["delegate"]
                assert _attempts(router.store) == {parent.task_id}
                return
            assert status.state == "succeeded"
            assert [request.operation for request in seen] == ["delegate", "build"]
            goal = seen[-1].goal
            assert (
                json.loads(goal.split(INSPECT_REFERENCE_HEADER)[1])
                == "A synthetic workspace fixture."
            )
            assert "DO_NOT_FORWARD" not in goal and "https://example.com" not in goal
            assert seen[-1].public_sources is None and seen[-1].depends_on is None
            assert str(parent.task_id) not in goal
            result = await router.receipt(child.task_id)
            assert result.receipt and result.receipt.data["public_sources"] == list(sources)
            assert result.receipt.data["inspect_task_id"] == str(parent.task_id)
        finally:
            await router.aclose()

    asyncio.run(exercise())
