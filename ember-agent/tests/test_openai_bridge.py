"""Task-bound OpenAI protocol mapping preserves sole-gateway ownership and tool history."""

import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import (
    GatewayError,
    GatewayRequest,
    PromptMessage,
    PromptToolCall,
    ToolCall,
    ToolDefinition,
    ToolFunction,
)
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.ollama import OllamaUpstream
from ember_agent.inference.openai_bridge import OpenAIModelBridge
from ember_agent.inference.service import create_app
from ember_agent.inference.strata import StrataUpstream
from ember_agent.inference.upstream import (
    NativeFrame,
    NativeFunction,
    NativeMessage,
    NativeTool,
    Upstream,
)
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.runtime.coding_api import coding_routes
from ember_agent.runtime.coding_memory import coding_memory_tools
from ember_agent.runtime.config import (
    ConfigurationError,
    DeploymentConfig,
    InferenceSettings,
    StrataSettings,
    load_config,
)
from ember_agent.runtime.memory_tools import (
    MemoryTools,
    RecallClient,
    RecallHit,
    RecallResult,
    _coding_rank,
)
from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.errors import AccessDeniedError
from ember_memory_abi.interfaces import MemoryResult, MutationReceipt
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.library import LibraryService
from ember_memory_engine.read_budget import ReadBudget

_EXAMPLE = Path(__file__).parents[1] / "config/deployment.example.toml"


def _config(tmp_path: Path) -> DeploymentConfig:
    path = tmp_path / "deployment.toml"
    path.write_text(
        _EXAMPLE.read_text()
        .replace('tag = ""', 'tag = "synthetic:27b"')
        .replace('expected_digest = ""', 'expected_digest = "' + "a" * 64 + '"')
    )
    return load_config(path, require_model_pin=True)


def _history() -> tuple[PromptMessage, ...]:
    return (
        PromptMessage(role="user", content="Find a synthetic fact."),
        PromptMessage(
            role="assistant",
            content="",
            tool_calls=(
                PromptToolCall(
                    call_id="call_synthetic", name="lookup", arguments={"query": "example"}
                ),
            ),
        ),
        PromptMessage(role="tool", content="Synthetic source", tool_call_id="call_synthetic"),
    )


def _request(config: DeploymentConfig, messages: tuple[PromptMessage, ...]) -> GatewayRequest:
    return GatewayRequest(
        request_id=uuid4(),
        context_id=uuid4(),
        model=config.model_tag,
        workload="P2",
        messages=messages,
        deadline_at=(datetime.now(UTC) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
    )


def test_legacy_prompt_json_remains_unchanged() -> None:
    assert PromptMessage(role="user", content="Synthetic").model_dump() == {
        "role": "user",
        "content": "Synthetic",
    }


@pytest.mark.parametrize(
    "fault", ["unknown", "duplicate_result", "duplicate_call", "unfinished", "interleaved"]
)
def test_complete_tool_history_requires_exact_call_result_pairing(
    tmp_path: Path, fault: str
) -> None:
    messages = _history()
    if fault == "unknown":
        messages = (
            *messages[:-1],
            PromptMessage(role="tool", content="unrelated", tool_call_id="other"),
        )
    elif fault == "duplicate_result":
        messages += (messages[-1],)
    elif fault == "duplicate_call":
        messages += messages[1:]
    elif fault == "unfinished":
        messages = messages[:-1]
    else:
        messages = (*messages[:-1], PromptMessage(role="user", content="interleaved"), messages[-1])
    with pytest.raises(ValidationError):
        _request(_config(tmp_path), messages)


def test_ollama_preserves_call_ids_and_correlated_native_tool_name(tmp_path: Path) -> None:
    async def run() -> None:
        captured = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, content=b'{"done":true}\n')

        config = _config(tmp_path)
        upstream = OllamaUpstream(
            config,
            httpx.AsyncClient(
                base_url="http://127.0.0.1:11434",
                transport=httpx.MockTransport(respond),
            ),
        )
        assert [frame async for frame in upstream.generate(_request(config, _history()))][-1].done
        messages = captured[0]["messages"]
        assert messages[1]["tool_calls"][0] == {
            "id": "call_synthetic",
            "function": {"index": 0, "name": "lookup", "arguments": {"query": "example"}},
        }
        assert messages[2]["tool_call_id"] == "call_synthetic"
        assert messages[2]["tool_name"] == "lookup"
        await upstream.aclose()

    asyncio.run(run())


