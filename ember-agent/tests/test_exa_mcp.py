"""Source-derived Exa fixtures; no network requests."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import time
from typing import Literal
from uuid import uuid4

import httpx
import pytest
from pydantic import JsonValue

from ember_agent.character.conversation import CompletedTurn
from ember_agent.character.tool_workflow import BUILD, PAGE, SEARCH
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import GatewayError, GatewayHealth, ToolCall
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.runtime import (
    exa_mcp,
    harness,
    hosted_web,
    livekit_runtime,
    opencode_harness,
    research,
)
from ember_agent.runtime.browser_control import TaskInput
from ember_agent.runtime.capabilities import capability_catalog
from ember_agent.runtime.composition import LocalRuntime
from ember_agent.runtime.config import BuildSettings, HarnessSettings, PublicSearchSettings
from ember_agent.runtime.exa_mcp import ExaMCP
from ember_agent.runtime.task_router import TaskResult, ToolContext, ToolReceipt
from ember_agent.runtime.task_store import TaskConflict, TaskSubmission
from ember_agent.web_evidence import WebFailure

_URL = "https://www.example.com/article"
_QUERY = "public transit map design"
_SEARCH_TEXT = (
    "Note: Retrieved public results\n\n"
    f"Title: Example guide\nURL: {_URL}\nPublished: N/A\nAuthor: N/A\n"
    "Highlights:\nUse strong contrast."
)
_PAGE_TEXT = f"# Example guide\nURL: {_URL}\nPublished: 2026-10-01\n\nUse strong contrast."
_ENDPOINT = "https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa"
_VERSION = "2025-06-18"


class Bytes(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _rpc(
    request_id: int, result: dict[str, JsonValue], *, sse: bool = False, session: bool = False
) -> httpx.Response:
    raw = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}).encode()
    headers = {"set-cookie": "token=synthetic-do-not-forward"}
    if session:
        headers["mcp-session-id"] = "fixture-session"
    if sse:
        raw = b": keepalive\r\n\r\nevent: message\r\ndata: " + raw + b"\r\n\r\n"
        headers["content-type"] = "text/event-stream"
        chunks = (raw[:15], raw[15:-2], raw[-2:])
    else:
        headers["content-type"] = "application/json"
        chunks = (raw,)
    return httpx.Response(200, headers=headers, stream=Bytes(*chunks))


def _tool(
    text: str, *, request_id: int = 2, sse: bool = False, is_error: bool = False
) -> httpx.Response:
    return _rpc(
        request_id, {"content": [{"type": "text", "text": text}], "isError": is_error}, sse=sse
    )


class Server:
    def __init__(self, result: httpx.Response, *, sse: bool = False, session: bool = True) -> None:
        self.result = result
        self.sse = sse
        self.session = session
        self.requests: list[dict[str, JsonValue]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and str(request.url) == _ENDPOINT
        assert request.headers["accept"] == "application/json, text/event-stream"
        assert request.headers["accept-encoding"] == "identity"
        assert not {"authorization", "x-api-key", "proxy-authorization", "cookie"}.intersection(
            request.headers
        )
        payload = json.loads(request.content)
        self.requests.append(payload)
        if len(self.requests) == 1:
            assert "mcp-protocol-version" not in request.headers
            assert "mcp-session-id" not in request.headers
            assert payload == {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "ember-public-web", "version": "1"},
                },
            }
            return _rpc(
                1,
                {"protocolVersion": _VERSION, "capabilities": {"tools": {}}},
                sse=self.sse,
                session=self.session,
            )
        assert request.headers["mcp-protocol-version"] == _VERSION
        assert request.headers.get("mcp-session-id") == (
            "fixture-session" if self.session else None
        )
        if len(self.requests) == 2:
            assert payload == {"jsonrpc": "2.0", "method": "notifications/initialized"}
            return httpx.Response(202, headers={"set-cookie": "token=do-not-forward-either"})
        assert len(self.requests) == 3
        return self.result

    @property
    def adapter(self) -> ExaMCP:
        return ExaMCP(transport=httpx.MockTransport(self))


@pytest.mark.parametrize("operation", ["search", "read"])
@pytest.mark.parametrize("sse", [False, True])
def test_exact_keyless_call_and_source_evidence(
    monkeypatch: pytest.MonkeyPatch, operation: Literal["search", "read"], sse: bool
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "synthetic-do-not-forward")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@example.com:8080")
    text = _SEARCH_TEXT if operation == "search" else _PAGE_TEXT
    server = Server(_tool(text, sse=sse), sse=sse, session=sse)
    evidence = asyncio.run(
        server.adapter.search(_QUERY) if operation == "search" else server.adapter.read(_URL)
    )
    assert len(server.requests) == 3
    assert server.requests[-1] == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "web_search_exa" if operation == "search" else "web_fetch_exa",
            "arguments": {"query": _QUERY, "numResults": 1}
            if operation == "search"
            else {"urls": [_URL], "maxCharacters": 2600},
        },
    }
    assert evidence.provider == "exa_mcp" and evidence.operation == operation
    assert evidence.coverage == "partial" and not evidence.truncated
    (source,) = evidence.sources
    assert source.url == _URL and source.title == "Example guide"
    assert source.passages == ("Use strong contrast.",)
    assert server.result.is_closed


@pytest.mark.parametrize(
    ("operation", "value"),
    [
        ("search", "private notes and comments"),
        ("search", "the design we discussed"),
        ("search", "public design category:people"),
        ("search", "first public topic\nsecond public topic"),
        ("search", "https://www.example.com"),
        ("search", "á" * 201),
        ("read", "http://127.0.0.1"),
        ("read", "https://docs.internal/public"),
        ("read", "https://user:password@example.com"),
        ("read", f"{_URL}?token=synthetic"),
        ("read", f"{_URL} https://example.net"),
    ],
)
def test_invalid_inputs_do_not_even_initialize(operation: str, value: str) -> None:
    def forbidden(_: httpx.Request) -> httpx.Response:
        raise AssertionError("Rejected input made a provider request")

    adapter = ExaMCP(transport=httpx.MockTransport(forbidden))
    evidence = asyncio.run(adapter.search(value) if operation == "search" else adapter.read(value))
    assert evidence.failure == "input_rejected" and not evidence.sources


@pytest.mark.parametrize(
    ("response", "failure"),
    [
        (lambda: httpx.Response(403), "unavailable"),
        (lambda: httpx.Response(429), "unavailable"),
        (lambda: httpx.Response(302, headers={"location": "https://example.org"}), "unavailable"),
        (
            lambda: _tool("provider error includes private synthetic text", is_error=True),
            "unavailable",
        ),
        (lambda: _tool(_PAGE_TEXT, request_id=9), "invalid_response"),
        (lambda: _tool(_PAGE_TEXT.replace(_URL, "https://example.org")), "invalid_response"),
        (
            lambda: _tool(_PAGE_TEXT + "\n\n# Other\nURL: https://example.org\n\nMore"),
            "invalid_response",
        ),
        (lambda: _tool(_PAGE_TEXT + f"\n\nError fetching {_URL}: unknown error"), "unavailable"),
        (
            lambda: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Bytes(b"x" * 131_073)
            ),
            "response_bound",
        ),
        (
            lambda: httpx.Response(
                200, headers={"content-type": "application/json", "content-encoding": "gzip"}
            ),
            "invalid_response",
        ),
    ],
)
def test_fail_closed_without_fallback(
    response: Callable[[], httpx.Response], failure: WebFailure
) -> None:
    server = Server(response())
    evidence = asyncio.run(server.adapter.read(_URL))
    assert len(server.requests) == 3
    assert evidence.failure == failure and not evidence.sources
    assert "synthetic" not in evidence.model_dump_json()


@pytest.mark.parametrize("read", [False, True])
def test_empty_response_matches_source_fixture(read: bool) -> None:
    text = (
        "No content found for the provided URL(s)."
        if read
        else "No search results found. Please try a different query."
    )
    server = Server(_tool(text))
    evidence = asyncio.run(server.adapter.read(_URL) if read else server.adapter.search(_QUERY))
    assert evidence.coverage == "empty" and evidence.failure is None and not evidence.sources


def test_timeout_has_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exa_mcp, "_TIMEOUT", 0.01)
    server = Server(_tool(_PAGE_TEXT))

    async def stalled(request: httpx.Request) -> httpx.Response:
        response = server(request)
        if len(server.requests) == 3:
            await asyncio.Event().wait()
        return response

    adapter = ExaMCP(transport=httpx.MockTransport(stalled))
    evidence = asyncio.run(adapter.read(_URL))
    assert evidence.failure == "timeout" and len(server.requests) == 3


def test_search_truncates_on_utf8_byte_limit() -> None:
    server = Server(_tool(_SEARCH_TEXT + "📖" * 500))
    evidence = asyncio.run(server.adapter.search(_QUERY))
    assert evidence.truncated and evidence.sources[0].truncated
    assert len(evidence.sources[0].passages[0].encode()) <= 900


def test_cancellation_propagates_without_more_requests() -> None:
    async def run() -> None:
        started = asyncio.Event()
        calls: list[httpx.Request] = []

        async def wait(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("Cancelled request resumed")

        adapter = ExaMCP(transport=httpx.MockTransport(wait))
        task = asyncio.create_task(adapter.search(_QUERY))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("chain_build", [False, True])
def test_composition_shares_mcp_with_tasks_only_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chain_build: bool
) -> None:
    from .test_gateway import configuration
    from .test_hosted_web import SummaryModel
    from .test_opencode_harness import _terminal
    from .test_tool_workflow import Model, _call

    server = Server(_tool(_SEARCH_TEXT if chain_build else _PAGE_TEXT, sse=True), sse=True)
    adapter = server.adapter
    builds: list[tuple[TaskSubmission, ToolContext]] = []
    monkeypatch.setattr(exa_mcp, "ExaMCP", lambda: adapter)

    def forbidden(*_: object) -> None:
        pytest.fail("Keyless retrieval must not construct a REST fallback")

    class ResearchWorker:
        available = True

        async def execute(
            self, request: TaskSubmission, context: ToolContext, _progress: object
        ) -> TaskResult:
            assert request.operation == "research" and request.research_provider == "exa_mcp"
            assert request.goal == f"Search: {_QUERY}"
            return TaskResult(
                summary="Public task completed.",
                receipt=ToolReceipt(context=context, tool="research", data={}),
            )

    async def gateway_health(_client: HttpGatewayClient) -> GatewayHealth:
        return GatewayHealth(
            status="ready",
            model="synthetic:27b",
            digest="a" * 64,
            context_tokens=8192,
            realtime_sessions=(uuid4(),),
            features=("idle_managed_tasks_v1",),
        )

    @asynccontextmanager
    async def open_worker(
        _config: object,
        settings: HarnessSettings,
        client: HttpGatewayClient,
        _observe: object,
        *,
        research: research.ResearchBroker,
    ) -> AsyncIterator[ResearchWorker]:
        assert settings.research
        assert research.backend == "exa_mcp" and research.provider is adapter
        assert research.tools == ("emberweb_search", "emberweb_page")
        assert client.headers.get("X-Ember-Managed-Task") == "idle"
        yield ResearchWorker()

    @asynccontextmanager
    async def unavailable_inspect(*_: object) -> AsyncIterator[None]:
        raise TaskConflict("fixture_no_workspace")
        yield  # pragma: no cover

    class BuildWorker:
        available = True

        async def execute(
            self, request: TaskSubmission, context: ToolContext, progress: object
        ) -> TaskResult:
            builds.append((request, context))
            return TaskResult(
                summary="Synthetic Build",
                receipt=ToolReceipt(
                    context=context,
                    tool="build",
                    data={"artifact": {"sha256": "0" * 64, "size_bytes": 22}},
                ),
            )

    @asynccontextmanager
    async def open_build(*_: object) -> AsyncIterator[BuildWorker]:
        yield BuildWorker()

    monkeypatch.setattr(hosted_web, "HostedWeb", forbidden)
    monkeypatch.setattr(research, "open_opencode", open_worker)
    monkeypatch.setattr(harness, "open_harness", unavailable_inspect)
    monkeypatch.setattr(opencode_harness, "open_build", open_build)
    monkeypatch.setattr(HttpGatewayClient, "health", gateway_health)

    async def run() -> None:
        config = configuration(tmp_path).model_copy(
            update={
                "data_root": tmp_path / "data",
                "runtime_root": tmp_path / "runtime",
                "public_search": PublicSearchSettings(provider="exa_mcp"),
                "harness": HarnessSettings(
                    provider="opencode",
                    command=(str(tmp_path / "fixture"),),
                    revision="2.3.2",
                    profile_sha256="0" * 64,
                    workspace=tmp_path / "workspace",
                    research=not chain_build,
                    build=BuildSettings(docker=tmp_path / "docker", image="sha256:" + "0" * 64)
                    if chain_build
                    else None,
                ),
            }
        )
        config.runtime_root.mkdir()
        for name in ("gateway", "gateway-agent"):
            (config.runtime_root / f"{name}.token").write_text("f" * 32)
        runtime = LocalRuntime(config)
        instance = uuid4()
        binding = RoomBinding(
            instance_id=instance,
            character_id=runtime.memory.character_id,
            room=f"ember-{instance.hex}",
            endpoint="ws://127.0.0.1:7880",
        )
        try:
            await runtime.start_public_search()
            core = runtime.core
            core.history.append(CompletedTurn("PRIVATE_HISTORY", "PRIVATE_REPLY", ()))
            async with AsyncExitStack() as cleanup:
                router = await livekit_runtime._start_tasks(runtime, binding, cleanup)
                assert router is not None and router.mcp is None
                capabilities = {c.id: c for c in capability_catalog(core, router)}
                if chain_build:
                    assert not router.research_available and "harness.research" not in capabilities
                else:
                    assert router.research_available and router.research_provider == "exa_mcp"
                    assert capabilities["harness.research"].provider == "exa_mcp"
                    assert capabilities["harness.research"].status == "enabled"
                    goal = f"Search: {_QUERY}"
                    denied = await router.submit_turn(goal, uuid4(), operation="research")
                    assert "couldn't queue" in denied
                    assert not await router.store.active(router.character_id)
                    with pytest.raises(TaskConflict, match="research_provider_consent_required"):
                        await router.submit(
                            "fixture", goal, operation="research", research_provider="exa"
                        )
                    task = await router.submit(
                        "fixture", goal, operation="research", research_provider="exa_mcp"
                    )
                    assert (await _terminal(router, task.task_id)).state == "succeeded"
                capability = capabilities["public_search"]
                assert capability.provider == "exa_mcp" and capability.status == "enabled"
                assert capability.reason and "Exa MCP read:" in capability.reason
                text_core = runtime.new_text_core(input_ceiling=8192, recall_enabled=False)
                assert text_core.public_web is core.public_web
                assert not server.requests
                if chain_build:

                    async def idle_conversation() -> tuple[bool, bool]:
                        return True, True

                    router.task_admission = idle_conversation
                    core.model = Model(
                        (_call(SEARCH, "query", _QUERY),),
                        (_call(BUILD, "goal", "Use a blue palette."),),
                    )
                    turn = await core.prepare(f"Search the web for {_QUERY} and build a demo")
                    reply = "".join([event.text async for event in core.stream(turn)])
                    assert "queued" in reply and _URL in reply
                    assert server.requests[-1]["params"] == {
                        "name": "web_search_exa",
                        "arguments": {"query": _QUERY, "numResults": 1},
                    }
                    async with asyncio.timeout(3):
                        while True:
                            status = await router.status(builds[0][1].task_id) if builds else None
                            if status and status.receipt:
                                break
                            await asyncio.sleep(0.01)
                    (build, _) = builds[0]
                    assert build.public_sources is None
                    assert (
                        "Use a blue palette" in build.goal
                        and "Use strong contrast" not in build.goal
                    )
                    assert "PRIVATE_" not in build.goal
                    receipt = TaskResult.model_validate_json(status.receipt).receipt
                    sources = receipt.data["public_sources"]
                    assert isinstance(sources, list) and len(sources) == 1
                    source = sources[0]
                    assert isinstance(source, dict)
                    assert source["url"] == _URL and source["provider"] == "exa_mcp"
                    assert source["source_id"]
                    assert len(server.requests) == 3
                    return
                model = SummaryModel('{"answer":"Public excerpt.","sources":[1]}')
                core.model = model
                for denied in (f"Exa read: {_URL}", f"Parallel read: {_URL}"):
                    assert "No other provider" in (await core.prepare(denied)).local_reply
                turn = await core.prepare(f"Exa MCP read: {_URL}")
                assert turn.web_evidence and turn.web_evidence.provider == "exa_mcp"
                assert "PRIVATE_" not in turn.request.model_dump_json()
                assert server.requests[-1]["params"] == {
                    "name": "web_fetch_exa",
                    "arguments": {"urls": [_URL], "maxCharacters": 2600},
                }
                text = "".join([event.text async for event in core.stream(turn)])
                assert _URL in text and "Public excerpt." in text
        finally:
            await runtime.aclose()
            runtime.close_workers()

    asyncio.run(run())


def test_research_broker_grants_only_exact_public_inputs_and_same_task_sources() -> None:
    search = Server(_tool(_SEARCH_TEXT + "\nSee https://example.net/unlisted."))
    page = Server(_tool(_PAGE_TEXT))

    def provider(request: httpx.Request) -> httpx.Response:
        return (search if len(search.requests) < 3 else page)(request)

    async def run() -> None:
        broker = research.ResearchBroker(ExaMCP(transport=httpx.MockTransport(provider)), "exa_mcp")
        task = TaskInput(
            idempotency_key="fixture",
            text=f"Read public guidance.\nSearch: {_QUERY}\nSearch: private notes",
            operation="research",
            research_provider="exa_mcp",
        )
        broker.begin("fixture", time() + 5, task.text)
        key = next(iter(broker.environment().values()))
        async with (
            broker.app.router.lifespan_context(broker.app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=broker.authenticate),
                base_url="http://127.0.0.1:1234",
            ) as http,
        ):

            def grant(name: str, **arguments: str) -> ToolCall:
                (call,) = broker.grant((ToolCall(call_id=uuid4(), name=name, arguments=arguments),))
                return call

            async def call(approved: ToolCall) -> dict[str, JsonValue]:
                response = await http.post(
                    "/mcp",
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Authorization": f"Bearer {key}",
                        "MCP-Protocol-Version": "2025-11-25",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": uuid4().hex,
                        "method": "tools/call",
                        "params": {
                            "name": approved.name.removeprefix("emberweb_"),
                            "arguments": approved.arguments,
                            "_meta": {"ai.opencode/sessionID": "fixture"},
                        },
                    },
                )
                return response.json()["result"]

            with pytest.raises(GatewayError, match="research_input_denied"):
                grant(research.SEARCH, query=_QUERY + " rewritten")
            with pytest.raises(GatewayError, match="research_input_denied"):
                grant(research.PAGE, url=_URL)
            private = await call(grant(research.SEARCH, query="private notes"))
            assert "input_rejected" in json.dumps(private) and not search.requests
            approved = grant(research.SEARCH, query=_QUERY)
            assert not (await call(approved))["isError"]
            assert (await call(approved))["isError"]  # permit cannot be replayed
            with pytest.raises(GatewayError, match="research_input_denied"):
                grant(research.PAGE, url="https://example.net/unlisted")
            assert not (await call(grant(research.PAGE, url=_URL)))["isError"]
            assert search.requests[-1]["params"] == {
                "name": "web_search_exa",
                "arguments": {"query": _QUERY, "numResults": 1},
            }
            assert page.requests[-1]["params"] == {
                "name": "web_fetch_exa",
                "arguments": {"urls": [_URL], "maxCharacters": 2600},
            }
            _, sources = broker.result("Public guidance [S1] [S2].")
            assert [(row["provider"], row["operation"], row["url"]) for row in sources] == [
                ("exa_mcp", "search", _URL),
                ("exa_mcp", "read", _URL),
            ]
            broker.fence()
            await broker.drain()
            broker.begin("next-task", time() + 5, task.text)
            with pytest.raises(GatewayError, match="research_input_denied"):
                grant(research.PAGE, url=_URL)
            broker.fence()
            await broker.drain()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("phrase", "operation", "arg", "proposal", "allowed"),
    [
        (f"Could you explain this page at <{_URL}>?", PAGE, "url", _URL, True),
        (f"Give me a summary of the article at {_URL}", PAGE, "url", _URL, True),
        (f"Give me a summary of {_URL} and https://example.net", PAGE, "url", _URL, False),
        (f"Explain {_URL}", PAGE, "url", "https://example.net/rewritten", False),
        (f"Search the web for {_QUERY}", SEARCH, "query", "public map designs", False),
    ],
)
def test_natural_language_requires_literal_grant(
    tmp_path: Path, phrase: str, operation: str, arg: str, proposal: str, allowed: bool
) -> None:
    from ember_memory_engine import CanonicalEngine

    from .test_tool_workflow import Harness, Model, _call

    server = Server(_tool(_PAGE_TEXT))
    model = Model((_call(operation, arg, proposal),), "Use strong contrast.")

    async def run() -> None:
        with CanonicalEngine(tmp_path) as engine:
            fixture = Harness(engine, model)
            fixture.core.search_provider = "exa_mcp"
            fixture.core.public_web = server.adapter
            try:
                reply = await fixture.turn(phrase)
                assert not fixture.plans
                if allowed:
                    assert len(server.requests) == 3 and _URL in reply
                    assert server.requests[-1]["params"] == {
                        "name": "web_fetch_exa",
                        "arguments": {"urls": [_URL], "maxCharacters": 2600},
                    }
                else:
                    assert not server.requests and len(model.requests) == 1
                    command = "read" if operation == PAGE else "search"
                    assert f"Exa MCP {command}: {proposal}" in reply
            finally:
                await fixture.memory.aclose()

    asyncio.run(run())
