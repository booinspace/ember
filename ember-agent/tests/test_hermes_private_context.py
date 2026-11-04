"""Synthetic private recall survives the real worker/task/gateway boundaries safely."""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import time
from uuid import uuid4

import httpx
import psutil
import pytest
from pydantic import JsonValue

from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import GatewayRequest
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.service import create_app
from ember_agent.inference.upstream import NativeFrame, NativeMessage
from ember_agent.runtime.config import HermesSettings, load_config
from ember_agent.runtime.hermes_tasks import HermesTask, PersonalContext
from ember_agent.runtime.task_router import (
    TaskResult,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolContext,
    ToolReceipt,
    ToolRouter,
)
from ember_agent.runtime.task_store import TERMINAL_STATES, TaskConflict, TaskState, TaskSubmission
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.related import ContextDependencies

FIXTURE = Path(__file__).parent / "fixtures/hermes_protocol_worker.py"
TEXT = "Synthetic project uses the blue notebook."


class SyntheticUpstream:
    def __init__(self) -> None:
        self.requests: list[GatewayRequest] = []

    async def verify(self, *, loaded: bool = False) -> None:
        pass

    async def generate(self, request: GatewayRequest) -> AsyncIterator[NativeFrame]:
        self.requests.append(request)
        yield NativeFrame(message=NativeMessage(content="Synthetic response"))
        yield NativeFrame(done=True, prompt_eval_count=10, eval_count=2)

    async def aclose(self) -> None:
        pass


class NoPublicSearch:
    capabilities = frozenset({"searx_search"})

    def __init__(self) -> None:
        self.calls = 0

    async def invoke(
        self, name: str, arguments: dict[str, JsonValue], context: ToolContext
    ) -> ToolReceipt:
        self.calls += 1
        raise AssertionError("Private recall must not reach the public search service")


class PrivateTask:
    def __init__(self, root: Path, mode: str, text: str) -> None:
        self.character, self.session, self.turn = uuid4(), uuid4(), uuid4()
        self.pid_path, self.marker = root / "worker.pid", root / "finish.ready"
        self.source = SourceRef(
            source_store_id=uuid4(),
            record_kind="message",
            record_id=uuid4(),
            record_revision=1,
        )
        self.dependencies = ContextDependencies(policy_epoch=7, sources=(self.source,))
        self.text, self.valid = text, True
        self.recalls: list[ToolContext] = []
        self.validations = 0
        self.search = NoPublicSearch()
        self.upstream = SyntheticUpstream()
        example = Path(__file__).parents[1] / "config/deployment.example.toml"
        path = root / "deployment.toml"
        path.write_text(
            example.read_text()
            .replace('tag = ""', 'tag = "synthetic:27b"')
            .replace('expected_digest = ""', 'expected_digest = "' + "a" * 64 + '"')
        )
        self.config = load_config(path, require_model_pin=True).model_copy(
            update={
                "data_root": root / "canonical",
                "runtime_root": root / "operational",
                "hermes": HermesSettings(
                    command=(
                        sys.executable,
                        str(FIXTURE),
                        mode,
                        str(self.pid_path),
                        str(self.marker),
                    ),
                    revision="71736c9f32597bbca9d89cede5aa136e55946b2f",
                    profile_sha256="a" * 64,
                    search_endpoint="http://installed.invalid",
                    private_recall=True,
                ),
            }
        )
        self.gateway = P0Gateway(self.config, self.upstream)

    async def recall(self, query: str, context: ToolContext) -> PersonalContext:
        assert query == "Synthetic project"
        assert (context.character_id, context.session_id, context.turn_id) == (
            self.character,
            self.session,
            self.turn,
        )
        self.recalls.append(context)
        return PersonalContext(self.text, self.dependencies, sources=(self.source,))

    async def validate(self, dependencies: ContextDependencies) -> bool:
        assert dependencies == self.dependencies
        self.validations += 1
        return self.valid

    @asynccontextmanager
    async def provider(self) -> AsyncIterator[HermesTask]:
        app = create_app(self.gateway, "foreground", agent_token="agent")
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as http:
                yield HermesTask(
                    self.config,
                    HttpGatewayClient("http://test", "agent", client=http),
                    ToolRouter(self.search, self.character),
                    recall=self.recall,
                    validate=self.validate,
                )
        finally:
            await self.gateway.aclose()
        assert not psutil.pid_exists(int(self.pid_path.read_text()))
        assert self.search.calls == 0
        assert not self.config.data_root.exists()

    async def execute(self, provider: HermesTask) -> TaskResult:
        async def progress(state: TaskState) -> None:
            pass

        return await provider.execute(
            TaskSubmission(
                character_id=self.character,
                session_id=self.session,
                turn_id=self.turn,
                idempotency_key="private",
                goal="Recall synthetic project",
                operation="delegate",
            ),
            ToolContext(
                character_id=self.character,
                session_id=self.session,
                turn_id=self.turn,
                task_id=uuid4(),
                deadline_at=time() + 8,
            ),
            progress,
        )