class _Upstream:
    def __init__(self) -> None:
        self.requests: list[GatewayRequest] = []
        self.frames = (
            NativeFrame(message=NativeMessage(content="Synthetic answer")),
            NativeFrame(done=True, prompt_eval_count=12, eval_count=3),
        )
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def verify(self, *, loaded: bool = False) -> None:
        pass

    async def generate(self, request: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        for frame in self.frames:
            yield frame

    async def aclose(self) -> None:
        pass


def _bridge(
    config: DeploymentConfig, upstream: Upstream
) -> tuple[OpenAIModelBridge, P0Gateway, HttpGatewayClient]:
    gateway = P0Gateway(config, upstream)
    client = HttpGatewayClient(
        config.services.gateway.endpoint,
        "agent",
        client=httpx.AsyncClient(
            base_url=config.services.gateway.endpoint,
            transport=httpx.ASGITransport(
                app=create_app(gateway, "foreground", agent_token="agent")
            ),
        ),
    )
    bridge = OpenAIModelBridge(
        client,
        config,
        context_id=uuid4(),
        deadline_at=(datetime.now(UTC) + timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
    )
    return bridge, gateway, client


def _body(config: DeploymentConfig) -> dict[str, object]:
    return {
        "model": config.model_tag,
        "messages": [{"role": "user", "content": "Synthetic question"}],
        "max_tokens": 100,
        "temperature": 0.2,
    }


def test_monitor_failures_cannot_drop_transport_end_or_leave_bridge_busy(tmp_path: Path) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        bridge, gateway, client = _bridge(config, _Upstream())

        def broken(_: object) -> None:
            raise RuntimeError("SYNTHETIC_OBSERVER_ERROR")

        bridge.observe_request = broken
        client.observe_event = broken
        try:
            for _ in range(2):
                result = await bridge.complete(_body(config))
                assert result["choices"][0]["message"]["content"] == "Synthetic answer"
                assert result["usage"]["completion_tokens"] == 3
                assert not bridge.running
            assert bridge.observation_failures == client.observation_failures == 2
        finally:
            await client.aclose()
            await gateway.aclose()

    asyncio.run(run())


def test_coding_api_shares_gateway_during_realtime_and_streams_tools(tmp_path: Path) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        config = config.model_copy(
            update={"inference": config.inference.model_copy(update={"coding_api": True})}
        )
        upstream = _Upstream()
        upstream.frames = (
            NativeFrame(
                message=NativeMessage(
                    tool_calls=[
                        NativeTool(
                            index=0,
                            function=NativeFunction(name="lookup", arguments='{"query":"x"}'),
                        )
                    ]
                )
            ),
            NativeFrame(done=True, prompt_eval_count=12, eval_count=3),
        )
        _, gateway, client = _bridge(config, upstream)
        app = FastAPI()
        app.include_router(coding_routes(config, client, "separate-key"))
        try:
            await gateway.session(uuid4(), connected=True)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as lan:
                assert (await lan.get("/coding/v1/models")).status_code == 401
                lan.headers["Authorization"] = "Bearer separate-key"
                assert (await lan.get("/coding/v1/models")).json()["data"][0][
                    "id"
                ] == config.model_tag
                body = _body(config)
                body.update(
                    stream=True,
                    stream_options={"include_usage": True},
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "lookup",
                                "strict": False,
                                "parameters": {
                                    "type": "object",
                                    "properties": {
                                        "sessionID": {"type": "string", "pattern": "^ses"}
                                    },
                                },
                            },
                        }
                    ],
                )
                response = await lan.post("/coding/v1/chat/completions", json=body)
                assert response.status_code == 200
                frames = [
                    line[6:] for line in response.text.splitlines() if line.startswith("data: ")
                ]
                assert frames[-1] == "[DONE]"
                chunks = [json.loads(frame) for frame in frames[:-1]]
                call = chunks[1]["choices"][0]["delta"]["tool_calls"][0]
                assert call["function"]["name"] == "lookup"
                assert json.loads(call["function"]["arguments"]) == {"query": "x"}
                assert chunks[2]["choices"][0]["finish_reason"] == "tool_calls"
                assert chunks[3]["usage"]["total_tokens"] == 15
                tools = body["tools"]
                tools[0]["function"]["parameters"]["properties"]["sessionID"]["pattern"] = "^(a+)+$"
                rejected = await lan.post("/coding/v1/chat/completions", json=body)
                assert rejected.status_code == 400
                assert rejected.json()["error"]["code"] == "unsupported_model_schema"
                assert [req.workload for req in upstream.requests] == ["P5"]
        finally:
            await client.aclose()
            await gateway.aclose()

    asyncio.run(run())


