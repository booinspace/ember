"""Phase 9 operational state regression tests."""

import asyncio
import sqlite3
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest

from ember_agent.runtime.task_store import TaskCancelled, TaskConflict, TaskStore, TaskSubmission


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
        with closing(sqlite3.connect(store.path)) as db:
            attempts = db.execute(
                "SELECT task_id, outcome, finished_at FROM task_attempt ORDER BY started_at"
            ).fetchall()
        assert [(row[0], row[1]) for row in attempts] == [
            (str(handle.task_id), "cancelled"),
            (str(second.task_id), "uncertain"),
        ]
        assert all(row[2] is not None for row in attempts)

    asyncio.run(exercise())


def test_reserved_queue_capacity_and_expiry(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        request = TaskSubmission(
            character_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            idempotency_key="running",
            goal="Synthetic fixture",
        )
        running = await store.submit(request)
        assert await store.claim(request.character_id) is not None
        queued = [
            await store.submit(
                request.model_copy(update={"idempotency_key": f"build-{i}", "operation": "build"})
            )
            for i in range(32)
        ]
        assert await store.claim(request.character_id, realtime_reserved=True) is None
        active = await store.active(request.character_id)
        assert len(active) == 33
        expected_ids = [running.task_id, *(item.task_id for item in queued)]
        assert [entry.task_id for entry in active] == expected_ids
        assert {entry.state for entry in active[1:]} == {"waiting_for_realtime"}
        with pytest.raises(TaskConflict, match="task_queue_full"):
            await store.submit(request.model_copy(update={"idempotency_key": "overflow"}))
        expired = queued[0].task_id
        with closing(sqlite3.connect(store.path)) as db, db:
            db.execute("UPDATE task SET deadline_at=0 WHERE id=?", (str(expired),))
            assert db.execute("SELECT task_id FROM task_attempt").fetchall() == [
                (str(running.task_id),)
            ]
        assert await store.claim(request.character_id, realtime_reserved=True) is None
        snapshot = await store.status(expired, request.character_id)
        assert snapshot is not None and snapshot.state == "timed_out"

    asyncio.run(exercise())


def test_summary_dispatches_through_p2_gateway_while_connected(tmp_path: Path) -> None:
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
            client = HttpGatewayClient("http://test", "agent", client=http, idle_managed_tasks=True)
            session = uuid4()
            await gateway.session(session, connected=True)

            async def admission() -> tuple[bool, bool]:
                health = await client.health()
                return bool(health.realtime_sessions), "idle_managed_tasks_v1" in health.features

            character = uuid4()
            router = TaskRouter(
                tmp_path,
                character,
                uuid4(),
                TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
                summary=SummaryTask(client, config.model_tag),
            )
            router.task_admission = admission
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


def test_attempt_table_is_additive_for_existing_runtime(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        request = TaskSubmission(
            character_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            idempotency_key="existing",
            goal="Synthetic input",
        )
        handle = await store.submit(request)
        with closing(sqlite3.connect(store.path)) as db, db:
            db.execute("DROP TABLE task_attempt")
        await store.initialize()
        claim = await store.claim(request.character_id)
        assert claim is not None and claim[0] == handle.task_id
        await store.finish(handle.task_id, "running", "failed")
        with closing(sqlite3.connect(store.path)) as db:
            assert db.execute(
                "SELECT outcome FROM task_attempt WHERE task_id=?", (str(handle.task_id),)
            ).fetchone() == ("failed",)

    asyncio.run(exercise())


def test_cancelled_progress_does_not_admit_next_step(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        request = TaskSubmission(
            character_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            idempotency_key="cancel",
            goal="synthetic",
        )
        handle = await store.submit(request)
        assert await store.claim(request.character_id) is not None
        await store.cancel(handle.task_id, request.character_id)
        with pytest.raises(TaskCancelled):
            await store.progress(handle.task_id, "running", "waiting_for_tool")
        await store.finish(handle.task_id, "waiting_for_tool", "cancelled")
        status = await store.status(handle.task_id, request.character_id)
        assert status is not None and status.state == "cancelled"

    asyncio.run(exercise())


def test_confirmation_is_scoped_to_exact_action_and_task(tmp_path: Path) -> None:
    from time import time

    from ember_agent.runtime.task_store import ConfirmationRequest

    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        request = TaskSubmission(
            character_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            idempotency_key="approval",
            goal="synthetic",
        )
        task = await store.submit(request)
        assert await store.claim(request.character_id) is not None
        action = ConfirmationRequest(
            task_id=task.task_id,
            tool="synthetic_tool",
            arguments={"value": "a"},
            deadline_at=time() + 30,
        )
        pending = await store.request_confirmation(request.character_id, action)
        assert await store.request_confirmation(request.character_id, action) == pending
        with pytest.raises(TaskConflict, match="confirmation_action_changed"):
            await store.request_confirmation(
                request.character_id, action.model_copy(update={"arguments": {"value": "b"}})
            )
        with pytest.raises(TaskConflict, match="confirmation_scope_or_action_mismatch"):
            await store.decide_confirmation(
                uuid4(), pending.confirmation_id, pending.action_sha256, approve=True
            )
        with pytest.raises(TaskConflict, match="confirmation_scope_or_action_mismatch"):
            await store.decide_confirmation(
                request.character_id, pending.confirmation_id, "0" * 64, approve=True
            )
        approved = await store.decide_confirmation(
            request.character_id, pending.confirmation_id, pending.action_sha256, approve=True
        )
        assert approved.state == "approved"
        assert (
            await store.decide_confirmation(
                request.character_id, pending.confirmation_id, pending.action_sha256, approve=True
            )
            == approved
        )
        with pytest.raises(TaskConflict, match="confirmation_decided"):
            await store.decide_confirmation(
                request.character_id, pending.confirmation_id, pending.action_sha256, approve=False
            )
        await store.cancel(task.task_id, request.character_id)
        with pytest.raises(TaskConflict, match="confirmation_task_unavailable"):
            await store.decide_confirmation(
                request.character_id, pending.confirmation_id, pending.action_sha256, approve=True
            )

    asyncio.run(exercise())


def test_cited_task_is_revalidated_at_delivery_and_artifact_export(tmp_path: Path) -> None:
    from ember_agent.runtime.task_router import (
        TaskResult,
        TaskRouter,
        ToolContext,
        ToolReceipt,
    )
    from ember_agent.runtime.task_store import TaskState
    from ember_memory_abi.evidence import SourceRef
    from ember_memory_abi.related import ContextDependencies

    class Provider:
        async def execute(
            self,
            request: TaskSubmission,
            context: ToolContext,
            progress: Callable[[TaskState], Awaitable[None]],
        ) -> TaskResult:
            del request, progress
            return TaskResult(
                summary="Synthetic supported result",
                receipt=ToolReceipt(context=context, tool="synthetic", data={}),
                citations=(source,),
                dependencies=dependencies,
            )

    async def exercise() -> None:
        nonlocal source, dependencies
        character = uuid4()
        source = SourceRef(
            source_store_id=character,
            record_kind="document",
            record_id=uuid4(),
            record_revision=1,
        )
        dependencies = ContextDependencies(policy_epoch=1, sources=(source,))
        allowed = True

        async def validate(_: ContextDependencies) -> bool:
            return allowed

        router = TaskRouter(tmp_path, character, uuid4(), Provider(), validate=validate)
        await router.start()
        try:
            handle = await router.submit("cited", "synthetic")
            async with asyncio.timeout(3):
                while True:
                    status = await router.status(handle.task_id)
                    if status is not None and status.state in {"succeeded", "failed"}:
                        break
                    await asyncio.sleep(0.01)
            assert status.state == "succeeded"
            assert status.result == "Synthetic supported result"
            allowed = False
            assert (await router.status(handle.task_id)).result is None
            assert (await router.notifications(None)).items[0].result is None
            with pytest.raises(TaskConflict, match="task_artifact_unavailable"):
                await router.save_artifact(handle.task_id)
            allowed = True
            source = SourceRef(
                source_store_id=character,
                record_kind="document",
                record_id=uuid4(),
                record_revision=1,
            )
            rejected = await router.submit("unscoped", "synthetic")
            async with asyncio.timeout(3):
                while True:
                    status = await router.status(rejected.task_id)
                    if status is not None and status.state in {"succeeded", "failed"}:
                        break
                    await asyncio.sleep(0.01)
            assert status.state == "failed" and status.result is None
        finally:
            await router.aclose()

    source: SourceRef
    dependencies: ContextDependencies
    asyncio.run(exercise())


def test_router_cancellation_stops_inflight_read_only_provider(tmp_path: Path) -> None:
    from ember_agent.runtime.task_router import TaskResult, TaskRouter, ToolContext
    from ember_agent.runtime.task_store import TaskState

    async def exercise() -> None:
        started = asyncio.Event()
        stopped = asyncio.Event()

        class Provider:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                await progress("waiting_for_tool")
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
                raise AssertionError("Provider should be interrupted")

        router = TaskRouter(tmp_path, uuid4(), uuid4(), Provider())
        await router.start()
        try:
            handle = await router.submit("cancel-active", "Synthetic input")
            async with asyncio.timeout(3):
                await started.wait()
                await router.cancel(handle.task_id)
                await stopped.wait()
                while True:
                    status = await router.status(handle.task_id)
                    if status is not None and status.state == "cancelled":
                        break
                    await asyncio.sleep(0.01)
            assert status.result is None
            items = (await router.notifications(None)).items
            assert items == (status,)
        finally:
            await router.aclose()

    asyncio.run(exercise())


def test_build_cancel_at_commit_fences_failed_cleanup_without_stopping_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import UUID

    from ember_agent.runtime.task_router import (
        TaskResult,
        TaskRouter,
        TextInspectionProvider,
        TextInspectionTask,
        ToolContext,
        ToolReceipt,
        ToolRouter,
    )
    from ember_agent.runtime.task_store import TaskState, TerminalState

    async def exercise() -> None:
        character = uuid4()
        native = TextInspectionTask(ToolRouter(TextInspectionProvider(), character))
        router = TaskRouter(tmp_path, character, uuid4(), native)
        finish = router.store.finish

        class Build:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                return TaskResult(
                    summary="Synthetic build",
                    receipt=ToolReceipt(
                        context=context,
                        tool="build",
                        data={"artifact": {"sha256": "0" * 64, "size_bytes": 22}},
                    ),
                )

        deleted: list[UUID] = []

        async def fail_delete(task_id: UUID) -> None:
            deleted.append(task_id)
            raise OSError("synthetic cleanup failure")

        build_id: UUID | None = None

        async def cancel_before_commit(
            task_id: UUID,
            expected: TaskState,
            state: TerminalState,
            *,
            result: str | None = None,
            receipt: str | None = None,
            artifact: tuple[str, int] | None = None,
        ) -> TerminalState:
            if artifact is not None:
                nonlocal build_id
                build_id = task_id
                assert state == "succeeded"
                await router.store.cancel(task_id, character)
            return await finish(
                task_id, expected, state, result=result, receipt=receipt, artifact=artifact
            )

        monkeypatch.setattr(router.store, "finish", cancel_before_commit)
        monkeypatch.setattr(router.build_artifacts, "delete", fail_delete)
        from ember_agent.runtime.task_router import TaskBinding

        router.bindings["build"] = TaskBinding(Build())
        await router.start()
        try:
            build = await router.submit("race", "Build a synthetic file", operation="build")
            inspect = await router.submit("after-race", "one two three")
            async with asyncio.timeout(3):
                while True:
                    status = await router.status(inspect.task_id)
                    if status is not None and status.state == "succeeded":
                        break
                    await asyncio.sleep(0.01)
            assert build_id == build.task_id
            assert deleted == [build.task_id]
            assert router.build_unavailable == "build_cleanup_failed"
            assert not router.build_available
            cancelled = await router.status(build.task_id)
            assert cancelled and cancelled.state == "cancelled" and not cancelled.build_artifact
            assert await router.active_build_service() is None
        finally:
            await router.aclose()

    asyncio.run(exercise())


def test_speech_claim_is_durable_scoped_and_does_not_ack_ui(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        request = TaskSubmission(
            character_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            idempotency_key="speech",
            goal="Synthetic result",
        )
        handle = await store.submit(request)
        await store.claim(request.character_id)
        await store.finish(handle.task_id, "running", "succeeded", result="Ready")
        assert await store.claim_speech(uuid4(), request.session_id) is None
        assert await store.claim_speech(request.character_id, uuid4()) is None
        assert (
            await store.claim_speech(request.character_id, request.session_id, turn_id=uuid4())
            is None
        )
        claims = await asyncio.gather(
            *(store.claim_speech(request.character_id, request.session_id) for _ in range(4))
        )
        assert claims.count(handle.task_id) == 1
        reopened = TaskStore(tmp_path)
        await reopened.initialize()
        assert await reopened.claim_speech(request.character_id, request.session_id) is None
        assert (await reopened.notifications(request.character_id))[0].task_id == handle.task_id
        await reopened.reconcile(request.character_id)
        with closing(sqlite3.connect(reopened.path)) as db:
            assert db.execute("SELECT state FROM task_speech_delivery").fetchone() == ("uncertain",)
        assert await reopened.claim_speech(request.character_id, request.session_id) is None
        with pytest.raises(TaskConflict, match="speech_delivery_state_conflict"):
            await reopened.finish_speech(handle.task_id, request.session_id, completed=True)

    asyncio.run(exercise())


def test_speech_worker_records_interrupted_playout_without_replay(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from ember_agent.realtime.session import HostedRoom
    from ember_agent.runtime.task_router import (
        TaskRouter,
        TextInspectionProvider,
        TextInspectionTask,
        ToolRouter,
    )

    async def exercise() -> None:
        character = uuid4()
        router = TaskRouter(
            tmp_path,
            character,
            uuid4(),
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
        )
        await router.start()
        entered = asyncio.Event()
        interrupted = asyncio.Event()

        class Speech:
            _tasks: list[asyncio.Task[object]] = []

            def __init__(self, text: AsyncIterable[str]) -> None:
                self.text = text
                self.consumed = False

            async def wait_for_playout(self) -> None:
                if not self.consumed:
                    assert [chunk async for chunk in self.text] == [
                        "A task result is ready in Tasks."
                    ]
                    self.consumed = True
                entered.set()
                await interrupted.wait()

            def interrupt(self, *, force: bool) -> None:
                assert force
                interrupted.set()

        class Session:
            def say(self, text: AsyncIterable[str], **settings: object) -> Speech:
                assert settings == {"allow_interruptions": True, "add_to_chat_ctx": False}
                return Speech(text)

        host = SimpleNamespace(
            closing=False,
            core=SimpleNamespace(history=[]),
            session=Session(),
            _can_announce_tasks=lambda: True,
            _notice_text=lambda _: notice(),
            _finish_announcement=lambda *args: HostedRoom._finish_announcement(host, *args),
            _settle_speech=lambda *args, **kwargs: HostedRoom._settle_speech(host, *args, **kwargs),
            _speech_finished=lambda _: asyncio.sleep(0),
            _linked_speech_tasks=HostedRoom._linked_speech_tasks,
            _wake_capture=lambda: None,
            _announcement=None,
        )

        async def notice() -> AsyncIterator[str]:
            yield "A task result is ready in Tasks."

        worker = asyncio.create_task(HostedRoom._announce_tasks(host, router))
        try:
            handle = await router.submit("speech-worker", "Synthetic input")
            async with asyncio.timeout(5):
                await entered.wait()
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                await interrupted.wait()
            assert await router.store.claim_speech(character, router.session_id) is None
            assert (await router.notifications(None)).items[0].task_id == handle.task_id
            with closing(sqlite3.connect(router.store.path)) as db:
                state = db.execute(
                    "SELECT state FROM task_speech_delivery WHERE task_id=?", (str(handle.task_id),)
                ).fetchone()
            assert state == ("uncertain",)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await router.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "blocked",
    [
        "core_busy",
        "pending_delivery",
        "prepared",
        "active_turn",
        "current_speech",
        "user_speaking",
        "agent_thinking",
        "disconnected",
        "closing",
    ],
)
def test_task_announcements_respect_full_turn_and_speech_fence(blocked: str) -> None:
    from types import SimpleNamespace

    from ember_agent.realtime.session import HostedRoom

    core = SimpleNamespace(busy=False, pending_delivery=None)
    gateway = SimpleNamespace(prepared=None, active_turn=None)
    session = SimpleNamespace(current_speech=None, agent_state="listening", user_state="listening")
    host = SimpleNamespace(
        closing=False,
        busy=False,
        core=core,
        session=session,
        agent=SimpleNamespace(gateway_llm=gateway),
        room=SimpleNamespace(remote_participants={"user": SimpleNamespace(identity="ember-user")}),
        _announcement=None,
        _stops={},
    )
    assert HostedRoom._can_announce_tasks(host)
    if blocked == "core_busy":
        core.busy = True
    elif blocked == "pending_delivery":
        core.pending_delivery = object()
    elif blocked == "prepared":
        gateway.prepared = object()
    elif blocked == "active_turn":
        gateway.active_turn = object()
    elif blocked == "current_speech":
        session.current_speech = object()
    elif blocked == "user_speaking":
        session.user_state = "speaking"
    elif blocked == "agent_thinking":
        session.agent_state = "thinking"
    elif blocked == "disconnected":
        host.room.remote_participants.clear()
    else:
        host.closing = True
    assert not HostedRoom._can_announce_tasks(host)


def test_confirmed_effect_fence_prevents_replay_across_restart(tmp_path: Path) -> None:
    from time import time

    from ember_agent.runtime.task_store import ConfirmationRequest

    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        request = TaskSubmission(
            character_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            idempotency_key="effect",
            goal="Synthetic draft",
        )
        task = await store.submit(request)
        await store.claim(request.character_id)
        action = await store.request_confirmation(
            request.character_id,
            ConfirmationRequest(
                task_id=task.task_id,
                tool="synthetic_write",
                arguments={"target": "draft"},
                deadline_at=time() + 60,
            ),
        )
        with pytest.raises(TaskConflict, match="effect_authority_invalid"):
            await store.begin_confirmed_effect(
                request.character_id, action.confirmation_id, action.action_sha256
            )
        await store.decide_confirmation(
            request.character_id, action.confirmation_id, action.action_sha256, approve=True
        )
        with pytest.raises(TaskConflict, match="effect_authority_invalid"):
            await store.begin_confirmed_effect(
                uuid4(), action.confirmation_id, action.action_sha256
            )
        with pytest.raises(TaskConflict, match="effect_authority_invalid"):
            await store.begin_confirmed_effect(
                request.character_id, action.confirmation_id, "0" * 64
            )
        outcomes = await asyncio.gather(
            *(
                store.begin_confirmed_effect(
                    request.character_id, action.confirmation_id, action.action_sha256
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert sum(result is None for result in outcomes) == 1
        assert sum(isinstance(result, TaskConflict) for result in outcomes) == 1
        await store.reconcile(request.character_id)
        with closing(sqlite3.connect(store.path)) as db:
            assert db.execute("SELECT state FROM task_effect").fetchone() == ("uncertain",)
        with pytest.raises(TaskConflict):
            await store.begin_confirmed_effect(
                request.character_id, action.confirmation_id, action.action_sha256
            )
        with pytest.raises(TaskConflict, match="effect_state_conflict"):
            await store.finish_confirmed_effect(
                request.character_id, action.confirmation_id, receipt={"ok": True}
            )

    asyncio.run(exercise())


def test_tool_and_confirmation_respect_remaining_task_deadline(tmp_path: Path) -> None:
    from time import time

    from ember_agent.runtime.task_artifacts import TaskArtifacts
    from ember_agent.runtime.task_router import (
        DraftReviewTask,
        EventBus,
        TextInspectionProvider,
        ToolContext,
        ToolRouter,
    )

    async def exercise() -> None:
        store = TaskStore(tmp_path)
        await store.initialize()
        request = TaskSubmission(
            character_id=uuid4(),
            session_id=uuid4(),
            turn_id=uuid4(),
            idempotency_key="deadline",
            goal="Synthetic draft",
            operation="review",
        )
        handle = await store.submit(request)
        await store.claim(request.character_id)
        context = ToolContext(
            character_id=request.character_id,
            session_id=request.session_id,
            turn_id=request.turn_id,
            task_id=handle.task_id,
            deadline_at=time() + 5,
        )
        tools = ToolRouter(TextInspectionProvider(), request.character_id)
        with pytest.raises(TaskConflict, match="tool_deadline_exceeded"):
            await tools.invoke(
                "inspect_text",
                {"text": "synthetic"},
                context.model_copy(update={"deadline_at": time() - 1}),
                allowed=frozenset({"inspect_text"}),
            )
        events = EventBus()
        provider = DraftReviewTask(store, events, TaskArtifacts(tmp_path), request.character_id)

        async def progress(state: str) -> None:
            await store.progress(handle.task_id, "running", state)

        execution = asyncio.create_task(provider.execute(request, context, progress))
        try:
            async with asyncio.timeout(3):
                while not (pending := await store.pending_confirmations(request.character_id)):
                    await asyncio.sleep(0.01)
            assert pending[0].deadline_at <= context.deadline_at
            await store.decide_confirmation(
                request.character_id,
                pending[0].confirmation_id,
                pending[0].action_sha256,
                approve=False,
            )
            events.publish()
            with pytest.raises(TaskConflict, match="draft_not_approved"):
                await execution
        finally:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)

    asyncio.run(exercise())


def test_failed_confirmed_write_is_uncertain_and_never_retried(tmp_path: Path) -> None:
    from time import time

    from pydantic import JsonValue

    from ember_agent.runtime.task_router import (
        ConfirmedEffectRouter,
        TaskResult,
        TaskRouter,
        ToolContext,
        ToolReceipt,
    )
    from ember_agent.runtime.task_store import ConfirmationRequest, TaskState

    async def exercise() -> None:
        calls = 0
        waiting = asyncio.Event()
        decision = asyncio.Event()

        class Tool:
            capabilities = frozenset({"write"})

            async def invoke(
                self, name: str, arguments: dict[str, JsonValue], context: ToolContext
            ) -> ToolReceipt:
                nonlocal calls
                calls += 1
                raise OSError("Unknown remote write outcome")

        class Provider:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                await progress("waiting_for_user")
                confirmation = await router.store.request_confirmation(
                    context.character_id,
                    ConfirmationRequest(
                        task_id=context.task_id,
                        tool="write",
                        arguments={"text": request.goal},
                        deadline_at=time() + 30,
                    ),
                )
                waiting.set()
                await decision.wait()
                await progress("running")
                broker = ConfirmedEffectRouter(router.store, Tool(), context.character_id)
                receipt = await broker.invoke(
                    confirmation, {"text": request.goal}, context, allowed=frozenset({"write"})
                )
                return TaskResult(summary="Done", receipt=receipt)

        router = TaskRouter(tmp_path, uuid4(), uuid4(), Provider())
        await router.start()
        try:
            task = await router.submit("unknown-write", "synthetic")
            async with asyncio.timeout(5):
                await waiting.wait()
                pending = await router.confirmations()
                assert len(pending) == 1
                await router.decide(
                    pending[0].confirmation_id, pending[0].action_sha256, approve=True
                )
                decision.set()
                while True:
                    status = await router.status(task.task_id)
                    if status is not None and status.state == "uncertain":
                        break
                    await asyncio.sleep(0.01)
            assert calls == 1
            assert (await router.submit("unknown-write", "synthetic")).state == "uncertain"
            with closing(sqlite3.connect(router.store.path)) as db:
                assert db.execute("SELECT state FROM task_effect").fetchone() == ("uncertain",)
        finally:
            await router.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["cancel", "malformed"])
def test_effect_cancellation_and_bad_receipt_are_uncertain(tmp_path: Path, mode: str) -> None:
    from time import time

    from pydantic import JsonValue

    from ember_agent.runtime.task_router import (
        ConfirmedEffectRouter,
        TaskResult,
        TaskRouter,
        ToolContext,
        ToolReceipt,
    )
    from ember_agent.runtime.task_store import ConfirmationRequest, TaskState

    async def exercise() -> None:
        started = asyncio.Event()
        ready = asyncio.Event()
        decision = asyncio.Event()

        class Tool:
            capabilities = frozenset({"write"})

            async def invoke(
                self, name: str, arguments: dict[str, JsonValue], context: ToolContext
            ) -> ToolReceipt:
                started.set()
                if mode == "cancel":
                    await asyncio.Event().wait()
                return ToolReceipt(
                    context=context.model_copy(update={"task_id": uuid4()}), tool=name, data={}
                )

        class Provider:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                await progress("waiting_for_user")
                confirmation = await router.store.request_confirmation(
                    context.character_id,
                    ConfirmationRequest(
                        task_id=context.task_id,
                        tool="write",
                        arguments={"text": request.goal},
                        deadline_at=time() + 30,
                    ),
                )
                ready.set()
                await decision.wait()
                await progress("running")
                receipt = await ConfirmedEffectRouter(
                    router.store, Tool(), context.character_id
                ).invoke(
                    confirmation, {"text": request.goal}, context, allowed=frozenset({"write"})
                )
                return TaskResult(summary="Done", receipt=receipt)

        router = TaskRouter(tmp_path, uuid4(), uuid4(), Provider())
        await router.start()
        try:
            task = await router.submit("effect", "synthetic")
            async with asyncio.timeout(5):
                await ready.wait()
                confirmation = (await router.confirmations())[0]
                await router.decide(
                    confirmation.confirmation_id, confirmation.action_sha256, approve=True
                )
                decision.set()
                await started.wait()
                if mode == "cancel":
                    await router.cancel(task.task_id)
                while True:
                    status = await router.status(task.task_id)
                    if status is not None and status.state in {"uncertain", "failed", "cancelled"}:
                        break
                    await asyncio.sleep(0.01)
            assert status.state == "uncertain"
            assert status.result is None
            with closing(sqlite3.connect(router.store.path)) as db:
                assert db.execute("SELECT state FROM task_effect").fetchone() == ("uncertain",)
        finally:
            await router.aclose()

    asyncio.run(exercise())
