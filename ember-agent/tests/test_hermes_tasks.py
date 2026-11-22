"""Actor-bound harness tasks use real subprocesses and the authenticated gateway boundary."""

import asyncio
import json
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from time import time
from uuid import uuid4

import httpx
import psutil
import pytest

from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import GatewayEvent, GatewayRequest
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.service import create_app
from ember_agent.inference.upstream import NativeFrame
from ember_agent.runtime.config import HermesSettings
from ember_agent.runtime.hermes_tasks import HermesTask, PersonalContext
from ember_agent.runtime.searx_tools import SearXNGTools
from ember_agent.runtime.task_router import (
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolContext,
    ToolRouter,
)
from ember_agent.runtime.task_store import TaskConflict, TaskState, TaskSubmission
from ember_memory_abi.related import ContextDependencies

from .test_gateway import FakeUpstream, configuration

FIXTURE = Path(__file__).parent / "fixtures/hermes_protocol_worker.py"
REVISION = "71736c9f32597bbca9d89cede5aa136e55946b2f"


class ObservedUpstream(FakeUpstream):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[GatewayRequest] = []

    async def generate(self, value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
        self.requests.append(value)
        async for frame in super().generate(value):
            yield frame


class Body(httpx.AsyncByteStream):
    def __init__(self, *, large: bool = False) -> None:
        self.large = large

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield json.dumps(
            {
                "results": (
                    [
                        {
                            "url": "https://public.example/"
                            + "x" * (512 - len("https://public.example/")),
                            "title": "😀" * 128,
                            "content": "😀" * 256,
                        }
                    ]
                    if self.large
                    else []
                )
                + [
                    {
                        "url": "https://docs.searxng.org/dev/search_api.html",
                        "title": "Search API",
                        "content": "GET and POST queries",
                    }
                ]
            }
        ).encode()


def search_handler(request: httpx.Request, *, large: bool = False) -> httpx.Response:
    assert request.url.host == "installed.invalid" and request.url.path == "/search"
    assert request.url.params["format"] == "json"
    return httpx.Response(
        200, headers={"Content-Type": "application/json"}, stream=Body(large=large)
    )


@pytest.mark.parametrize(
    "mode,code",
    [
        ("wrong-pin", "profile_mismatch"),
        ("denied-tool", "tool_denied"),
        ("denied-model", "model_route_denied"),
        ("bad-citation", "unverified_citations"),
        ("omitted-citation", "unverified_citations"),
        ("invented-url", "unverified_citations"),
        ("changed-result", "result_changed"),
        ("flood", "invalid_frame"),
    ],
)
def test_worker_rejections_publish_no_success_and_reap_child(
    tmp_path: Path, mode: str, code: str
) -> None:
    asyncio.run(exercise(tmp_path, mode, code))


async def exercise(tmp_path: Path, mode: str, code: str | None = None) -> None:
    config = configuration(tmp_path)
    pid_path = tmp_path / "worker.pid"
    config = config.model_copy(
        update={
            "hermes": HermesSettings(
                command=(sys.executable, str(FIXTURE), mode, str(pid_path)),
                revision=REVISION,
                profile_sha256="a" * 64,
                search_endpoint="http://installed.invalid",
            )
        }
    )
    native = ObservedUpstream()
    gateway = P0Gateway(config, native)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(gateway, "foreground", agent_token="agent")),
        base_url="http://test",
    ) as http:
        model = HttpGatewayClient("http://test", "agent", client=http)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: search_handler(
                    request, large=mode in {"bounded-search", "omitted-citation"}
                )
            )
        ) as http_search:
            search = SearXNGTools("http://installed.invalid", client=http_search)
            character, session = uuid4(), uuid4()
            provider = HermesTask(config, model, ToolRouter(search, character))
            context = ToolContext(
                character_id=character,
                session_id=session,
                turn_id=uuid4(),
                task_id=uuid4(),
                deadline_at=time() + 8,
            )
            request = TaskSubmission(
                character_id=character,
                session_id=session,
                turn_id=context.turn_id,
                goal="Find official search documentation",
                idempotency_key="task",
                operation="delegate",
            )
            states = []

            async def progress(state: TaskState) -> None:
                states.append(state)

            if code:
                with pytest.raises(TaskConflict, match=code):
                    await provider.execute(request, context, progress)
            else:
                result = await provider.execute(request, context, progress)
                assert result.receipt.context == context and result.receipt.tool == "hermes"
                assert result.receipt.data["purged"] is True
                assert "https://docs.searxng.org/dev/search_api.html" in result.summary
                if mode == "tool-policy":
                    assert native.count == 4
                    assert [len(value.messages) for value in native.requests] == [1, 3, 5, 7]
                    assert [
                        {tool.function.name for tool in value.tools} for value in native.requests
                    ] == [
                        {"searx_search"},
                        {"searx_search", "ember_finish"},
                        {"searx_search", "ember_finish"},
                        {"ember_finish"},
                    ]
                    assert all(value.context_id == context.task_id for value in native.requests)
                    assert all(value.workload == "P2" for value in native.requests)
                    assert len({value.request_id for value in native.requests}) == 4
                    finish = native.requests[-1].tools[0].function.parameters
                    assert finish["properties"]["citations"]["items"]["enum"] == [
                        "q1-s1",
                        "q2-s1",
                        "q3-s1",
                    ]
                else:
                    assert native.count == 1
                    assert states == [
                        "waiting_for_inference",
                        "waiting_for_tool",
                        "waiting_for_tool",
                    ]
                if mode == "bounded-search":
                    assert result.receipt.data["citations"][0]["id"] == "q1-s2"
                    assert "public.example" not in result.summary
            assert not psutil.pid_exists(int(pid_path.read_text()))
    await gateway.aclose()