def test_bridge_uses_real_gateway_p2_admission_and_server_owned_identity(tmp_path: Path) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        metadata = bridge.models()["data"][0]
        assert metadata["context_length"] == config.context_tokens
        assert metadata["max_input_tokens"] == min(config.max_input_tokens, 16384)
        answer = await bridge.complete(json.dumps(_body(config)).encode())
        assert answer["choices"][0]["message"]["content"] == "Synthetic answer"
        assert answer["usage"] == {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
        assert len(upstream.requests) == 1
        request = upstream.requests[0]
        assert request.workload == "P2" and request.context_id == bridge.context_id
        assert request.model == config.model_tag and not request.thinking
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("thinking_options", "effort"),
    [
        ({"reasoning_effort": "none"}, None),
        ({"reasoning_effort": "high"}, "high"),
        ({"enable_thinking": False}, None),
        ({"enable_thinking": True, "thinking_budget": 24576}, "high"),
    ],
)
def test_strata_coding_context_uses_native_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
    thinking_options: dict[str, object],
    effort: str | None,
) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        settings = StrataSettings(
            endpoint="http://127.0.0.1:18110",
            python_executable=sys.executable,
            server_script=str(tmp_path / "server.py"),
            runner_config=str(tmp_path / "runner.json"),
            runner_config_sha256="a" * 64,
        )
        config = config.model_copy(
            update={
                "context_tokens": 131072,
                "max_input_tokens": 126976,
                "output_reserve_tokens": 4096,
                "inference": InferenceSettings(backend="strata", strata=settings, coding_api=True),
            }
        )
        instance = "b" * 32
        monkeypatch.setenv("EMBER_STRATA_INSTANCE_ID", instance)
        monkeypatch.setenv("EMBER_STRATA_API_KEY", "synthetic")
        content = "long fixture line\n" * 4800
        messages = [{"role": "user", "content": content}, {"role": "user", "content": content}]
        for index in range(32):
            call_id = f"call_fixture_{index}"
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": "lookup", "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "tool", "content": "Synthetic result", "tool_call_id": call_id},
                ]
            )
        assert len(messages) == 66
        assert len(json.dumps(messages).encode()) > config.max_input_tokens
        completion_calls = 0

        def json_response(status: int, body: dict[str, object]) -> httpx.Response:
            return httpx.Response(status, stream=httpx.ByteStream(json.dumps(body).encode()))

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal completion_calls
            if request.url.path == "/v1/status":
                return json_response(
                    200,
                    {
                        "service": "strata",
                        "loaded": True,
                        "model": config.model_tag,
                        "context": {"native": 131072},
                    },
                )
            if request.url.path == "/v1/telemetry":
                return json_response(200, {"instance": {"id": instance}})
            assert request.url.path == "/v1/chat/completions"
            body = json.loads(request.content)
            assert body["messages"] == messages
            assert body["chat_template_kwargs"] == {"enable_thinking": effort is not None}
            assert body.get("reasoning_effort") == effort
            assert "enable_thinking" not in body and "thinking_budget" not in body
            assert body["ember_coding_input_limit"] == 126976
            assert body["max_completion_tokens"] == 4096
            completion_calls += 1
            if completion_calls == 1:
                return json_response(
                    400,
                    {
                        "error": {
                            "type": "invalid_request_error",
                            "message": "ember_coding_context_length_exceeded",
                        }
                    },
                )
            base = {"id": "fixture", "object": "chat.completion.chunk", "model": config.model_tag}
            chunks = [
                {
                    "choices": [
                        {"index": 0, "delta": {"reasoning_content": "Trace"}, "finish_reason": None}
                    ]
                },
                {"choices": [{"index": 0, "delta": {"content": "Yes"}, "finish_reason": None}]},
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 126976,
                        "completion_tokens": 1,
                        "total_tokens": 126977,
                    },
                },
            ]
            text = "".join(f"data: {json.dumps(base | chunk)}\n\n" for chunk in chunks)
            return httpx.Response(
                200,
                stream=httpx.ByteStream((text + "data: [DONE]\n\n").encode()),
                headers={"Content-Type": "text/event-stream"},
            )

        upstream = StrataUpstream(
            config,
            httpx.AsyncClient(base_url=settings.endpoint, transport=httpx.MockTransport(respond)),
        )
        bridge, gateway, client = _bridge(config, upstream)
        app = FastAPI()
        app.include_router(coding_routes(config, client, "separate-key"))
        body = {
            "model": config.model_tag,
            "messages": [
                {**message, "reasoning_content": "DISCARDED_PREVIOUS_TRACE"}
                if message["role"] == "assistant"
                else message
                for message in messages
            ],
            **thinking_options,
            "stream": stream,
            "stream_options": {"include_usage": True},
        }
        route = "/coding/v1/chat/completions"
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1",
                headers={"Authorization": "Bearer separate-key"},
            ) as lan:
                for options in (
                    {"enable_thinking": True, "thinking_budget": 4096},
                    {"enable_thinking": False, "thinking_budget": 24576},
                    {"thinking_budget": 24576},
                    {"enable_thinking": True, "reasoning_effort": "high"},
                ):
                    rejected = await lan.post(
                        route, json={"model": config.model_tag, "messages": messages, **options}
                    )
                    assert rejected.status_code == 400
                    assert rejected.json()["error"]["code"] == "unsupported_model_options"
                assert completion_calls == 0

                refused = await lan.post(route, json=body)
                assert refused.status_code == (200 if stream else 400)
                assert "context_length_exceeded" in refused.text
                assert "ember_coding_context_length_exceeded" not in refused.text
                assert not gateway.uncertain

                accepted = await lan.post(route, json=body)
                assert accepted.status_code == 200
                if stream:
                    assert '"prompt_tokens": 126976' in accepted.text
                    assert "data: [DONE]" in accepted.text
                    assert ('"reasoning_content": "Trace"' in accepted.text) == (effort == "high")
                else:
                    assert accepted.json()["usage"]["prompt_tokens"] == 126976
                    message = accepted.json()["choices"][0]["message"]
                    assert message["content"] == "Yes"
                    assert message.get("reasoning_content") == (
                        "Trace" if effort == "high" else None
                    )
                assert "DISCARDED_PREVIOUS_TRACE" not in accepted.text
                assert completion_calls == 2

            legacy = {"model": config.model_tag, "messages": messages}
            with pytest.raises(GatewayError, match="invalid_openai_request"):
                await bridge.complete(legacy)
            with pytest.raises(GatewayError, match="prompt_overflow"):
                await bridge.complete({**legacy, "messages": messages[:2]})
            assert completion_calls == 2
        finally:
            await client.aclose()
            await gateway.aclose()

    asyncio.run(run())