@pytest.mark.parametrize("revoke_before_delivery", [False, True])
def test_private_task_completes_with_current_broker_citation_and_revalidates_delivery(
    tmp_path: Path, revoke_before_delivery: bool
) -> None:
    async def run() -> None:
        task = PrivateTask(tmp_path, "private-success", TEXT)
        async with task.provider() as provider:
            router = TaskRouter(
                task.config.runtime_root,
                task.character,
                task.session,
                TextInspectionTask(ToolRouter(TextInspectionProvider(), task.character)),
                delegate=provider,
                validate=task.validate,
            )
            await router.start()
            try:
                handle = await router.submit(
                    "private", "Recall synthetic project", turn_id=task.turn, operation="delegate"
                )
                async with asyncio.timeout(8):
                    while (
                        raw := await router.store.status(handle.task_id, task.character)
                    ) is None or (raw.state not in TERMINAL_STATES):
                        await asyncio.sleep(0.01)
                assert raw.state == "succeeded" and raw.receipt is not None
                result = TaskResult.model_validate_json(raw.receipt)
                assert result.summary == TEXT and result.dependencies == task.dependencies
                assert result.citations == (task.source,)
                assert result.receipt.context == task.recalls[0]
                assert result.receipt.context.task_id == handle.task_id
                assert result.receipt.data["citations"] == [
                    {"id": "memory", "title": "Ember scoped recall"}
                ]
                assert result.receipt.data["purged"] is True
                assert result.receipt.data["tool_receipts"] == []
                assert len(task.upstream.requests) == 1
                generated = task.upstream.requests[0]
                assert generated.workload == "P2" and generated.context_id == handle.task_id
                assert generated.messages[0].content == TEXT
                assert [tool.function.name for tool in generated.tools] == ["ember_finish"]
                assert generated.tools[0].function.parameters["properties"]["citations"]["items"][
                    "enum"
                ] == ["memory"]
                assert generated.request_id != generated.context_id
                assert task.validations > 1 and len(task.recalls) == 1
                task.valid = not revoke_before_delivery
                visible = await router.status(handle.task_id)
                notifications = await router.notifications(None)
                receipt = await router.receipt(handle.task_id)
                assert visible is not None and visible.state == "succeeded"
                assert len(notifications.items) == 1
                assert notifications.items[0].task_id == handle.task_id
                if revoke_before_delivery:
                    assert visible.result is None and notifications.items[0].result is None
                    assert receipt.receipt is None
                    with pytest.raises(TaskConflict, match="task_artifact_unavailable"):
                        await router.save_artifact(handle.task_id)
                else:
                    assert visible.result == TEXT and notifications.items[0].result == TEXT
                    assert receipt.receipt == result.receipt
                await router.acknowledge(handle.task_id)
                assert not (await router.notifications(None)).items
            finally:
                await router.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode,text,code",
    [
        ("private-search", TEXT, "hermes_search_scope_denied"),
        ("private-success", "é" * 513, "hermes_context_budget"),
    ],
)
def test_private_context_cannot_escape_to_search_or_exceed_its_byte_budget(
    tmp_path: Path, mode: str, text: str, code: str
) -> None:
    async def run() -> None:
        task = PrivateTask(tmp_path, mode, text)
        async with task.provider() as provider:
            with pytest.raises(TaskConflict, match=code):
                await task.execute(provider)
            assert len(task.recalls) == 1
            assert task.upstream.requests == [] and task.search.calls == 0

    asyncio.run(run())


def test_revocation_after_structured_finish_blocks_final_private_result(tmp_path: Path) -> None:
    async def run() -> None:
        task = PrivateTask(tmp_path, "private-finalize", TEXT)
        async with task.provider() as provider:
            running = asyncio.create_task(task.execute(provider))
            try:
                async with asyncio.timeout(5):
                    while not task.marker.exists():
                        if running.done():
                            await running
                            raise AssertionError("Worker exited before its finish checkpoint")
                        await asyncio.sleep(0.005)
                task.valid = False
                task.marker.with_suffix(".release").touch()
                with pytest.raises(TaskConflict, match="hermes_context_revoked"):
                    await asyncio.wait_for(running, 4)
            finally:
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            assert len(task.upstream.requests) == 1

    asyncio.run(run())