def test_scoped_success_uses_gateway_tool_broker_and_structured_receipt(tmp_path: Path) -> None:
    asyncio.run(exercise(tmp_path, "normal"))


def test_only_whole_forwarded_search_references_can_support_a_task_result(tmp_path: Path) -> None:
    asyncio.run(exercise(tmp_path, "bounded-search"))


def test_model_tools_follow_remaining_authority_and_preserve_the_complete_transcript(
    tmp_path: Path,
) -> None:
    asyncio.run(exercise(tmp_path, "tool-policy"))


def test_private_context_revocation_stops_and_reaps_worker(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        pid_path = tmp_path / "worker.pid"
        config = config.model_copy(
            update={
                "hermes": HermesSettings(
                    command=(sys.executable, str(FIXTURE), "private", str(pid_path)),
                    revision=REVISION,
                    profile_sha256="a" * 64,
                    search_endpoint="http://installed.invalid",
                    private_recall=True,
                )
            }
        )
        character, session, turn = uuid4(), uuid4(), uuid4()
        recalled = asyncio.Event()
        valid = True
        dependency = ContextDependencies(policy_epoch=1)

        async def recall(query: str, context: ToolContext) -> PersonalContext:
            assert query == "Synthetic project" and context.character_id == character
            recalled.set()
            return PersonalContext("UNIQUE_PRIVATE_SENTINEL", dependency)

        async def validate(value: ContextDependencies) -> bool:
            assert value == dependency
            return valid

        async def progress(state: TaskState) -> None:
            pass

        class NoModel:
            async def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayEvent]:
                raise AssertionError("No model work expected")
                yield

        provider = HermesTask(
            config,
            NoModel(),
            ToolRouter(TextInspectionProvider(), character),
            recall=recall,
            validate=validate,
        )
        task = asyncio.create_task(
            provider.execute(
                TaskSubmission(
                    character_id=character,
                    session_id=session,
                    turn_id=turn,
                    idempotency_key="private",
                    goal="Recall synthetic project",
                    operation="delegate",
                ),
                ToolContext(
                    character_id=character,
                    session_id=session,
                    turn_id=turn,
                    task_id=uuid4(),
                    deadline_at=time() + 8,
                ),
                progress,
            )
        )
        await asyncio.wait_for(recalled.wait(), 3)
        valid = False
        with pytest.raises(TaskConflict, match="context_revoked"):
            await asyncio.wait_for(task, 4)
        assert not psutil.pid_exists(int(pid_path.read_text()))

    asyncio.run(run())


def test_disabled_provider_preserves_native_task_dispatch(tmp_path: Path) -> None:
    async def run() -> None:
        character = uuid4()
        router = TaskRouter(
            tmp_path,
            character,
            uuid4(),
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
        )
        await router.start()
        try:
            with pytest.raises(TaskConflict, match="provider_unavailable"):
                await router.submit("disabled", "Do work", operation="delegate")
            handle = await router.submit("native", "Still works")
            async with asyncio.timeout(3):
                while (
                    value := await router.status(handle.task_id)
                ) is None or value.state != "succeeded":
                    await asyncio.sleep(0.01)
            assert "2 words" in value.result
        finally:
            await router.aclose()

    asyncio.run(run())