def test_host_policy_narrows_tools_after_full_validation_without_changing_scope(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        config, upstream = _config(tmp_path), _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        body = _body(config)
        body["tools"] = [
            {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}
        ]
        original = json.dumps(body)
        seen = []

        def policy(tools: tuple[ToolDefinition, ...]) -> tuple[ToolDefinition, ...]:
            seen.extend(tool.function.name for tool in tools)
            return ()

        try:
            await bridge.complete(body, tool_policy=policy)
            assert seen == ["lookup"] and json.dumps(body) == original
            request = upstream.requests[0]
            assert request.tools == () and request.context_id == bridge.context_id
            assert request.workload == "P2" and request.model == config.model_tag
            assert request.messages == (PromptMessage(role="user", content="Synthetic question"),)
            assert request.max_output_tokens == 100 and request.request_id != request.context_id
        finally:
            await gateway.aclose()
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "definition",
    [
        {"name": "pruned", "parameters": {"type": "invented"}},
        {"name": "pruned", "parameters": {}},
        {"name": "pruned", "parameters": {"type": "string"}},
        {"name": "pruned", "parameters": {"type": "string", "pattern": "^(a+)+$"}},
        {"name": "pruned", "parameters": {}, "unknown_field": True},
    ],
)
def test_host_policy_cannot_hide_malformed_pruned_definitions(
    tmp_path: Path, definition: dict
) -> None:
    async def run() -> None:
        config, upstream = _config(tmp_path), _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        body = _body(config)
        body["tools"] = [{"type": "function", "function": definition}]
        invoked = False

        def policy(tools: tuple[ToolDefinition, ...]) -> tuple[ToolDefinition, ...]:
            nonlocal invoked
            invoked = True
            return ()

        try:
            with pytest.raises(GatewayError):
                await bridge.complete(body, tool_policy=policy)
            assert not invoked and not upstream.requests
        finally:
            await gateway.aclose()
            await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["added_name", "duplicate", "schema"])
