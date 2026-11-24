"""Conversation workflow regressions with synthetic models, public adapters and tasks."""

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from livekit.agents import llm
from pydantic import JsonValue

from ember_agent.character.conversation import CompletedTurn
from ember_agent.character.core import CharacterCore
from ember_agent.character.tool_workflow import (
    BUILD,
    INSPECT,
    PAGE,
    SEARCH,
    ManagedPlan,
    intent_for,
)
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import (
    GatewayEvent,
    GatewayRequest,
    GenerationUsage,
    ModelClient,
    PromptMessage,
    ToolCall,
)
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.ollama import NativeFrame, NativeFunction, NativeMessage, NativeTool
from ember_agent.inference.service import create_app
from ember_agent.observability import ActivityLog
from ember_agent.public_web import PublicWeb, WebBinding
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.realtime.livekit import EmberAgent
from ember_agent.runtime import harness as harness_runtime
from ember_agent.runtime import hosted_web, livekit_runtime, opencode_harness, public_web_runtime
from ember_agent.runtime.composition import LocalRuntime
from ember_agent.runtime.config import BuildSettings, HarnessSettings, PublicSearchSettings
from ember_agent.runtime.public_web_runtime import _snippet_retrieval
from ember_agent.runtime.task_router import TaskResult, ToolContext, ToolReceipt
from ember_agent.runtime.task_store import TaskState, TaskSubmission
from ember_agent.web_evidence import PublicWebOperation, WebEvidence, WebSource
from ember_memory_abi.interfaces import ContextRequest, ForgetRequest, RetrievalContext
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject

from .test_gateway import FakeUpstream, configuration
from .test_hosted_web import reply as web_reply

_QUERY = "public transit map design patterns"
_URL = "https://www.example.com/design"
_INSPECT = "Inspect the sample dashboard"
_BUILD = "build a small demo based on what you find"
_TURN = f"Search the web for {_QUERY} and {_BUILD}"
_USAGE = GenerationUsage(
    prompt_tokens=1,
    output_tokens=1,
    total_seconds=0,
    first_visible_seconds=None,
    tokens_per_second=None,
)


def _call(name: str, key: str, value: str) -> ToolCall:
    args: dict[str, JsonValue] = {key: value}
    return ToolCall(call_id=uuid4(), name=name, arguments=args)


class Model:
    def __init__(self, *steps: tuple[ToolCall, ...] | str) -> None:
        self.requests: list[GatewayRequest] = []
        self.steps = steps

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        response = self.steps[len(self.requests)]
        self.requests.append(GatewayRequest.model_validate_json(request.model_dump_json()))
        start = GatewayEvent(
            request_id=request.request_id, context_id=request.context_id, kind="start", sequence=0
        )
        yield start
        if isinstance(response, str):
            yield start.model_copy(update={"kind": "text", "sequence": 1, "text": response})
        else:
            yield start.model_copy(update={"kind": "tools", "sequence": 1, "calls": response})
        yield start.model_copy(update={"kind": "end", "sequence": 2, "usage": _USAGE})


class Web:
    def __init__(self) -> None:
        self.inputs: list[tuple[PublicWebOperation, str]] = []
        self.available = True
        self.started = asyncio.Event()
        self.block = False

    async def _run(self, operation: PublicWebOperation, value: str) -> WebEvidence:
        self.inputs.append((operation, value))
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        return WebEvidence(
            provider="exa",
            operation=operation,
            retrieved_at=datetime.now(UTC),
            coverage="partial" if self.available else "unavailable",
            failure=None if self.available else "timeout",
            sources=(
                WebSource(url=_URL, title="Design guide", passages=("Use strong contrast. " * 40,)),
            )
            if self.available
            else (),
        )

    async def search(self, query: str) -> WebEvidence:
        return await self._run("search", query)

    async def read(self, url: str) -> WebEvidence:
        return await self._run("read", url)


