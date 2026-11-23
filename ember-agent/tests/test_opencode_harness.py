"""Exercise owned delegation with synthetic files and a deterministic gateway upstream."""

import asyncio
import os
from collections.abc import AsyncGenerator
from hashlib import file_digest
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import JsonValue

from ember_agent.character.core import CharacterCore, ConversationTask
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import GatewayRequest
from ember_agent.inference.service import create_app
from ember_agent.inference.upstream import NativeFrame, NativeMessage
from ember_agent.runtime.config import HarnessSettings
from ember_agent.runtime.harness import open_harness
from ember_agent.runtime.opencode_harness import _prepare, open_opencode
from ember_agent.runtime.task_router import (
    TaskBinding,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolRouter,
)
from ember_agent.runtime.task_store import TaskConflict, TaskSnapshot
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_openai_bridge import _bridge, _call, _config, _Upstream


async def _terminal(router: TaskRouter, task_id: UUID) -> TaskSnapshot:
    async with asyncio.timeout(15):
        while True:
            value = await router.status(task_id)
            if value and value.state in {"succeeded", "failed", "cancelled", "timed_out"}:
                return value
            await asyncio.sleep(0.02)


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"private_recall": True}, "opencode_optional_capability_unqualified"),
        ({"skills": True}, "opencode_optional_capability_unqualified"),
        ({"tools": ("mcp.ember.recall",)}, "opencode_optional_capability_unqualified"),
        ({}, "opencode_executable_pin_mismatch"),
    ],
)
def test_unqualified_harness_rejected_before_launch(
    tmp_path: Path, updates: dict[str, object], reason: str
) -> None:
    config = _config(tmp_path)
    executable = tmp_path / "not-a-worker"
    executable.write_bytes(b"synthetic")
    settings = HarnessSettings(
        provider="opencode",
        command=(str(executable),),
        revision="2.3.2",
        profile_sha256="0" * 64,
        workspace=tmp_path / "workspace",
    ).model_copy(update=updates)

    async def run() -> None:
        _, gateway, model = _bridge(config, _Upstream())
        try:
            with pytest.raises(TaskConflict, match="harness_provider_unsupported"):
                async with open_harness(config, model, lambda _: None):
                    pytest.fail("Absent harness must not fall back to another provider")
            with pytest.raises(TaskConflict, match=reason):
                async with open_opencode(config, settings, model):
                    pytest.fail("Unqualified harness must not start")
        finally:
            await model.aclose()
            await gateway.aclose()

    asyncio.run(run())


def test_unreadable_opencode_executable_reports_access_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    settings = HarnessSettings(
        provider="opencode",
        command=(str(tmp_path / "opencode"),),
        revision="2.3.2",
        profile_sha256="0" * 64,
        workspace=tmp_path / "workspace",
    )

    def deny_resolve(path: Path, *, strict: bool = False) -> Path:
        raise PermissionError(13, "Access denied", str(path))

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "resolve", deny_resolve)
        with pytest.raises(TaskConflict, match="^opencode_executable_access_denied$"):
            _prepare(config, settings)