def test_projected_host_tools_are_validated_again_before_dispatch(
    tmp_path: Path, fault: str
) -> None:
    async def run() -> None:
        config, upstream = _config(tmp_path), _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        body = _body(config)
        body["tools"] = [
            {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}
        ]

        def policy(tools: tuple[ToolDefinition, ...]) -> tuple[ToolDefinition, ...]:
            if fault == "duplicate":
                return (tools[0], tools[0])
            return (
                ToolDefinition(
                    function=ToolFunction(
                        name="other" if fault == "added_name" else "lookup",
                        parameters={"type": "invented"}
                        if fault == "schema"
                        else {"type": "object"},
                    )
                ),
            )

        try:
            with pytest.raises(GatewayError):
                await bridge.complete(body, tool_policy=policy)
            assert not upstream.requests
        finally:
            await gateway.aclose()
            await client.aclose()

    asyncio.run(run())


def test_complete_tool_exchange_preserves_ids_through_harness_gateway_roundtrip(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        upstream.frames = (
            NativeFrame(
                message=NativeMessage(
                    tool_calls=[
                        NativeTool(
                            function=NativeFunction(name="lookup", arguments={"query": "example"})
                        )
                    ]
                )
            ),
            NativeFrame(done=True),
        )
        bridge, gateway, client = _bridge(config, upstream)
        body = _body(config)
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ]
        body["tool_choice"] = "auto"
        response = await bridge.complete(json.dumps(body).encode())
        assistant = response["choices"][0]["message"]
        call = assistant["tool_calls"][0]
        assert response["choices"][0]["finish_reason"] == "tool_calls"
        assert json.loads(call["function"]["arguments"]) == {"query": "example"}
        body["messages"] += [
            assistant,
            {"role": "tool", "tool_call_id": call["id"], "content": "Synthetic source"},
        ]
        upstream.frames = (
            NativeFrame(message=NativeMessage(content="Completed")),
            NativeFrame(done=True),
        )
        final = await bridge.complete(json.dumps(body).encode())
        assert final["choices"][0]["message"]["content"] == "Completed"
        historical = upstream.requests[-1].messages[-2:]
        assert historical[0].tool_calls[0].call_id == call["id"]
        assert historical[1].tool_call_id == call["id"]
        assert upstream.requests[0].request_id != upstream.requests[1].request_id
        assert upstream.requests[0].context_id == upstream.requests[1].context_id
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    [
        "model",
        "workload",
        "context_id",
        "request_id",
        "thinking",
        "backend",
        "output_budget",
        "prompt_budget",
        "incomplete_arguments",
        "invalid_json",
        "unknown_field",
        "temperature",
    ],
)
def test_unsupported_or_escalating_requests_fail_before_generation(
    tmp_path: Path, fault: str
) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        body = _body(config)
        if fault == "model":
            body["model"] = "unapproved"
        elif fault == "output_budget":
            body["max_tokens"] = 1193
        elif fault == "prompt_budget":
            body["messages"] = [{"role": "user", "content": "x" * 17000}]
        elif fault == "incomplete_arguments":
            body["messages"] = [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call_a", "function": {"name": "lookup", "arguments": "{"}}
                    ],
                }
            ]
        elif fault == "temperature":
            body["temperature"] = 1.0
        else:
            body[fault] = "untrusted"
        wire = b"{invalid" if fault == "invalid_json" else json.dumps(body).encode()
        with pytest.raises(GatewayError):
            await bridge.complete(wire)
        assert not upstream.requests
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


def test_structured_response_is_not_published_after_schema_error_or_eof(tmp_path: Path) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        body = _body(config)
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": {"type": "object", "required": ["ready"]}},
        }
        upstream.frames = (NativeFrame(message=NativeMessage(content="{}")), NativeFrame(done=True))
        with pytest.raises(GatewayError, match="structured_schema_mismatch"):
            await bridge.complete(json.dumps(body).encode())
        assert not bridge.running
        upstream.frames = (NativeFrame(message=NativeMessage(content="partial")),)
        with pytest.raises(GatewayError):
            await bridge.complete(json.dumps(_body(config)).encode())
        assert gateway.uncertain and not bridge.running
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


