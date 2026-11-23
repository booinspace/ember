"""Conversation Build admission and realtime deferral regressions."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from time import time
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from ember_agent.character.build_intent import build_goal
from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayEvent, GatewayRequest
from ember_agent.runtime.task_router import (
    TaskBinding,
    TaskResult,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolContext,
    ToolReceipt,
    ToolRouter,
)
from ember_agent.runtime.task_store import TaskState, TaskSubmission
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient


class NoInference:
    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        raise AssertionError("Build admission must not use a model")
        yield


@pytest.mark.parametrize(
    ("text", "goal"),
    [
        ("Build it using the tool call", ""),
        ("Build this app", ""),
        ("Build: Make a stopwatch", "Make a stopwatch"),
        (
            "Could you build me a small webapp with a timer?",
            "Could you build me a small webapp with a timer?",
        ),
        ("Read fixture.txt", None),
        ("How do I build a webapp?", None),
        ("Build a healthy habit", None),
        ("Build the app we discussed", ""),
        ("Build: Build it", ""),
        ("Write the script for my wedding ceremony", None),
        ("Create an application for a replacement passport", None),
    ],
)
def test_build_goal(text: str, goal: str | None) -> None:
    assert build_goal(text) == goal


def test_build_turn_does_not_reach_model_or_replay_history(tmp_path: Path) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "canonical") as engine:
            memory = InProcessAsyncMemoryClient(
                engine.character(engine.create_character().store_id)
            )
            core = CharacterCore(memory, NoInference(), model_tag="fixture", recall_enabled=False)
            submit = AsyncMock(return_value="Build queued")
            core.submit_build = submit
            try:
                vague = await core.prepare("Build it using the tool call")
                assert vague.local_reply and "standalone" in vague.local_reply
                submit.assert_not_awaited()
                goal = "Build a small webapp with a timer"
                turn = await core.prepare(goal)
                assert turn.local_reply == "Build queued"
                submit.assert_awaited_once_with(goal, turn.request.request_id)
                assert not turn.request.tools
                core.submit_build = None
                unavailable = await core.prepare("Build: Make a stopwatch")
                assert unavailable.local_reply == "Build is unavailable. Inspect cannot build apps."
                assert not unavailable.request.tools
            finally:
                await memory.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("cancel", [True, False])
def test_build_waits_without_starting_provider(tmp_path: Path, cancel: bool) -> None:
    async def exercise() -> None:
        admitted = asyncio.Event()
        release = asyncio.Event()

        class Provider:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                assert context.deadline_at is not None and context.deadline_at > time() + 500
                admitted.set()
                await progress("waiting_for_tool")
                return TaskResult(
                    summary="synthetic",
                    receipt=ToolReceipt(context=context, tool="build", data={}),
                )

        async def reserved() -> tuple[bool, bool]:
            return not release.is_set(), False

        character = uuid4()
        router = TaskRouter(
            tmp_path,
            character,
            uuid4(),
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
        )
        router.bindings["build"] = TaskBinding(Provider())
        router.task_admission = reserved
        await router.start()
        try:
            identity = uuid4()
            assert (
                await router.submit_turn("Build a timer", identity, operation="build")
            ).startswith("Build task queued.")
            async with asyncio.timeout(5):
                while True:
                    active = (await router.notifications(None)).active
                    if active and active[0].state == "waiting_for_realtime":
                        break
                    await asyncio.sleep(0.01)
                task_id = active[0].task_id
                assert active[0].operation == "build" and not admitted.is_set()
                native = await router.submit("native", "Count my words", operation="inspect")
                while True:
                    status = await router.status(native.task_id)
                    if status and status.state == "succeeded":
                        break
                    await asyncio.sleep(0.01)
                assert not admitted.is_set()
                await router.store.reconcile(router.character_id)
                waiting = await router.status(task_id)
                assert waiting and waiting.state == "waiting_for_realtime"
                if cancel:
                    await router.cancel(task_id)
                else:
                    release.set()
                    await admitted.wait()
                while True:
                    snapshot = await router.status(task_id)
                    if snapshot and snapshot.state in ("cancelled", "failed"):
                        break
                    await asyncio.sleep(0.01)
                # Stub intentionally lacks a real Build artifact; commit must reject it.
                assert snapshot.state == ("cancelled" if cancel else "failed")
                assert admitted.is_set() is not cancel
        finally:
            release.set()
            await router.aclose()

    asyncio.run(exercise())


def test_shutdown_during_realtime_probe_does_not_start_build(tmp_path: Path) -> None:
    async def exercise() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()

        async def probe() -> tuple[bool, bool]:
            entered.set()
            await release.wait()
            return False, False

        character = uuid4()
        native = TextInspectionTask(ToolRouter(TextInspectionProvider(), character))
        router = TaskRouter(tmp_path, character, uuid4(), native)
        router.bindings["build"] = TaskBinding(native)
        router.task_admission = probe
        await router.start()
        try:
            handle = await router.submit("build", "Build a fixture", operation="build")
            async with asyncio.timeout(5):
                await entered.wait()
                shutdown = asyncio.create_task(router.aclose())
                await asyncio.sleep(0)
                release.set()
                await shutdown
            snapshot = await router.status(handle.task_id)
            assert snapshot and snapshot.state in {"queued", "waiting_for_realtime"}
        finally:
            release.set()
            await router.aclose()

    asyncio.run(exercise())
