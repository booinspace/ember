"""Native scope, legacy dependency revocation and safe task diagnostics."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import time
from uuid import uuid4

import pytest

from ember_agent.observability import ActivityLog
from ember_agent.runtime.mcp_tools import MCPTools
from ember_agent.runtime.task_router import (
    TaskResult,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolContext,
    ToolDependency,
    ToolRouter,
)
from ember_agent.runtime.task_store import (
    TaskConflict,
    TaskSnapshot,
    TaskState,
    TaskSubmission,
    ToolInvocation,
)


def test_native_router_only_accepts_issued_scope_and_bound_character() -> None:
    async def run() -> None:
        character = uuid4()
        tools = ToolRouter(TextInspectionProvider(), character)
        ctx = ToolContext(
            character_id=character,
            session_id=uuid4(),
            turn_id=uuid4(),
            task_id=uuid4(),
            deadline_at=time() + 5,
        )
        args = {"text": "one two"}
        granted = frozenset({"inspect_text"})
        receipt = await tools.invoke("inspect_text", args, ctx, allowed=granted)
        assert receipt.data == {"words": 2, "characters": 7}
        with pytest.raises(TaskConflict, match="tool_scope_denied"):
            await tools.invoke("inspect_text", args, ctx, allowed=frozenset())
        with pytest.raises(TaskConflict, match="tool_scope_denied"):
            await tools.invoke(
                "inspect_text",
                args,
                ctx.model_copy(update={"character_id": uuid4()}),
                allowed=granted,
            )

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
        direct = router.providers["tool"]
        assert direct is not None

        class LegacyReceipt:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                tag = tools.authority_tag(tool)
                result = await direct.execute(
                    request.model_copy(
                        update={
                            "operation": "tool",
                            "tool": ToolInvocation(
                                tool_id=tool,
                                arguments={"text": request.goal},
                            ),
                        }
                    ),
                    context,
                    progress,
                )
                return result.model_copy(
                    update={
                        "summary": "Legacy result",
                        "tool_dependencies": (ToolDependency(tool_id=tool, authority_tag=tag),),
                    }
                )

        router.delegate = LegacyReceipt()
        router.providers["delegate"] = router.delegate
        await router.start()
        try:
            handle = await router.submit("extended", "Synthetic tool input", operation="delegate")
            finished = await terminal(router, handle.task_id)
            assert finished.state == "succeeded" and finished.receipt is not None
            assert (await router.receipt(handle.task_id)).receipt is not None
            await router.save_artifact(handle.task_id)
            assert await router.read_artifact(handle.task_id) == b"Legacy result"
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