def test_json_object_format_rejects_valid_non_object_json(tmp_path: Path) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        upstream.frames = (NativeFrame(message=NativeMessage(content="[]")), NativeFrame(done=True))
        bridge, gateway, client = _bridge(config, upstream)
        body = _body(config)
        body["response_format"] = {"type": "json_object"}
        with pytest.raises(GatewayError, match="structured_schema_mismatch"):
            await bridge.complete(json.dumps(body).encode())
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["expired", "oversized"])
def test_task_deadline_and_body_size_fail_before_admission(tmp_path: Path, fault: str) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        if fault == "expired":
            bridge = OpenAIModelBridge(
                client, config, context_id=uuid4(), deadline_at="2000-01-01T00:00:00.000000Z"
            )
        body = (
            b" " * (1024 * 1024 + 1) if fault == "oversized" else json.dumps(_body(config)).encode()
        )
        with pytest.raises(
            GatewayError, match="request_too_large" if fault == "oversized" else "deadline_exceeded"
        ):
            await bridge.complete(body)
        assert not upstream.requests
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("location", ["tools", "response_format"])
@pytest.mark.parametrize(
    "keyword, value",
    [
        ("pattern", "^(a+)+$"),
        ("patternProperties", {"^(a+)+$": {"type": "string"}}),
        ("$ref", "https://untrusted.invalid/schema"),
        ("allOf", [{"type": "string"}]),
        ("propertyNames", {"pattern": "^(a+)+$"}),
    ],
)
def test_external_schema_cpu_amplification_is_rejected_before_dispatch(
    tmp_path: Path, location: str, keyword: str, value: object
) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        body = _body(config)
        schema = {"type": "object", "properties": {"query": {"type": "string", keyword: value}}}
        if location == "tools":
            body["tools"] = [
                {"type": "function", "function": {"name": "lookup", "parameters": schema}}
            ]
        else:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema},
            }
        with pytest.raises(GatewayError, match="unsupported_model_schema"):
            await bridge.complete(json.dumps(body).encode())
        assert not upstream.requests and bridge.calls == 0
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


def test_schema_property_named_pattern_and_literal_data_remain_supported(tmp_path: Path) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        upstream.frames = (
            NativeFrame(message=NativeMessage(content='{"pattern":"literal"}')),
            NativeFrame(done=True),
        )
        bridge, gateway, client = _bridge(config, upstream)
        body = _body(config)
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "answer",
                "schema": {
                    "type": "object",
                    "properties": {"pattern": {"type": "string", "default": "^(a+)+$"}},
                },
            },
        }
        response = await bridge.complete(json.dumps(body).encode())
        assert response["choices"][0]["message"]["content"] == '{"pattern":"literal"}'
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


def test_bridge_limits_each_task_to_twelve_model_calls(tmp_path: Path) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        for _ in range(12):
            await bridge.complete(json.dumps(_body(config)).encode())
        with pytest.raises(GatewayError, match="model_call_limit"):
            await bridge.complete(json.dumps(_body(config)).encode())
        assert len(upstream.requests) == 12
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


def test_bridge_cancellation_keeps_existing_runner_ownership(tmp_path: Path) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        upstream = _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        upstream.release.clear()
        task = asyncio.create_task(bridge.complete(json.dumps(_body(config)).encode()))
        await upstream.started.wait()
        owned = gateway.active
        assert owned is not None and owned.task is not None
        with pytest.raises(GatewayError, match="model_bridge_busy"):
            await bridge.complete(json.dumps(_body(config)).encode())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not bridge.running and gateway.active is owned and not owned.task.done()
        upstream.release.set()
        await owned.task
        assert not gateway.uncertain
        await gateway.aclose()
        await client.aclose()

    asyncio.run(run())


RECALL = "ember_recall_memory"
SAVE = "ember_save_memory"


def test_coding_memory_and_mcp_are_exclusive(tmp_path: Path) -> None:
    _config(tmp_path)
    path = tmp_path / "deployment.toml"
    path.write_text(
        path.read_text() + "\n[inference]\ncoding_api = true\ncoding_memory = true\n[memory_mcp]\n"
    )
    with pytest.raises(ConfigurationError, match="Choose coding memory or memory MCP"):
        load_config(path)


def _call(index: int, name: str, **arguments: str) -> NativeTool:
    return NativeTool(
        index=index,
        function=NativeFunction(name=name, arguments=json.dumps(arguments)),
    )


