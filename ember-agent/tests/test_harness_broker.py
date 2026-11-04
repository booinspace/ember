"""Harness requests use the same native/MCP grant, approval and effect owners as the UI."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import time
from uuid import uuid4

import pytest

from ember_agent.observability import ActivityLog
from ember_agent.runtime.mcp_tools import MCPTools
from ember_agent.runtime.task_router import (
    AgenticTaskProvider,
    HarnessToolBroker,
    TaskResult,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolContext,
    ToolRouter,
)
from ember_agent.runtime.task_store import TaskConflict, TaskSnapshot, TaskState, TaskSubmission


async def progress(_: TaskState) -> None:
    pass


def test_continuation_reserves_match_advertisement_dispatch_and_small_native_envelope() -> None:
    import json

    from ember_agent.runtime.hermes_tasks import broker_continuation_fits
    from ember_agent.runtime.task_router import ToolReceipt

    assert broker_continuation_fits(6400, "mcp.synthetic", 16384)
    assert not broker_continuation_fits(6401, "mcp.synthetic", 16384)
    assert not broker_continuation_fits(6400, "mcp.synthetic", 16384, call_units=769)
    assert broker_continuation_fits(14000, "native.inspect_text", 16384)
    assert not broker_continuation_fits(14000, "native.inspect_text", 16384, call_units=1500)
    context = ToolContext(
        character_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        task_id=uuid4(),
        deadline_at=1.2345678912345678e200,
    )
    largest = ToolReceipt(
        context=context, tool="inspect_text", data={"words": 8192, "characters": 8192}
    )
    content = json.dumps(
        {"receipt": largest.model_dump(mode="json"), "citation_id": "tool-30"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Include the escaped tool-role JSON and the bridge's per-message allowance.
    assert (
        len(json.dumps({"role": "tool", "content": content, "tool_call_id": "x" * 128}).encode())
        + 64
        < 1024
    )


def test_native_broker_only_accepts_the_issued_catalog_and_bound_character() -> None:
    async def run() -> None:
        character = uuid4()
        broker = HarnessToolBroker(character, None)
        catalog = broker.catalog(("native.inspect_text",))
        ctx = ToolContext(
            character_id=character,
            session_id=uuid4(),
            turn_id=uuid4(),
            task_id=uuid4(),
            deadline_at=time() + 5,
        )
        receipt = await broker.invoke(
            catalog, "native.inspect_text", {"text": "one two"}, ctx, progress
        )
        assert receipt.data == {"words": 2, "characters": 7}
        with pytest.raises(TaskConflict, match="harness_tool_scope_denied"):
            await broker.invoke((), "native.inspect_text", {"text": "one two"}, ctx, progress)
        with pytest.raises(TaskConflict, match="tool_scope_denied"):
            await broker.invoke(
                catalog,
                "native.inspect_text",
                {"text": "one two"},
                ctx.model_copy(update={"character_id": uuid4()}),
                progress,
            )

    asyncio.run(run())


def test_copied_mcp_context_is_revoked_on_credential_or_metadata_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .test_mcp_tools import identity, settings

    monkeypatch.setenv("EMBER_BROKER_FIXTURE_TOKEN", "synthetic-one")

    async def run() -> None:
        character, session = uuid4(), uuid4()
        tools = MCPTools(
            (settings(environment={"MCP_FIXTURE_TOKEN": "EMBER_BROKER_FIXTURE_TOKEN"}),),
            character,
            session,
        )
        await tools.start()
        router = TaskRouter(
            Path("unused"),
            character,
            session,
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
            mcp=tools,
        )
        try:
            tool = identity(tools, "echo")
            catalog = router.broker.catalog((tool,))
            assert router.broker.valid(catalog, (tool,))
            assert "synthetic-one" not in catalog[0].model_dump_json()
            monkeypatch.setenv("EMBER_BROKER_FIXTURE_TOKEN", "synthetic-two")
            assert not router.broker.valid(catalog, (tool,))
        finally:
            await tools.aclose()

    asyncio.run(run())


class BrokerWorkflow:
    def __init__(self, broker: HarnessToolBroker, tool: str) -> None:
        self.broker, self.tool = broker, tool

    async def execute(
        self,
        request: TaskSubmission,
        context: ToolContext,
        progress: Callable[[TaskState], Awaitable[None]],
    ) -> TaskResult:
        receipt = await self.broker.invoke(
            self.broker.catalog((self.tool,)), self.tool, {"text": request.goal}, context, progress
        )
        return TaskResult(summary="Broker completed", receipt=receipt)


@pytest.mark.parametrize("outcome", ["success", "cancel", "rotate"])
def test_harness_mcp_write_uses_exact_approval_and_emits_correlated_tool_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    from ember_agent.runtime.mcp_contracts import MCPToolGrant

    from .test_mcp_tasks import terminal
    from .test_mcp_tools import identity, settings

    calls = tmp_path / "calls.txt"
    monkeypatch.setenv("EMBER_BROKER_CALLS", str(calls))
    monkeypatch.setenv("EMBER_BROKER_TOKEN", "before")
    name = "hang" if outcome == "cancel" else "write"

    async def run() -> None:
        character, session = uuid4(), uuid4()
        activity = ActivityLog()
        tools = MCPTools(
            (
                settings(
                    environment={
                        "MCP_CALL_FILE": "EMBER_BROKER_CALLS",
                        "MCP_FIXTURE_TOKEN": "EMBER_BROKER_TOKEN",
                    },
                    grants=(MCPToolGrant(name=name, effect="write"),),
                ),
            ),
            character,
            session,
        )
        await tools.start()
        router = TaskRouter(
            tmp_path / "runtime",
            character,
            session,
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
            mcp=tools,
            activity=activity,
        )
        tool = identity(tools, name)
        router.delegate = BrokerWorkflow(router.broker, tool)
        router.providers["delegate"] = router.delegate
        await router.start()
        try:
            handle = await router.submit("harness-write", "SYNTHETIC_WRITE", operation="delegate")
            async with asyncio.timeout(10):
                while not (confirmations := await router.confirmations()):
                    await asyncio.sleep(0.01)
            pending = confirmations[0]
            assert pending.state == "pending" and not calls.exists()
            with pytest.raises(TaskConflict):
                await router.decide(pending.confirmation_id, "0" * 64, approve=True)
            assert not calls.exists()
            if outcome == "rotate":
                monkeypatch.setenv("EMBER_BROKER_TOKEN", "after")
            await router.decide(pending.confirmation_id, pending.action_sha256, approve=True)
            if outcome == "cancel":
                async with asyncio.timeout(8):
                    while not calls.exists():
                        await asyncio.sleep(0.01)
                await router.cancel(handle.task_id)
            finished = await terminal(router, handle.task_id)
            expected = {"success": "succeeded", "cancel": "uncertain", "rotate": "failed"}[outcome]
            assert finished.state == expected
            assert (calls.read_text().splitlines() if calls.exists() else []) == (
                [] if outcome == "rotate" else [name]
            )
            events = [event for event in activity.since(0) if event.kind == "tool"]
            assert events and events[-1].task_id == handle.task_id and events[-1].status == expected
            duplicate = await router.submit(
                "harness-write", "SYNTHETIC_WRITE", operation="delegate"
            )
            assert duplicate.task_id == handle.task_id and duplicate.state == expected
            assert (calls.read_text().splitlines() if calls.exists() else []) == (
                [] if outcome == "rotate" else [name]
            )
        finally:
            await router.aclose()
            await tools.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["native", "mcp", "private_search", "oversized", "premodel"])
def test_extended_worker_broker_history_privacy_and_pre_effect_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    import sys

    from ember_agent.runtime.config import HarnessSettings
    from ember_agent.runtime.hermes_tasks import HermesTask

    from .test_character import FakeModel
    from .test_mcp_tasks import terminal
    from .test_mcp_tools import identity, settings
    from .test_openai_bridge import _config

    calls = tmp_path / "calls"
    monkeypatch.setenv("EMBER_BROKER_CALLS", str(calls))
    monkeypatch.setenv("EMBER_BROKER_TOKEN", "before")

    async def run() -> None:
        character, session, turn, task = uuid4(), uuid4(), uuid4(), uuid4()
        tools = MCPTools(
            (
                settings(
                    environment={
                        "MCP_CALL_FILE": "EMBER_BROKER_CALLS",
                        "MCP_FIXTURE_TOKEN": "EMBER_BROKER_TOKEN",
                    }
                ),
            ),
            character,
            session,
        )
        await tools.start()
        router = TaskRouter(
            tmp_path / "runtime",
            character,
            session,
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
            mcp=tools,
        )
        grant = (
            "native.inspect_text"
            if mode == "native"
            else identity(tools, "write" if mode == "premodel" else "echo")
        )
        config = _config(tmp_path).model_copy(
            update={
                "context_tokens": 131072,
                "max_input_tokens": 24576,
                "harness": HarnessSettings(
                    provider="hermes",
                    command=(
                        sys.executable,
                        str(Path(__file__).parent / "fixtures/hermes_broker_worker.py"),
                        mode,
                    ),
                    revision="71736c9f32597bbca9d89cede5aa136e55946b2f",
                    profile_sha256="a" * 64,
                    search_endpoint="http://nas.example:8095",
                    tools=(grant,),
                ),
            }
        )
        context = ToolContext(
            character_id=character,
            session_id=session,
            turn_id=turn,
            task_id=task,
            deadline_at=time() + 15,
        )
        model = FakeModel()
        observed = []
        provider = HermesTask(
            config,
            model,
            ToolRouter(TextInspectionProvider(), character),
            broker=router.broker,
            observe=lambda request, validate: observed.append((request, validate)),
        )
        request = TaskSubmission(
            character_id=character,
            session_id=session,
            turn_id=turn,
            idempotency_key="extended",
            goal="Count the words in one two three",
            operation="delegate",
        )
        try:
            if mode in {"private_search", "oversized", "premodel"}:
                reason = (
                    "hermes_search_scope_denied"
                    if mode == "private_search"
                    else "harness_model_context_missing"
                    if mode == "premodel"
                    else "harness_tool_result_budget"
                )
                with pytest.raises(TaskConflict, match=reason):
                    await provider.execute(request, context, progress)
                if mode == "premodel":
                    assert not calls.exists() and not model.requests
                elif mode == "oversized":
                    assert not calls.exists()
                    assert "ember_tool" not in {
                        tool.function.name for tool in model.requests[0].tools
                    }
                else:
                    assert calls.read_text().splitlines() == ["echo"]
                    assert {tool.function.name for tool in model.requests[1].tools} == {
                        "ember_finish"
                    }
                return
            router.delegate = provider
            router.providers["delegate"] = provider
            await router.start()
            handle = await router.submit(
                request.idempotency_key, request.goal, operation="delegate", turn_id=turn
            )
            finished = await terminal(router, handle.task_id)
            assert finished.state == "succeeded" and finished.receipt is not None
            result = TaskResult.model_validate_json(finished.receipt)
            assert result.receipt.data["purged"] is True and "three words" in result.summary
            assert len(model.requests) == len(observed) == 2
            assert observed[1][0] is model.requests[1] and await observed[1][1]()
            assert model.requests[1].messages[-1].role == "tool"
            assert "receipt" in model.requests[1].messages[-1].content
            assert {tool.function.name for tool in model.requests[0].tools} == {
                "searx_search",
                "ember_tool",
            }
            if mode == "mcp":
                assert "searx_search" not in {
                    tool.function.name for tool in model.requests[1].tools
                }
                await router.save_artifact(handle.task_id)
                assert await router.read_artifact(handle.task_id)
                read = router.artifacts.read

                async def rotate_during_read(snapshot: TaskSnapshot) -> bytes:
                    data = await read(snapshot)
                    monkeypatch.setenv("EMBER_BROKER_TOKEN", "after")
                    return data

                router.artifacts.read = rotate_during_read
                with pytest.raises(TaskConflict, match="task_context_invalidated"):
                    await router.read_artifact(handle.task_id)
                monkeypatch.setenv("EMBER_BROKER_TOKEN", "after")
                assert not await observed[1][1]()
                redacted = await router.status(handle.task_id)
                assert redacted and redacted.result is None and redacted.receipt is None
                notice = (await router.notifications(None)).items[0]
                assert notice.result is None and notice.receipt is None
                assert (await router.receipt(handle.task_id)).receipt is None
                for operation in (router.save_artifact, router.read_artifact):
                    with pytest.raises(TaskConflict, match="task_artifact_unavailable"):
                        await operation(handle.task_id)
                await router.aclose()
                restarted = TaskRouter(
                    tmp_path / "runtime",
                    character,
                    uuid4(),
                    TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
                )
                await restarted.start()
                try:
                    stale = await restarted.status(handle.task_id)
                    assert stale and stale.result is None and stale.receipt is None
                finally:
                    await restarted.aclose()
        finally:
            await router.aclose()
            await tools.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("selection", ["absolute", "path", "missing"])
def test_startup_factory_preserves_installed_path_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    import os
    import sys
    from unittest.mock import AsyncMock

    from ember_agent.runtime.config import HarnessSettings
    from ember_agent.runtime.harness import create_task_provider

    from .test_character import FakeModel
    from .test_openai_bridge import _config

    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    command = sys.executable if selection == "absolute" else Path(sys.executable).name
    if selection == "missing":
        command = "ember-missing-worker-synthetic"
    config = _config(tmp_path).model_copy(
        update={
            "harness": HarnessSettings(
                command=(command,),
                revision="71736c9f32597bbca9d89cede5aa136e55946b2f",
                profile_sha256="a" * 64,
                search_endpoint="http://nas.example:8095",
            )
        }
    )
    character = uuid4()

    def create() -> AgenticTaskProvider:
        return create_task_provider(
            config,
            FakeModel(),
            ToolRouter(TextInspectionProvider(), character),
            broker=HarnessToolBroker(character, None),
            recall=AsyncMock(return_value=None),
            validate=AsyncMock(return_value=True),
            observe=lambda request, validate: None,
        )

    if selection == "missing":
        with pytest.raises(TaskConflict, match="harness_executable_unavailable"):
            create()
    else:
        provider = create()
        assert Path(provider.settings.command[0]).is_absolute()


@pytest.mark.parametrize(
    "message,expected",
    [
        ("harness_profile_mismatch", "harness_profile_mismatch"),
        ("PRIVATE ERROR /local/path", "task_provider_failed"),
    ],
)
def test_task_activity_reports_only_fixed_safe_failure_codes(
    tmp_path: Path,
    message: str,
    expected: str,
) -> None:
    from .test_mcp_tasks import terminal

    class FailedProvider:
        async def execute(
            self,
            request: TaskSubmission,
            context: ToolContext,
            progress: Callable[[TaskState], Awaitable[None]],
        ) -> TaskResult:
            raise TaskConflict(message)

    async def run() -> None:
        character = uuid4()
        activity = ActivityLog()
        router = TaskRouter(tmp_path, character, uuid4(), FailedProvider(), activity=activity)
        await router.start()
        try:
            handle = await router.submit("failure", "Synthetic input")
            assert (await terminal(router, handle.task_id)).state == "failed"
            event = activity.since(0)[-1]
            assert event.code == expected and "PRIVATE" not in event.model_dump_json()
        finally:
            await router.aclose()

    asyncio.run(run())