@pytest.mark.integration
def test_installed_opencode_runs_and_revokes_read_only_tasks(tmp_path: Path) -> None:
    binary = os.environ.get("EMBER_TEST_OPENCODE")
    if not binary:
        pytest.skip("EMBER_TEST_OPENCODE must point to installed OpenCode 2.3.2")
    executable = Path(binary).resolve(strict=True)
    with executable.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    markers = ["fixture-" + uuid4().hex for _ in range(2)]
    files = [workspace / f"pin{index}.txt" for index in range(2)]
    for file, marker in zip(files, markers, strict=True):
        file.write_text(marker, encoding="utf-8")
    external = tmp_path / "outside.txt"
    external.write_text("not-in-workspace", encoding="utf-8")

    chat_goal = "Please find the contents of both pin files in the workspace."

    class FixtureUpstream(_Upstream):
        async def generate(self, request: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            self.requests.append(request)
            if request.workload == "P0":
                assert request.messages[-1].content == chat_goal
                assert [tool.function.name for tool in request.tools] == ["delegate_task"]
                yield _call("delegate_task", {})
                yield NativeFrame(done=True, done_reason="tool_calls")
                return
            self.started.set()
            await self.release.wait()
            assert {tool.function.name for tool in request.tools} == {"read", "glob", "grep"}
            assert request.workload == "P2"
            results = [item for item in request.messages if item.role == "tool"]
            goal = next(item.content for item in request.messages if item.role == "user")
            steps: list[tuple[str, dict[str, JsonValue]]] = [
                ("glob", {"path": str(workspace), "pattern": "pin*.txt"}),
                ("grep", {"path": str(workspace), "pattern": "fixture-", "include": "*.txt"}),
                *(("read", {"path": str(file)}) for file in files),
            ]
            if "outside" in goal:
                steps = [("read", {"path": str(external)})]
            elif "forbidden" in goal:
                steps = [("shell", {"command": "echo never-executed"})]
            if results:
                assert results[-1].tool_call_id == request.messages[-2].tool_calls[0].call_id
                expected = [files[0].name, markers[0], *markers][len(results) - 1]
                assert expected in results[-1].content
            if len(results) < len(steps):
                yield _call(*steps[len(results)])
                yield NativeFrame(
                    done=True, done_reason="tool_calls", prompt_eval_count=200, eval_count=10
                )
            else:
                yield NativeFrame(message=NativeMessage(content=" ".join(markers)))
                yield NativeFrame(
                    done=True, done_reason="stop", prompt_eval_count=300, eval_count=10
                )

    async def run() -> None:
        config = _config(tmp_path).model_copy(
            update={"context_tokens": 32768, "max_input_tokens": 16384}
        )
        config.runtime_root.mkdir(parents=True, exist_ok=True)
        settings = HarnessSettings(
            provider="opencode",
            command=(str(executable),),
            revision="2.3.2",
            profile_sha256=digest,
            workspace=workspace,
        )
        upstream = FixtureUpstream()
        _, gateway, model = _bridge(config, upstream)
        character = uuid4()
        native = TextInspectionTask(ToolRouter(TextInspectionProvider(), character))
        try:
            async with open_opencode(config, settings, model) as worker:
                router = TaskRouter(
                    config.runtime_root, character, uuid4(), native, delegate=worker
                )
                router.bindings["delegate"] = TaskBinding(worker, ready=lambda: worker.available)
                await router.start()
                try:
                    realtime = uuid4()
                    await gateway.session(realtime, connected=True)
                    upstream.release.clear()
                    cancelled = await router.submit("cancel", "Read fixture", operation="delegate")
                    async with asyncio.timeout(8):
                        while not gateway.pending["P2"]:
                            await asyncio.sleep(0.02)
                    assert not upstream.started.is_set()
                    await gateway.session(realtime, connected=False)
                    async with asyncio.timeout(8):
                        await upstream.started.wait()
                    active = await router.status(cancelled.task_id)
                    assert active and active.state == "waiting_for_inference"
                    await router.cancel(cancelled.task_id)
                    assert (await _terminal(router, cancelled.task_id)).state == "cancelled"
                    assert worker.available and not worker.ingress.active
                    assert worker.ingress.bridge is None
                    owned_job = gateway.active
                    assert owned_job and owned_job.request.context_id == cancelled.task_id
                    assert owned_job.task and not owned_job.task.done()
                    upstream.release.set()
                    async with asyncio.timeout(8):
                        await asyncio.shield(owned_job.task)
                    assert gateway.active is None and not gateway.uncertain

                    start = len(upstream.requests)
                    endpoint = config.services.gateway.endpoint
                    async with httpx.AsyncClient(
                        base_url=endpoint,
                        transport=httpx.ASGITransport(app=create_app(gateway, "foreground")),
                    ) as http:
                        foreground = HttpGatewayClient(endpoint, "foreground", client=http)
                        with CanonicalEngine(tmp_path / "canonical") as engine:
                            memory = InProcessAsyncMemoryClient(
                                engine.character(
                                    engine.create_character(character_id=character).store_id
                                )
                            )
                            try:
                                core = CharacterCore(
                                    memory,
                                    foreground,
                                    model_tag=config.model_tag,
                                    recall_enabled=False,
                                )

                                async def delegate(text: str, turn_id: UUID) -> str:
                                    return await router.submit_turn(
                                        text, turn_id, operation="delegate"
                                    )

                                core.task_tools["delegate_task"] = ConversationTask(
                                    "Read the workspace using the current message.",
                                    delegate,
                                    use_current_turn=True,
                                )
                                turn = await core.prepare(chat_goal)
                                events = [event async for event in core.stream(turn)]
                                assert "".join(event.text for event in events) == (
                                    "Inspect task queued. Disconnect Conversation to let it run, "
                                    "then check Tasks."
                                )
                            finally:
                                await memory.aclose()

                    async with asyncio.timeout(8):
                        while len(upstream.requests) < start + 2:
                            await asyncio.sleep(0.02)
                    p0, first_p2 = upstream.requests[start : start + 2]
                    assert p0.request_id == turn.request.request_id
                    assert first_p2.workload == "P2"
                    assert any(
                        message.role == "user" and message.content == chat_goal
                        for message in first_p2.messages
                    )
                    task = await router.submit("direct-read", "Read fixture", operation="delegate")
                    for task_id in (first_p2.context_id, task.task_id):
                        result = await _terminal(router, task_id)
                        assert result.state == "succeeded" and result.result == " ".join(markers)
                        receipt = await router.receipt(task_id)
                        assert receipt.receipt
                        assert receipt.receipt.data["tools"] == [
                            {"name": name, "status": "completed"}
                            for name in ("glob", "grep", "read", "read")
                        ]
                    assert [request.context_id for request in upstream.requests[start + 1 :]] == (
                        [first_p2.context_id] * 5 + [task.task_id] * 5
                    )

                    for goal in ("outside", "forbidden"):
                        start = len(upstream.requests)
                        denied = await router.submit(goal, goal, operation="delegate")
                        result = await _terminal(router, denied.task_id)
                        assert result.state == "failed" and result.result is None
                        assert len(upstream.requests) == start + 1
                        assert worker.available

                    upstream.started.clear()
                    upstream.release.clear()
                    crashed = await router.submit(
                        "crash-active", "Read fixture", operation="delegate"
                    )
                    async with asyncio.timeout(8):
                        await upstream.started.wait()
                    worker.process.kill()
                    assert (await _terminal(router, crashed.task_id)).state == "failed"
                    assert worker.process.returncode is not None and not worker.ingress.active
                    assert worker.failed and not router.delegation_available
                    upstream.release.set()
                    with pytest.raises(TaskConflict, match="task_provider_unavailable"):
                        await router.submit("crash", "Read fixture", operation="delegate")
                    task = await router.submit("native", "one two three")
                    assert (await _terminal(router, task.task_id)).state == "succeeded"
                finally:
                    await router.aclose()
            assert worker.process.returncode is not None
            assert not list(config.runtime_root.glob("opencode-*"))
            async with httpx.AsyncClient(base_url=worker.http.base_url, trust_env=False) as probe:
                with pytest.raises(httpx.ConnectError):
                    await probe.get("/api/info")
        finally:
            upstream.release.set()
            await model.aclose()
            await gateway.aclose()

    asyncio.run(run())