class Harness:
    def __init__(self, engine: CanonicalEngine, model: ModelClient) -> None:
        self.memory = InProcessAsyncMemoryClient(
            engine.character(engine.create_character().store_id)
        )
        self.web = Web()
        self.plans: list[tuple[ManagedPlan, UUID]] = []
        self.core = CharacterCore(self.memory, model, model_tag="scripted", input_ceiling=8192)
        self.core.public_web = PublicWeb(
            WebBinding("exa", "Exa", "fixture", self.web.search),
            WebBinding("exa", "Exa", "fixture", self.web.read),
        )
        self.core.activity = ActivityLog()
        self.core.submit_managed = self.submit
        self.core.inspect_ready = lambda: True
        self.core.build_ready = lambda: True
        self.core.history.append(CompletedTurn("PRIVATE_HISTORY", "PRIVATE_REPLY", ()))

    async def submit(self, plan: ManagedPlan, turn: UUID) -> str:
        self.plans.append((plan, turn))
        return "Queued in Tasks; disconnect Conversation to run."

    async def turn(self, text: str) -> str:
        turn = await self.core.prepare(text)
        return "".join([event.text async for event in self.core.stream(turn)])


def test_search_then_build_hands_off_only_current_evidence(tmp_path: Path) -> None:
    async def run() -> None:
        model = Model(
            (_call(SEARCH, "query", _QUERY),),
            (_call(BUILD, "goal", "Use high-contrast transit lines."),),
        )
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            try:
                text = await harness.turn(_TURN)
                assert harness.web.inputs == [("search", _QUERY)]
                assert "Queued in Tasks" in text and _URL in text and "shortened" in text
                assert len(model.requests) == 2
                first, second = model.requests
                assert (
                    first.context_id == second.context_id and first.request_id != second.request_id
                )
                assert [tool.function.name for tool in first.tools] == [
                    SEARCH,
                    PAGE,
                    INSPECT,
                    BUILD,
                ]
                assert [tool.function.name for tool in second.tools] == [BUILD]
                assert first.messages[-1].content == _TURN
                assert second.messages[-1].role == "tool"
                dump = second.model_dump_json()
                assert "Use strong contrast" in dump
                assert _URL not in dump and "PRIVATE_" not in dump
                [(plan, turn_id)] = harness.plans
                assert turn_id == first.request_id and plan.inspect is None
                assert harness.core.history[-1].request_id == turn_id
                assert plan.build and "Use high-contrast transit lines" in plan.build
                assert "Use strong contrast" not in plan.build
                assert plan.sources[0].url == _URL and plan.sources[0].source_id
                assert harness.core.history[-1].public_web_reply
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("first", [SEARCH, INSPECT])
def test_chain_through_composed_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str
) -> None:
    async def run() -> None:
        base = configuration(tmp_path)
        config = base.model_copy(
            update={
                "data_root": tmp_path / "data",
                "runtime_root": tmp_path / "runtime",
                "harness": HarnessSettings(
                    provider="opencode",
                    command=(str(tmp_path / "fixture"),),
                    revision="2.3.2",
                    profile_sha256="0" * 64,
                    workspace=tmp_path / "workspace",
                    build=BuildSettings(docker=tmp_path / "docker", image="sha256:" + "0" * 64),
                ),
                "public_search": PublicSearchSettings(
                    provider="exa", api_key_env="FIXTURE_EXA_KEY"
                ),
            }
        )
        config.runtime_root.mkdir()
        token = "f" * 32
        agent_token = "a" * 32
        (config.runtime_root / "gateway.token").write_text(token)
        (config.runtime_root / "gateway-agent.token").write_text(agent_token)
        web_inputs: list[str] = []

        def serve(request: httpx.Request) -> httpx.Response:
            assert (request.url.host, request.url.path) == ("api.exa.ai", "/search")
            assert request.headers["x-api-key"] == "synthetic-key"
            payload = json.loads(request.content)
            assert payload["query"] == _QUERY and b"PRIVATE_HISTORY" not in request.content
            web_inputs.append(payload["query"])
            return web_reply(
                {
                    "results": [
                        {
                            "url": _URL,
                            "title": "Design guide",
                            "highlights": ["Use strong contrast"],
                        }
                    ]
                },
            )

        def hosted(provider: hosted_web.HostedWebName, env: str) -> hosted_web.HostedWeb:
            return adapter(provider, env, transport=httpx.MockTransport(serve))

        adapter = hosted_web.HostedWeb
        monkeypatch.setenv("FIXTURE_EXA_KEY", "synthetic-key")
        monkeypatch.setattr(public_web_runtime, "HostedWeb", hosted)

        class Upstream(FakeUpstream):
            async def generate(self, value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
                self.count += 1
                assert "PRIVATE_HISTORY" not in value.model_dump_json()
                if value.workload == "P2":
                    yield NativeFrame(message=NativeMessage(content="worker step"), done=True)
                    return
                if self.count == 1:
                    name, args = (
                        first,
                        ({"query": _QUERY} if first == SEARCH else {"goal": _INSPECT}),
                    )
                else:
                    marker = "Use strong contrast" if first == SEARCH else "Inspect staged, not run"
                    assert (
                        value.messages[-1].role == "tool" and marker in value.messages[-1].content
                    )
                    assert [tool.function.name for tool in value.tools] == [BUILD]
                    name, args = BUILD, {"goal": "Use high-contrast transit lines."}
                function = NativeFunction(name=name, arguments=json.dumps(args))
                yield NativeFrame(message=NativeMessage(tool_calls=[NativeTool(function=function)]))
                yield NativeFrame(done=True, done_reason="tool_calls")

        seen: list[TaskSubmission] = []
        task_ids: dict[str, UUID] = {}

        class Worker:
            available = True

            def __init__(self, model: ModelClient) -> None:
                self.model = model

            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                seen.append(request)
                task_ids[request.operation] = context.task_id
                assert context.deadline_at is not None
                model_request = GatewayRequest(
                    request_id=uuid4(),
                    context_id=context.task_id,
                    model=config.model_tag,
                    workload="P2",
                    messages=(PromptMessage(role="user", content="Synthetic worker prompt"),),
                    deadline_at=datetime.fromtimestamp(context.deadline_at, UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                )
                assert [event async for event in self.model.stream(model_request)][-1].kind == "end"
                await progress("waiting_for_tool")
                inspect = request.operation == "delegate"
                return TaskResult(
                    summary="Synthetic workspace finding" if inspect else "Synthetic Build",
                    receipt=ToolReceipt(
                        context=context,
                        tool=request.operation,
                        data={
                            "provider": "opencode",
                            "tools": [{"name": "read", "status": "completed"}],
                        }
                        if inspect
                        else {"artifact": {"sha256": "0" * 64, "size_bytes": 22}},
                    ),
                )

        @asynccontextmanager
        async def open_inspect(
            _config: object,
            model: ModelClient,
            *_: object,
        ) -> AsyncGenerator[tuple[Worker, Callable[[], bool]], None]:
            worker = Worker(model)
            yield worker, lambda: worker.available

        @asynccontextmanager
        async def open_build(
            _config: object,
            _settings: object,
            model: ModelClient,
            *_: object,
        ) -> AsyncGenerator[Worker, None]:
            yield Worker(model)

        upstream = Upstream()
        gateway = P0Gateway(config, upstream)
        app = create_app(gateway, token, agent_token=agent_token)

        def gateway_client(endpoint: str, key: str, **kwargs: object) -> HttpGatewayClient:
            return HttpGatewayClient(
                endpoint,
                key,
                idle_managed_tasks=kwargs.get("idle_managed_tasks") is True,
                client=httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://gateway",
                ),
            )

        monkeypatch.setattr(harness_runtime, "open_harness", open_inspect)
        monkeypatch.setattr(opencode_harness, "open_build", open_build)
        monkeypatch.setattr(livekit_runtime, "HttpGatewayClient", gateway_client)
        runtime = LocalRuntime(config)
        await runtime.model.aclose()
        runtime.model = gateway_client("http://gateway", token)
        runtime.core.model = runtime.model
        instance = uuid4()
        binding = RoomBinding(
            instance_id=instance,
            character_id=runtime.memory.character_id,
            room=f"ember-{instance.hex}",
            endpoint="ws://127.0.0.1:7880",
        )
        try:
            await runtime.start_public_search()
            async with AsyncExitStack() as cleanup:
                router = await livekit_runtime._start_tasks(runtime, binding, cleanup)
                assert router is not None
                core = runtime.core
                core.history.append(CompletedTurn("PRIVATE_HISTORY", "PRIVATE_REPLY", ()))
                await runtime.model.session(core.context_id, connected=True)
                try:
                    turn = await core.prepare(
                        _TURN if first == SEARCH else f"{_INSPECT} and {_BUILD}"
                    )
                    assert "queued" in "".join([event.text async for event in core.stream(turn)])
                    async with asyncio.timeout(5):
                        while True:
                            child_id = task_ids.get("build")
                            status = await router.status(child_id) if child_id else None
                            if status and status.receipt:
                                break
                            await asyncio.sleep(0.01)
                    assert (await runtime.model.health()).realtime_sessions == (core.context_id,)
                    assert web_inputs == ([_QUERY] if first == SEARCH else [])
                finally:
                    await runtime.model.session(core.context_id, connected=False)
                data = TaskResult.model_validate_json(status.receipt).receipt.data
                assert status.state == "succeeded"
                if first == SEARCH:
                    assert [request.operation for request in seen] == ["build"]
                    sources = data["public_sources"]
                    assert isinstance(sources, list) and len(sources) == 1
                    assert isinstance(sources[0], dict) and sources[0]["url"] == _URL
                else:
                    assert data["inspect_task_id"] == str(task_ids["delegate"])
                    assert [request.operation for request in seen] == ["delegate", "build"]
                    assert "Synthetic workspace finding" in seen[-1].goal
        finally:
            await runtime.aclose()
            runtime.close_workers()
            await gateway.aclose()

    asyncio.run(run())


def test_inspect_then_build_stages_both_before_submission(tmp_path: Path) -> None:
    async def run() -> None:
        model = Model(
            (_call(INSPECT, "goal", _INSPECT),), (_call(BUILD, "goal", "Build a dashboard demo."),)
        )
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            try:
                text = await harness.turn(f"{_INSPECT} and {_BUILD}")
                assert "Queued in Tasks" in text and not harness.web.inputs
                [(plan, _)] = harness.plans
                assert plan == ManagedPlan(inspect=_INSPECT, build=_BUILD)
                assert "Inspect staged, not run" in model.requests[1].messages[-1].content
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "case", ["rewrite", "private", "batched", "out_of_order", "unavailable", "no_build", "no_topic"]
)
def test_rejected_steps_never_start_build(tmp_path: Path, case: str) -> None:
    async def run() -> None:
        text = _TURN
        search = _call(SEARCH, "query", _QUERY)
        build = _call(BUILD, "goal", "Build a dashboard demo.")
        calls = (search,)
        if case == "rewrite":
            calls = (_call(SEARCH, "query", "transit UX"),)
        elif case == "private":
            text = f"Search the web for private notes PRIVATE_CONTEXT and {_BUILD}"
            calls = (_call(SEARCH, "query", "private notes PRIVATE_CONTEXT"),)
        elif case == "no_topic":
            text = f"Search the web and {_BUILD}"
        elif case == "batched":
            calls = (search, build)
        elif case == "out_of_order":
            calls = (build,)
        model = Model(calls)
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            harness.web.available = case != "unavailable"
            harness.core.build_ready = lambda: case != "no_build"
            try:
                reply = await harness.turn(text)
                assert "Queued in Tasks" not in reply and not harness.plans
                assert harness.web.inputs == ([("search", _QUERY)] if case == "unavailable" else [])
                if case == "rewrite":
                    assert "Exa search: transit UX" in reply
                if case == "private":
                    assert "PRIVATE_CONTEXT" not in reply
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        "Build a webapp after inspecting the dashboard",
        "Build: Build a webapp after inspecting the dashboard",
        "Build a webapp and inspect the dashboard",
        "Build: Build a webapp and search for transit map design",
    ],
)
def test_unsupported_prerequisites_never_fall_back_to_standalone_build(
    tmp_path: Path, text: str
) -> None:
    async def run() -> None:
        model = Model()
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)

            async def forbidden(goal: str, turn_id: UUID) -> str:
                raise AssertionError("Standalone Build discarded its prerequisite")

            harness.core.submit_build = forbidden
            try:
                reply = await harness.turn(text)
                assert "No task was queued" in reply
                assert not model.requests and not harness.plans
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


