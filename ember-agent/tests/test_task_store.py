"""Phase 9 operational state regression tests."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from ember_agent.runtime.task_store import TaskConflict, TaskStore, TaskSubmission


def test_durable_retries_and_terminal_outbox(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        request = TaskSubmission(
            character_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            idempotency_key="synthetic",
            goal="Collect synthetic results",
        )
        first, retry = await asyncio.gather(store.submit(request), store.submit(request))
        assert first == retry
        reopened = TaskStore(tmp_path)
        restarted_request = request.model_copy(update={"session_id": uuid4(), "turn_id": uuid4()})
        assert await reopened.submit(restarted_request) == first
        with pytest.raises(TaskConflict, match="idempotency_key_reused"):
            await reopened.submit(request.model_copy(update={"goal": "different"}))
        await reopened.finish(first.task_id, "queued", "cancelled")
        assert (await store.submit(request)).state == "cancelled"
        with pytest.raises(TaskConflict, match="task_state_conflict"):
            await store.finish(first.task_id, "queued", "failed")
        with closing(sqlite3.connect(store.path)) as db:
            assert db.execute("SELECT state, delivery FROM terminal_outbox").fetchall() == [
                ("cancelled", "pending")
            ]

    asyncio.run(exercise())


def test_restart_and_cancellation_preserve_terminal_outcome(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        request = TaskSubmission(
            character_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            idempotency_key="first",
            goal="Synthetic work",
        )
        handle = await store.submit(request)
        claim = await store.claim(request.character_id)
        assert claim is not None and claim[:2] == (handle.task_id, request)
        assert await store.status(handle.task_id, uuid4()) is None
        await store.cancel(handle.task_id, request.character_id)
        snapshot = await store.status(handle.task_id, request.character_id)
        assert snapshot is not None and snapshot.state == "running"
        assert snapshot.cancellation_requested
        await store.finish(handle.task_id, "running", "succeeded", result="late")
        snapshot = await store.status(handle.task_id, request.character_id)
        assert snapshot is not None and snapshot.state == "cancelled" and snapshot.result is None

        interrupted = request.model_copy(update={"idempotency_key": "interrupted"})
        second = await store.submit(interrupted)
        claim = await store.claim(request.character_id)
        assert claim is not None and claim[:2] == (second.task_id, interrupted)
        reopened = TaskStore(tmp_path)
        await reopened.reconcile(request.character_id)
        await reopened.reconcile(request.character_id)
        snapshot = await reopened.status(second.task_id, request.character_id)
        assert snapshot is not None and snapshot.state == "uncertain"
        assert await reopened.claim(request.character_id) is None

    asyncio.run(exercise())


def test_summary_dispatches_through_p2_gateway(tmp_path: Path) -> None:
    import httpx

    from ember_agent.inference.client import HttpGatewayClient
    from ember_agent.inference.gateway import P0Gateway
    from ember_agent.inference.service import create_app
    from ember_agent.runtime.task_router import (
        SummaryTask,
        TaskRouter,
        TextInspectionProvider,
        TextInspectionTask,
        ToolRouter,
    )

    from .test_gateway import FakeUpstream, configuration

    async def exercise() -> None:
        config = configuration(tmp_path)
        native = FakeUpstream()
        gateway = P0Gateway(config, native)
        app = create_app(gateway, "foreground", agent_token="agent")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            client = HttpGatewayClient("http://test", "agent", client=http)
            character = uuid4()
            router = TaskRouter(
                tmp_path,
                character,
                uuid4(),
                TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
                summary=SummaryTask(client, config.model_tag),
            )
            await router.start()
            try:
                handle = await router.submit("summary", "Synthetic content", operation="summarize")
                async with asyncio.timeout(3):
                    while True:
                        result = await router.status(handle.task_id)
                        if result is not None and result.state == "succeeded":
                            break
                        await asyncio.sleep(0.01)
                assert result.result == "visible"
                assert native.count == 1
                assert (await router.notifications(None)).items[0].task_id == handle.task_id
            finally:
                await router.aclose()
                await gateway.aclose()

    asyncio.run(exercise())