@pytest.mark.parametrize("stream", [False, True])
def test_coding_memory_uses_gateway_and_fixed_library(tmp_path: Path, stream: bool) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character().store_id
            other = engine.create_character().store_id
            opencode = LibraryService(engine, authority=MutationAuthority(mode="opencode_notes"))
            opencode.save_opencode_note(character, "Cobalt fixture uses green build flags.")
            opencode.save_opencode_note(other, "Cobalt unrelated private fact.")
            coding = LibraryService(engine, authority=MutationAuthority(mode="coding_notes"))
            with pytest.raises(AccessDeniedError):
                opencode.save_coding_note(character, "Wrong authority.")
            with pytest.raises(AccessDeniedError):
                coding.save_opencode_note(character, "Wrong transport.")
            store = engine.character(character)
            client = InProcessAsyncMemoryClient(store)

            def deep_client(deadline: float) -> InProcessAsyncMemoryClient:
                budget = ReadBudget(deadline=deadline)
                return InProcessAsyncMemoryClient(
                    store, deep_sources=DeepSources(engine, budget), read_budget=budget
                )

            deep = DeepRecallService(character, deep_client, validation=client)

            async def writer(note: str) -> MutationReceipt:
                return await asyncio.to_thread(coding.save_coding_note, character, note)

            service = MemoryTools(client, deep, "Europe/London", writer, library="Coding")
            hosted = coding_memory_tools(service)
            read_only = coding_memory_tools(MemoryTools(client, deep, "Europe/London", None))
            assert [tool.function.name for tool in read_only.definitions] == [RECALL]
            saved_note = "Use purple flags for the next fixture."

            class Scripted(_Upstream):
                async def generate(
                    self, request: GatewayRequest
                ) -> AsyncGenerator[NativeFrame, None]:
                    self.requests.append(request)
                    match len(self.requests):
                        case 1:
                            yield NativeFrame(
                                message=NativeMessage(
                                    tool_calls=[_call(0, RECALL, query="Cobalt fixture")]
                                )
                            )
                        case 2:
                            text = request.messages[-1].content
                            assert len(text.encode()) < 126976
                            assert "green build flags" in text and "private fact" not in text
                            assert json.loads(text)["memories"][0]["claim"]["confidence"] == 0.5
                            yield NativeFrame(
                                message=NativeMessage(tool_calls=[_call(0, SAVE, note=saved_note)])
                            )
                        case 3:
                            assert json.loads(request.messages[-1].content)["library"] == "Coding"
                            assert SAVE not in {tool.function.name for tool in request.tools}
                            yield NativeFrame(message=NativeMessage(content="Saved the fixture."))
                        case _:
                            raise AssertionError("Too many model rounds")
                    yield NativeFrame(done=True, prompt_eval_count=50, eval_count=3)

            config = _config(tmp_path)
            config = config.model_copy(
                update={
                    "inference": config.inference.model_copy(update={"coding_api": True}),
                    "context_tokens": 131072,
                    "max_input_tokens": 126976,
                    "output_reserve_tokens": 4096,
                }
            )
            native = Scripted()
            _, gateway, transport = _bridge(config, native)
            app = FastAPI()
            app.include_router(coding_routes(config, transport, "fixture-key", hosted_tools=hosted))
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://localhost"
                ) as http:
                    body = _body(config)
                    body.update(stream=stream, stream_options={"include_usage": True})
                    url = "/coding/v1/chat/completions"
                    assert (await http.post(url, json=body)).status_code == 401
                    http.headers["Authorization"] = "Bearer fixture-key"
                    response = await http.post(url, json=body)
                    assert response.status_code == 200
                    assert "Saved the fixture." in response.text
                    assert RECALL not in response.text and SAVE not in response.text
                    if stream:
                        assert "data: [DONE]" in response.text
                    else:
                        assert response.json()["usage"]["completion_tokens"] == 9
                assert [request.workload for request in native.requests] == ["P5"] * 3
                assert len({request.request_id for request in native.requests}) == 3
                coding_id = coding.ensure_coding_library()
                assert coding_id != opencode.ensure_opencode_library()
                assert coding.save_coding_note(character, saved_note).outcome == "already_applied"
                recall = await hosted.invoke(
                    ToolCall(call_id=uuid4(), name=RECALL, arguments={"query": "purple flags"}),
                    8192,
                )
                assert saved_note in recall
            finally:
                await transport.aclose()
                await gateway.aclose()
                await deep.aclose()
                await client.aclose()

    asyncio.run(run())