def test_cancel_inflight_retrieval_prevents_build(tmp_path: Path) -> None:
    async def run() -> None:
        model = Model((_call(SEARCH, "query", _QUERY),))
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            harness.web.block = True
            try:
                task = asyncio.create_task(harness.turn(_TURN))
                await asyncio.wait_for(harness.web.started.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not harness.plans and len(model.requests) == 1 and not harness.core.busy
                assert harness.core.activity
                assert any(
                    event.kind == "tool" and event.status == "cancelled"
                    for event in harness.core.activity.since(0)
                )
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "request_text",
    [
        f"Read {_URL}",
        f"Read the\tpage <{_URL}>",
        f"Summarise this labels music {_URL}",
        f"Fetch the data from this page {_URL.removeprefix('https://')}",
        f"fetch this page [{_URL}]({_URL})",
        f"sick but I want you to read the contents of this page and tell me what you think {_URL}",
        f"Use Exa {_URL}",
    ],
)
def test_public_page_without_commands_and_no_private_url_egress(
    tmp_path: Path, request_text: str
) -> None:
    async def run() -> None:
        private = "http://router.nas/secret"
        model = Model('{"answer":"Use strong contrast.","sources":[1]}')
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            try:
                assert "Nothing was sent" in await harness.turn(f"Read {private}")
                assert not harness.web.inputs
                text = await harness.turn(request_text)
                assert "Use strong contrast." in text and _URL in text
                assert harness.web.inputs == [("read", _URL)] and not harness.plans
                assert not model.requests[-1].tools
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "request_text",
    [
        "lets build a basic python hello world webapp",
        "Let’s build a basic Python hello world webapp",
        "Build a webapp that sends reminders when bills are due",
        "Let us build a webapp that sends reminders when bills are due",
    ],
)
def test_affirmative_build_uses_local_planner(tmp_path: Path, request_text: str) -> None:
    async def run() -> None:
        model = Model((_call(BUILD, "goal", "Build a Python hello world webapp"),))
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            try:
                assert "Queued in Tasks" in await harness.turn(request_text)
                [(plan, _)] = harness.plans
                assert plan == ManagedPlan(build=request_text) and not harness.web.inputs
                [request] = model.requests
                assert BUILD in {tool.function.name for tool in request.tools}
                assert request.messages[-1].content == request_text
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("available", [True, False])
def test_described_page_then_cooperative_build(tmp_path: Path, available: bool) -> None:
    async def run() -> None:
        goal = "let's build a small website based on what you find"
        model = Model((_call(PAGE, "url", _URL),), (_call(BUILD, "goal", "Use strong contrast"),))
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            harness.web.available = available
            try:
                answer = await harness.turn(f"Summarise this label's music <{_URL}>, then {goal}")
                assert harness.web.inputs == [("read", _URL)]
                if not available:
                    assert "No other provider was used or task queued" in answer
                    assert not harness.plans and len(model.requests) == 1
                    return
                assert "Queued in Tasks" in answer
                first, next_step = model.requests
                assert [tool.function.name for tool in next_step.tools] == [BUILD]
                assert first.request_id != next_step.request_id
                assert "Use strong contrast" in next_step.messages[-1].content
                [(plan, _)] = harness.plans
                assert plan.build and plan.build.startswith(goal)
                assert plan.sources[0].url == _URL
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        f"Summarise this label {_URL} or https://www.example.com/other",
        f"Summarise this label {_URL} with PRIVATE_CURRENT_COMMENTARY",
        f"Summarise a page if I approve {_URL}",
        f"Summarise how to read {_URL}",
        f"Explain how you would summarize <{_URL}>",
        f"Explain the process of summarizing <{_URL}>",
        f"Summarise this example request to read {_URL}",
        f"Summarise my private notes at {_URL}",
        f"Summarise this label {_URL}?token=secret",
    ],
)
def test_ambiguous_page_proposal_has_no_egress(tmp_path: Path, text: str) -> None:
    async def run() -> None:
        model = Model((_call(PAGE, "url", _URL),), "Please give me an exact page link.")
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            try:
                assert "public page link" in await harness.turn(text) or len(model.requests) == 2
                assert not harness.web.inputs and not harness.plans
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "condition",
    [
        "only if I approve",
        "only after reviewing the dashboard",
        ", but wait for my go-ahead",
        "pending my approval",
    ],
)
@pytest.mark.parametrize("page_first", [False, True])
def test_conditional_cooperative_build_never_queues(
    tmp_path: Path, condition: str, page_first: bool
) -> None:
    async def run() -> None:
        goal = f"Let’s build a website {condition}"
        build = (_call(BUILD, "goal", "Build a website"),)
        model = (
            Model((_call(PAGE, "url", _URL),), build, "I'll wait.")
            if page_first
            else Model(build, "I'll wait.")
        )
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            try:
                text = f"Read {_URL}, then {goal}" if page_first else goal
                assert await harness.turn(text)
                assert not harness.web.inputs and not harness.plans
                assert len(model.requests) <= 2
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        f'"{_TURN}"',
        f"Don't {_TURN}",
        "Do you support Build?",
        "Inspect the dashboard. Don’t inspect it yet.",
        f"Search for {_QUERY} but do not search yet",
    ],
)
def test_mere_mentions_do_not_grant_tools(text: str) -> None:
    assert intent_for(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "How are you?",
        "Binary search: why is it logarithmic?",
        "Cold read: what is it?",
        "What's my artist name?",
        "What was my favourite set and when?",
        "List some of the best sets I told you about at the club",
    ],
)
def test_no_proposal_preserves_ordinary_reply(tmp_path: Path, text: str) -> None:
    async def run() -> None:
        model = Model("Ordinary reply")
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            retrieve = harness.memory.retrieve_context
            recalled: list[ContextRequest] = []

            async def recall(request: ContextRequest) -> RetrievalContext:
                assert not model.requests
                recalled.append(request)
                return await retrieve(request)

            harness.memory.retrieve_context = recall
            harness.core.history.extend(
                (
                    CompletedTurn("Binary search: what is it?", "PRIVATE_ORDINARY_REPLY", ()),
                    CompletedTurn(f"Exa\tMCP read: {_URL}", "PRIVATE_LEGACY_WEB_REPLY", ()),
                    CompletedTurn(
                        f"Read {_URL}", "PRIVATE_MARKED_WEB_REPLY", (), public_web_reply=True
                    ),
                )
            )
            try:
                identity = uuid4()
                turn = await harness.core.prepare(text, request_id=identity)
                reply = "".join([event.text async for event in harness.core.stream(turn)])
                assert reply == "Ordinary reply"
                assert len(recalled) == 1
                assert not harness.plans and not harness.web.inputs
                (ordinary,) = model.requests
                assert [tool.function.name for tool in ordinary.tools] == [
                    SEARCH,
                    PAGE,
                    INSPECT,
                    BUILD,
                ]
                assert ordinary.request_id == identity
                prompt = ordinary.model_dump_json()
                assert "PRIVATE_HISTORY" in prompt and "PRIVATE_ORDINARY_REPLY" in prompt
                assert "PRIVATE_LEGACY_WEB_REPLY" not in prompt
                assert "PRIVATE_MARKED_WEB_REPLY" not in prompt
                assert not harness.core.history[-1].public_web_reply
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [f"Exa MCP read: {_URL}", f"Parallel search: {_QUERY}", f"Fixture Reader search: {_QUERY}"],
)
def test_unbound_provider_shortcut_never_falls_back(tmp_path: Path, text: str) -> None:
    async def run() -> None:
        model = Model()
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            harness.core.public_web = PublicWeb(
                harness.core.public_web.search_binding,
                WebBinding("fixture_reader", "Fixture Reader", "fixture", harness.web.read),
            )
            try:
                turn = await harness.core.prepare(text)
                assert turn.local_reply and "No request was sent" in turn.local_reply
                assert not model.requests and not harness.web.inputs and not harness.plans
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("text", "name", "arg", "value"),
    [
        ("List the best sets I told you about", INSPECT, "goal", _INSPECT),
        ("What was my favourite set?", SEARCH, "query", "public design guide"),
        (f"I bookmarked {_URL}", PAGE, "url", _URL),
        (f"Don't read {_URL}", PAGE, "url", _URL),
        (f"Check {_URL}", INSPECT, "goal", f"Check {_URL}"),
    ],
)
def test_proposal_does_not_grant_authority(
    tmp_path: Path, text: str, name: str, arg: str, value: str
) -> None:
    async def run() -> None:
        model = Model((_call(name, arg, value),), "Ordinary reply")
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            try:
                assert await harness.turn(text) == "Ordinary reply"
                first, fallback = model.requests
                assert first.request_id != fallback.request_id
                assert first.messages == fallback.messages and not fallback.tools
                assert not harness.plans and not harness.web.inputs
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("text", [f"Search the web for {_QUERY}", f"Find me {_QUERY} online"])
def test_snippet_search_stays_snippet_only(tmp_path: Path, text: str) -> None:
    async def run() -> None:
        model = Model((_call(SEARCH, "query", _QUERY),), "Use strong contrast.")
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)

            async def search(query: str) -> str:
                assert query == _QUERY
                return json.dumps(
                    {
                        "results": [
                            {"url": _URL, "title": "Guide", "snippet": "Use strong contrast."}
                        ]
                    }
                )

            harness.core.public_web = PublicWeb(
                WebBinding(
                    "searxng",
                    "SearXNG",
                    "fixture",
                    _snippet_retrieval("searxng", search),
                    snippets=True,
                )
            )
            try:
                answer = await harness.turn(text)
                assert "search snippets only" in answer and "excerpts" not in answer
                assert _URL in answer and not harness.plans
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("case", ["approved", "ambiguous", "revoked", "interrupted", "intervening"])
def test_profile_followup_is_evidence_bound(tmp_path: Path, case: str) -> None:
    profile_url = "https://www.soundstage.org/artists/synthetic"

    async def run() -> None:
        model = Model(
            _WEB_SUMMARY if case == "approved" else "Ordinary reply",
            "Ordinary reply",
        )
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            authority = MutationAuthority(mode="confirmed_user")
            author = MemoryAuthoringService(
                engine, ensure_subject(engine, harness.memory.character_id), authority=authority
            )
            source = f"My Soundstage profile is {profile_url}"
            if case == "ambiguous":
                source += " or https://www.soundstage.org/other"
            memory = author.create(NewMemoryInput(idempotency_key="profile", text=source)).affected[
                0
            ]

            async def voice(text: str, *, complete: bool = True) -> str:
                agent = EmberAgent(harness.core, defer_delivery=True)
                context = llm.ChatContext()
                message = context.add_message(role="user", content=text)
                await agent.on_user_turn_completed(context, message)
                stream = agent.gateway_llm.chat(chat_ctx=context)
                try:
                    reply = "".join(
                        [chunk.delta.content or "" async for chunk in stream if chunk.delta]
                    )
                finally:
                    await stream.aclose()
                turn_id = agent.gateway_llm.completed_request_id
                assert turn_id is not None
                await harness.core.acknowledge(
                    turn_id, reply if complete else reply[:3], complete=complete
                )
                return reply

            try:
                reply = await voice(
                    "Read my Soundstage profile, thoughts?", complete=case != "interrupted"
                )
                assert not model.requests and not harness.web.inputs and not harness.plans
                if case == "ambiguous":
                    assert "Paste one public link" in reply and profile_url not in reply
                    return
                assert profile_url in reply and "with Exa" in reply
                if case == "revoked":
                    engine.character(harness.memory.character_id, authority=authority).forget(
                        ForgetRequest(
                            idempotency_key="forget-profile",
                            memory_id=memory.memory_id,
                            expected_revision=memory.revision,
                            reason="user_request",
                        )
                    )
                if case == "intervening":
                    assert await voice("Yes") == "Ordinary reply"
                    assert not harness.web.inputs
                response = await voice(f"from here !!! [{profile_url}]({profile_url})")
                if case == "approved":
                    assert harness.web.inputs == [("read", profile_url)] and not harness.plans
                    assert "Use strong contrast" in response
                    (synthesis,) = model.requests
                    assert "Read my Soundstage profile, thoughts?" in synthesis.messages[-1].content
                else:
                    assert not harness.web.inputs
                    assert (
                        "directly" in response
                        if case == "revoked"
                        else response == "Ordinary reply"
                    )
            finally:
                await harness.memory.aclose()

    asyncio.run(run())


