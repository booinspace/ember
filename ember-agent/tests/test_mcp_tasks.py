"""Real SDK services must use durable tasks, exact approvals and existing operator authority."""

import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from ember_agent.runtime.mcp_contracts import MCPServerSettings, MCPToolGrant
from ember_agent.runtime.mcp_tools import MCPTools
from ember_agent.runtime.task_router import (
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolRouter,
)
from ember_agent.runtime.task_store import TaskConflict, TaskSnapshot, ToolInvocation

FIXTURE = Path(__file__).parent / "fixtures/mcp_conformance_server.py"


def test_operator_api_discovers_and_invokes_without_model_or_memory_authority(
    tmp_path: Path,
) -> None:
    from ember_agent.character.core import CharacterCore
    from ember_agent.memory.deep_recall import DeepRecallService
    from ember_agent.realtime.contracts import RoomBinding
    from ember_agent.runtime.browser_control import BrowserControl
    from ember_memory_engine import CanonicalEngine
    from ember_memory_engine.async_client import InProcessAsyncMemoryClient

    from .test_character import FakeModel
    from .test_phase_eight_browser import ORIGIN, Controls

    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "canonical") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            model = FakeModel()
            core = CharacterCore(memory, model, model_tag="synthetic")
            deep = DeepRecallService(character, lambda _: memory, validation=memory)
            session = uuid4()
            tools = MCPTools(
                (
                    MCPServerSettings(
                        id="fixture",
                        transport="stdio",
                        command=sys.executable,
                        arguments=(str(FIXTURE), "normal"),
                        grants=(MCPToolGrant(name="echo", effect="read"),),
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
            await router.start()

            async def answer(_: object) -> str:
                return "unused"

            owner = BrowserControl(
                origin=ORIGIN,
                assets=tmp_path / "unbuilt",
                core=core,
                deep=deep,
                controls=Controls(),
                binding=RoomBinding(
                    instance_id=session,
                    character_id=character,
                    room="ember-" + session.hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                room_token=lambda: "unused",
                answer=answer,
                task_router=router,
            )
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=owner.app),
                    base_url=ORIGIN,
                    headers={"Origin": ORIGIN},
                ) as client:
                    assert (await client.get("/api/tools")).status_code == 401
                    assert (
                        await client.post("/api/bootstrap", json={"token": owner.activation})
                    ).status_code == 200
                    discovered = await client.get("/api/tools")
                    assert (
                        discovered.status_code == 200
                        and discovered.headers["cache-control"] == "no-store"
                    )
                    definition = next(
                        tool for tool in discovered.json()["tools"] if tool["available"]
                    )
                    payload = {
                        "idempotency_key": "api",
                        "text": "Run echo",
                        "operation": "tool",
                        "tool": {
                            "tool_id": definition["id"],
                            "arguments": {"text": "Do not change memory or call a model."},
                        },
                    }
                    assert (
                        await client.post(
                            "/api/tasks", json={**payload, "character_id": str(uuid4())}
                        )
                    ).status_code == 400
                    for invalid in ({**payload, "tool": None}, {**payload, "operation": "inspect"}):
                        rejected = await client.post("/api/tasks", json=invalid)
                        assert rejected.status_code == 400
                        assert rejected.json() == {"error": "invalid_control_input"}
                    assert not (await router.notifications(None)).items
                    response = await client.post("/api/tasks", json=payload)
                    assert response.status_code == 202
                    identity = response.json()["task_id"]
                    async with asyncio.timeout(8):
                        while (await client.get("/api/tasks/" + identity)).json()[
                            "state"
                        ] != "succeeded":
                            await asyncio.sleep(0.02)
                    receipt = await client.get("/api/tasks/" + identity + "/receipt")
                    assert (
                        receipt.status_code == 200
                        and receipt.json()["receipt"]["outcome"] == "success"
                    )
                    assert (
                        await client.get("/api/tasks/" + str(uuid4()) + "/receipt")
                    ).status_code == 409
                    assert not model.requests and not core.history
            finally:
                await owner.aclose()
                await router.aclose()
                await tools.aclose()
                await deep.aclose()

    asyncio.run(exercise())


async def terminal(router: TaskRouter, identity: UUID) -> TaskSnapshot:
    async with asyncio.timeout(12):
        while True:
            snapshot = await router.status(identity)
            if snapshot and snapshot.state in {
                "succeeded",
                "failed",
                "uncertain",
                "cancelled",
                "timed_out",
            }:
                return snapshot
            await asyncio.sleep(0.02)


@pytest.mark.parametrize(
    "mode", ["normal", "legacy-nonterminal:2025-06-18", "legacy-nonterminal:2025-11-25"]
)
def test_real_tools_use_approval_receipts_and_bound_argument_idempotency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    calls = tmp_path / "calls.txt"
    monkeypatch.setenv("EMBER_MCP_TEST_CALLS", str(calls))

    async def exercise() -> None:
        character, session = uuid4(), uuid4()
        settings = MCPServerSettings(
            id="fixture",
            transport="stdio",
            command=sys.executable,
            arguments=(str(FIXTURE), mode),
            environment={"MCP_CALL_FILE": "EMBER_MCP_TEST_CALLS"},
            grants=(
                MCPToolGrant(name="echo", effect="read"),
                MCPToolGrant(name="write", effect="write"),
            ),
        )
        tools = MCPTools((settings,), character, session)
        await tools.start()
        assert tools.snapshot().servers[0].status == "ready"
        router = TaskRouter(
            tmp_path / "runtime",
            character,
            session,
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
            mcp=tools,
        )
        await router.start()
        try:
            echo = next(tool for tool in tools.snapshot().tools if tool.name == "echo")
            request = ToolInvocation(
                tool_id=echo.id, arguments={"text": "<script>synthetic</script>"}
            )
            first = await router.submit("echo", "Run echo", operation="tool", tool=request)
            assert (await terminal(router, first.task_id)).state == "succeeded"
            receipt = await router.receipt(first.task_id)
            assert receipt.receipt and receipt.receipt.outcome == "success"
            assert "<script>synthetic</script>" in receipt.receipt.model_dump_json()
            assert (
                await router.submit("echo", "Run echo", operation="tool", tool=request)
            ).task_id == first.task_id
            with pytest.raises(TaskConflict, match="idempotency_key_reused"):
                await router.submit(
                    "echo",
                    "Run echo",
                    operation="tool",
                    tool=request.model_copy(update={"arguments": {"text": "changed"}}),
                )
            write = next(tool for tool in tools.snapshot().tools if tool.name == "write")
            pending = await router.submit(
                "write",
                "Run write",
                operation="tool",
                tool=ToolInvocation(tool_id=write.id, arguments={"text": "Exact approved text"}),
            )
            async with asyncio.timeout(5):
                while not (approvals := await router.confirmations()):
                    await asyncio.sleep(0.02)
            assert "write" not in calls.read_text().splitlines()
            approval = approvals[0]
            assert approval.task_id == pending.task_id and approval.tool == write.id
            assert approval.arguments == {"text": "Exact approved text"}
            with pytest.raises(TaskConflict, match="confirmation_scope_or_action_mismatch"):
                await router.decide(approval.confirmation_id, "b" * 64, approve=True)
            await router.decide(approval.confirmation_id, approval.action_sha256, approve=True)
            state = "succeeded" if mode == "normal" else "uncertain"
            outcome = "success" if mode == "normal" else "unknown_effect"
            assert (await terminal(router, pending.task_id)).state == state
            assert calls.read_text().splitlines() == ["echo", "write"]
            assert (await router.receipt(pending.task_id)).receipt.outcome == outcome
            assert len((await router.notifications(None)).items) == 2
        finally:
            await router.aclose()
            await tools.aclose()

    asyncio.run(exercise())


def test_dispatched_write_cancellation_is_uncertain_and_never_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = tmp_path / "calls.txt"
    monkeypatch.setenv("EMBER_MCP_TEST_CALLS", str(calls))

    async def exercise() -> None:
        character, session = uuid4(), uuid4()
        tools = MCPTools(
            (
                MCPServerSettings(
                    id="fixture",
                    transport="stdio",
                    command=sys.executable,
                    arguments=(str(FIXTURE), "normal"),
                    environment={"MCP_CALL_FILE": "EMBER_MCP_TEST_CALLS"},
                    grants=(MCPToolGrant(name="hang", effect="write"),),
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
        await router.start()
        request = ToolInvocation(
            tool_id=next(iter(tools.capabilities)), arguments={"text": "synthetic"}
        )
        try:
            task = await router.submit("write", "Run hang", operation="tool", tool=request)
            async with asyncio.timeout(5):
                while not (approvals := await router.confirmations()):
                    await asyncio.sleep(0.02)
            await router.decide(
                approvals[0].confirmation_id, approvals[0].action_sha256, approve=True
            )
            async with asyncio.timeout(8):
                while not calls.exists():
                    await asyncio.sleep(0.02)
            await router.cancel(task.task_id)
            assert (await terminal(router, task.task_id)).state == "uncertain"
            view = await router.receipt(task.task_id)
            assert view.state == "uncertain" and view.receipt is None
        finally:
            await router.aclose()
            await tools.aclose()
        restarted = TaskRouter(
            tmp_path / "runtime",
            character,
            uuid4(),
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
            mcp=tools,
        )
        await restarted.start()
        try:
            retry = await restarted.submit("write", "Run hang", operation="tool", tool=request)
            assert retry.state == "uncertain" and calls.read_text().splitlines() == ["hang"]
        finally:
            await restarted.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("change", ["credential", "metadata"])
def test_approved_write_rejected_before_rpc_is_failed_not_an_unknown_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    calls, variant = tmp_path / "calls", tmp_path / "variant"
    variant.write_text("original")
    monkeypatch.setenv("EMBER_MCP_TEST_CALLS", str(calls))
    monkeypatch.setenv("EMBER_MCP_TEST_VARIANT", str(variant))
    monkeypatch.setenv("EMBER_MCP_TEST_TOKEN", "before")

    async def exercise() -> None:
        character, session = uuid4(), uuid4()
        tools = MCPTools(
            (
                MCPServerSettings(
                    id="fixture",
                    transport="stdio",
                    command=sys.executable,
                    arguments=(str(FIXTURE), "normal"),
                    environment={
                        "MCP_CALL_FILE": "EMBER_MCP_TEST_CALLS",
                        "MCP_VARIANT_FILE": "EMBER_MCP_TEST_VARIANT",
                        "MCP_FIXTURE_TOKEN": "EMBER_MCP_TEST_TOKEN",
                    },
                    grants=(MCPToolGrant(name="write", effect="write"),),
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
        await router.start()
        try:
            request = ToolInvocation(
                tool_id=next(iter(tools.capabilities)), arguments={"text": "exact"}
            )
            task = await router.submit("write", "Run write", operation="tool", tool=request)
            async with asyncio.timeout(5):
                while not (approvals := await router.confirmations()):
                    await asyncio.sleep(0.02)
            assert approvals[0].display_label == "fixture: write (write)"
            assert approvals[0].approvable
            if change == "credential":
                monkeypatch.setenv("EMBER_MCP_TEST_TOKEN", "after")
            else:
                variant.write_text("changed")
            await router.decide(
                approvals[0].confirmation_id, approvals[0].action_sha256, approve=True
            )
            assert (await terminal(router, task.task_id)).state == "failed"
            receipt = (await router.receipt(task.task_id)).receipt
            assert receipt and receipt.outcome == "failure" and receipt.dispatched is False
            assert receipt.code == "mcp_metadata_changed"
            assert not calls.exists()
            assert (
                await router.submit("write", "Run write", operation="tool", tool=request)
            ).state == "failed"
        finally:
            await router.aclose()
            await tools.aclose()

    asyncio.run(exercise())