def test_coding_memory_keeps_client_tools_and_rejects_reserved_names(tmp_path: Path) -> None:
    async def run() -> None:
        config = _config(tmp_path)
        config = config.model_copy(
            update={"inference": config.inference.model_copy(update={"coding_api": True})}
        )
        native = _Upstream()
        native.frames = (
            NativeFrame(
                message=NativeMessage(
                    tool_calls=[
                        _call(0, SAVE, note="Do not execute this mixed save."),
                        _call(1, "client_read", path="example.py"),
                    ]
                )
            ),
            NativeFrame(done=True, prompt_eval_count=50, eval_count=3),
        )
        _, gateway, transport = _bridge(config, native)
        calls: list[str] = []

        async def writer(note: str) -> MutationReceipt:
            calls.append(note)
            raise AssertionError("A mixed tool call must not save a note.")

        # Mixed client batches never reach either memory operation.
        service = MemoryTools(
            Mock(spec=RecallClient),
            Mock(spec=DeepRecallService),
            "Europe/London",
            writer,
            library="Coding",
        )
        app = FastAPI()
        app.include_router(
            coding_routes(
                config, transport, "fixture-key", hosted_tools=coding_memory_tools(service)
            )
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer fixture-key"},
            ) as http:
                tool = {
                    "type": "function",
                    "function": {
                        "name": SAVE,
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
                body = _body(config) | {"tools": [tool]}
                path = "/coding/v1/chat/completions"
                bad = await http.post(path, json=body)
                assert bad.json()["error"]["code"] == "reserved_tool_name"
                tool["function"]["name"] = "client_read"
                response = await http.post(path, json=body)
                assert response.status_code == 200, response.text
                message = response.json()["choices"][0]["message"]
                assert [item["function"]["name"] for item in message["tool_calls"]] == [
                    "client_read"
                ]
                assert calls == []
        finally:
            await transport.aclose()
            await gateway.aclose()

    asyncio.run(run())


def test_coding_recall_uses_available_budget_without_cutting_claims() -> None:
    async def run() -> None:
        hits = tuple(
            RecallHit(
                memory_id=uuid4(),
                revision=1,
                kind="episode",
                claim={"value": f"rank {index} " + "é" * 400},
            )
            for index in range(14)
        )
        recall = Mock(spec=MemoryTools)
        recall.write_note = None
        recall.recall = AsyncMock(
            return_value=RecallResult(memories=hits, coverage="complete", omitted_for_size=0)
        )
        hosted = coding_memory_tools(recall)
        call = ToolCall(call_id=uuid4(), name=RECALL, arguments={"query": "fixture"})
        large = await hosted.invoke(call, 20_000)
        assert 6144 < len(large.encode()) <= 20_000
        assert len(json.loads(large)["memories"]) == len(hits)
        small = await hosted.invoke(call, 1600)
        assert len(small.encode()) <= 1600
        body = json.loads(small)
        assert len(body["memories"]) == 1 and body["omitted_for_size"] == len(hits) - 1
        assert body["memories"][0]["claim"] == hits[0].claim
        recall.recall.assert_awaited_with("fixture", coding_budget=1600)

    asyncio.run(run())


def test_coding_ranking_preserves_relevance_and_uses_recency_and_confidence(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        coding = LibraryService(engine, authority=MutationAuthority(mode="coding_notes"))
        receipt = coding.save_coding_note(character, "Use fixture-mode for local rehearsals.")
        memory = engine.character(character).get(receipt.affected[0].memory_id)
        recent = timestamp_from_datetime(datetime.now(UTC))
        older = timestamp_from_datetime(datetime.now(UTC) - timedelta(days=100))

        def item(relevance: float, confidence: float, date: str) -> MemoryResult:
            return MemoryResult(
                score=relevance,
                memory=memory.model_copy(
                    update={
                        "content": memory.content.model_copy(update={"confidence": confidence}),
                        "temporal": memory.temporal.model_copy(update={"observed_at": date}),
                    }
                ),
            )

        relevant = item(1.5, 0.1, older)
        recent_confident = item(1.0, 1.0, recent)
        recent_uncertain = item(1.0, 0.2, recent)
        older_confident = item(1.0, 1.0, older)
        assert _coding_rank((older_confident, recent_uncertain, recent_confident, relevant)) == (
            relevant,
            recent_confident,
            recent_uncertain,
            older_confident,
        )
