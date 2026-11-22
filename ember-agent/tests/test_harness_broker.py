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
    HarnessToolBroker,
    TaskResult,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolContext,
    ToolDependency,
    ToolRouter,
)
from ember_agent.runtime.task_store import TaskConflict, TaskSnapshot, TaskState, TaskSubmission


async def progress(_: TaskState) -> None:
    pass


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


class RevocableWorkflow(BrokerWorkflow):
    async def execute(
        self,
        request: TaskSubmission,
        context: ToolContext,
        progress: Callable[[TaskState], Awaitable[None]],
    ) -> TaskResult:
        (grant,) = self.broker.catalog((self.tool,))
        assert grant.authority_tag is not None
        tag = grant.authority_tag
        result = await super().execute(request, context, progress)
        return result.model_copy(
            update={"tool_dependencies": (ToolDependency(tool_id=self.tool, authority_tag=tag),)}
        )


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


def test_revoked_tool_context_redacts_all_disclosure_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from .test_mcp_tasks import terminal
    from .test_mcp_tools import identity, settings

    monkeypatch.setenv("EMBER_BROKER_TOKEN", "before")

    async def run() -> None:
        character, session = uuid4(), uuid4()
        tools = MCPTools(
            (settings(environment={"MCP_FIXTURE_TOKEN": "EMBER_BROKER_TOKEN"}),),
            character,
            session,
        )
        await tools.start()
        router = TaskRouter(
            tmp_path,
            character,
            session,
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
            mcp=tools,
        )
        tool = identity(tools, "echo")
        router.delegate = RevocableWorkflow(router.broker, tool)
        router.providers["delegate"] = router.delegate
        await router.start()
        try:
            handle = await router.submit("extended", "Synthetic tool input", operation="delegate")
            finished = await terminal(router, handle.task_id)
            assert finished.state == "succeeded" and finished.receipt is not None
            assert (await router.receipt(handle.task_id)).receipt is not None
            await router.save_artifact(handle.task_id)
            assert await router.read_artifact(handle.task_id) == b"Broker completed"
            read = router.artifacts.read

            async def rotate_during_read(snapshot: TaskSnapshot) -> bytes:
                data = await read(snapshot)
                monkeypatch.setenv("EMBER_BROKER_TOKEN", "after")
                return data

            router.artifacts.read = rotate_during_read
            with pytest.raises(TaskConflict, match="task_context_invalidated"):
                await router.read_artifact(handle.task_id)
            redacted = await router.status(handle.task_id)
            assert redacted and redacted.result is None and redacted.receipt is None
            notice = (await router.notifications(None)).items[0]
            assert notice.result is None and notice.receipt is None
            assert (await router.receipt(handle.task_id)).receipt is None
            for operation in (router.save_artifact, router.read_artifact):
                with pytest.raises(TaskConflict, match="task_artifact_unavailable"):
                    await operation(handle.task_id)
        finally:
            await router.aclose()
            await tools.aclose()

        restarted = TaskRouter(
            tmp_path,
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

    asyncio.run(run())


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