_WEB_SUMMARY = '{"answer":"Use strong contrast.","sources":[1]}'


def test_visible_voice_reply_discards_late_tool(tmp_path: Path) -> None:
    class MixedModel(Model):
        async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
            async for event in super().stream(request):
                if event.kind == "text":
                    yield event
                    yield event.model_copy(
                        update={
                            "kind": "tools",
                            "sequence": 2,
                            "text": "",
                            "calls": (_call(SEARCH, "query", _QUERY),),
                        }
                    )
                else:
                    yield event.model_copy(update={"sequence": 3}) if event.kind == "end" else event

    async def run() -> None:
        model = MixedModel("I remember the opening set.")
        with CanonicalEngine(tmp_path) as engine:
            harness = Harness(engine, model)
            try:
                agent = EmberAgent(harness.core, defer_delivery=True)
                context = llm.ChatContext()
                message = context.add_message(
                    role="user", content="Which set did I tell you about?"
                )
                await agent.on_user_turn_completed(context, message)
                stream = agent.gateway_llm.chat(chat_ctx=context)
                try:
                    chunks = [chunk async for chunk in stream]
                finally:
                    await stream.aclose()
                reply = "".join(chunk.delta.content or "" for chunk in chunks if chunk.delta)
                assert reply == "I remember the opening set."
                assert chunks[-1].usage is not None and len(model.requests) == 1
                assert not harness.web.inputs and not harness.plans
                turn_id = agent.gateway_llm.completed_request_id
                assert turn_id is not None
                await harness.core.acknowledge(turn_id, "I remember", complete=False)
                assert harness.core.history[-1].assistant == "I remember"
            finally:
                await harness.memory.aclose()

    asyncio.run(run())
