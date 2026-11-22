"""Task-bound OpenAI protocol mapping preserves sole-gateway ownership and tool history."""

import asyncio
import base64
import json
import sys
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from pydantic import JsonValue, ValidationError

from ember_agent.character.core import ConversationTask
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import (
    GatewayError,
    GatewayRequest,
    PromptMessage,
    PromptToolCall,
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
from ember_agent.runtime.coding_api import coding_routes
from ember_agent.runtime.config import (
    DeploymentConfig,
    InferenceSettings,
    StrataSettings,
    load_config,
)

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


def _image_url(format_: str = "PNG", size: tuple[int, int] = (16, 16)) -> str:
    buffer = BytesIO()
    Image.new("RGB", size, "red").save(buffer, format=format_)
    return f"data:image/{format_.lower()};base64," + base64.b64encode(buffer.getvalue()).decode()


def _image_part(url: str | None = None) -> dict[str, JsonValue]:
    return {"type": "image_url", "image_url": {"url": url or _image_url()}}


@pytest.mark.parametrize("vision", [False, True])
def test_coding_vision_admits_only_bounded_inline_user_images(tmp_path: Path, vision: bool) -> None:
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
                "inference": InferenceSettings(
                    backend="strata", strata=settings, coding_api=True, coding_vision=vision
                )
            }
        )
        upstream = _Upstream()
        bridge, gateway, client = _bridge(config, upstream)
        app = FastAPI()
        app.include_router(coding_routes(config, client, "separate-key"))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost"
            ) as lan:
                route = "/coding/v1/chat/completions"
                headers = {"Authorization": "Bearer separate-key"}
                body = {
                    "model": config.model_tag,
                    "messages": [{"role": "user", "content": [_image_part()]}],
                }
                assert (await lan.post(route, json=body)).status_code == 401
                assert (await lan.get("/coding/v1/models")).status_code == 401
                models = (await lan.get("/coding/v1/models", headers=headers)).json()
                assert models["data"][0]["architecture"] == {
                    "input_modalities": ["text", "image"] if vision else ["text"],
                    "output_modalities": ["text"],
                }
                if not vision:
                    response = await lan.post(route, headers=headers, json=body)
                    assert response.status_code == 400
                    assert response.json()["error"]["code"] == "image_input_not_supported"
                    assert not upstream.requests
                    return
                animated = BytesIO()
                Image.new("RGB", (16, 16), "red").save(
                    animated,
                    format="PNG",
                    save_all=True,
                    append_images=[Image.new("RGB", (16, 16), "blue")],
                )
                animated_url = base64.b64encode(animated.getvalue()).decode()
                oversized = base64.b64encode(bytes(512 * 1024 + 1)).decode()
                for role, parts, code in (
                    (
                        "user",
                        [_image_part("https://example.test/image.png")],
                        "invalid_openai_request",
                    ),
                    ("user", [_image_part("file:///tmp/fixture.png")], "invalid_openai_request"),
                    ("user", [_image_part(r"C:\fixture.png")], "invalid_openai_request"),
                    ("user", [_image_part(_image_url("GIF"))], "invalid_openai_request"),
                    ("user", [_image_part("data:image/png;base64," + oversized)], "invalid_image"),
                    (
                        "user",
                        [_image_part("data:image/png;base64," + animated_url)],
                        "invalid_image",
                    ),
                    ("user", [_image_part("data:image/png;base64,A")], "invalid_image"),
                    ("user", [_image_part("data:image/png;base64,AAAA")], "invalid_image"),
                    ("user", [_image_part(_image_url(size=(2100, 2100)))], "invalid_image"),
                    (
                        "user",
                        [_image_part(_image_url("JPEG").replace("image/jpeg", "image/png"))],
                        "invalid_image",
                    ),
                    ("user", [_image_part()] * 5, "invalid_openai_request"),
                    ("system", [_image_part()], "image_input_not_supported"),
                ):
                    response = await lan.post(
                        route,
                        headers=headers,
                        json={**body, "messages": [{"role": role, "content": parts}]},
                    )
                    assert response.status_code == 400
                    assert response.json()["error"]["code"] == code
                    assert "data:image" not in response.text
                response = await lan.post(route, headers=headers, content=b" " * (1024**2 + 1))
                assert response.status_code == 413
                assert not upstream.requests and not gateway.uncertain
                jpeg = _image_part(_image_url("JPEG"))
                response = await lan.post(
                    route,
                    headers=headers,
                    json={**body, "messages": [{"role": "user", "content": [jpeg]}]},
                )
                assert response.status_code == 200
                assert len(upstream.requests) == 1
                request = upstream.requests[0]
                assert request.workload == "P5"
                assert request.images[0].model_dump(mode="json") == jpeg
                with pytest.raises(ValidationError, match="Multimodal parts require"):
                    GatewayRequest.model_validate_json(
                        request.model_copy(update={"workload": "P2"}).model_dump_json()
                    )
                with pytest.raises(GatewayError, match="invalid_openai_request"):
                    await bridge.complete(
                        {
                            "model": config.model_tag,
                            "messages": [{"role": "user", "content": [jpeg]}],
                        }
                    )
        finally:
            await client.aclose()
            await gateway.aclose()

    asyncio.run(run())


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


@pytest.mark.parametrize("arguments", ["{}", '{"text":"not authorized"}'])
def test_strata_p0_accepts_only_empty_delegation_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: str
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
            update={"inference": InferenceSettings(backend="strata", strata=settings)}
        )
        instance = "b" * 32
        monkeypatch.setenv("EMBER_STRATA_INSTANCE_ID", instance)
        monkeypatch.setenv("EMBER_STRATA_API_KEY", "synthetic")

        async def unused_submit(text: str, turn_id: UUID) -> str:
            pytest.fail("Gateway validates tool calls but does not execute them")

        definition = ConversationTask("Inspect fixture files.", unused_submit, True).definition(
            "delegate_task"
        )

        def metadata(body: dict[str, object]) -> httpx.Response:
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/status":
                return metadata(
                    {
                        "service": "strata",
                        "loaded": True,
                        "model": config.model_tag,
                        "context": {"native": config.context_tokens},
                    }
                )
            if request.url.path == "/v1/telemetry":
                return metadata({"instance": {"id": instance}})
            assert request.url.path == "/v1/chat/completions"
            body = json.loads(request.content)
            assert body["tool_choice"] == "auto"
            assert body["tools"] == [definition.model_dump(mode="json")]
            base = {"id": "fixture", "object": "chat.completion.chunk", "model": config.model_tag}
            functions = [
                {"name": "delegate_task", "arguments": arguments[:1]},
                {"arguments": arguments[1:]},
            ]
            choices = [
                {"index": 0, "delta": {"tool_calls": [{"index": 0, "function": function}]}}
                for function in functions
            ]
            terminal = {
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
            }
            chunks = [{"choices": [choice]} for choice in choices] + [terminal]
            text = "".join(f"data: {json.dumps(base | chunk)}\n\n" for chunk in chunks)
            return httpx.Response(
                200,
                stream=httpx.ByteStream((text + "data: [DONE]\n\n").encode()),
                headers={"Content-Type": "text/event-stream"},
            )

        gateway = P0Gateway(
            config,
            StrataUpstream(
                config,
                httpx.AsyncClient(
                    base_url=settings.endpoint,
                    transport=httpx.MockTransport(respond),
                ),
            ),
        )
        request = _request(config, (PromptMessage(role="user", content="Read fixture.txt"),))
        request = request.model_copy(update={"workload": "P0", "tools": (definition,)})
        try:
            events = [event async for event in (await gateway.admit(request)).events()]
            if arguments == "{}":
                assert [event.kind for event in events] == ["start", "tools", "end"], events[
                    -1
                ].code
                (call,) = events[1].calls
                assert call.name == "delegate_task" and call.arguments == {}
            else:
                assert [event.kind for event in events] == ["start", "error"]
                assert events[-1].code == "invalid_tool_arguments"
        finally:
            await gateway.aclose()

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
@pytest.mark.parametrize("vision", [False, True])
def test_strata_coding_context_uses_native_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stream: bool,
    thinking_options: dict[str, object],
    effort: str | None,
    vision: bool,
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
                "inference": InferenceSettings(
                    backend="strata", strata=settings, coding_api=True, coding_vision=vision
                ),
            }
        )
        instance = "b" * 32
        monkeypatch.setenv("EMBER_STRATA_INSTANCE_ID", instance)
        monkeypatch.setenv("EMBER_STRATA_API_KEY", "synthetic")
        # OpenCode can replay a single long text message alongside earlier images.
        content = "long fixture line\n" * 7500
        messages: list[dict[str, object]] = [
            {"role": "user", "content": content},
            {"role": "user", "content": content},
        ]
        if vision:
            messages[0]["content"] = [
                {"type": "text", "text": content},
                _image_part(),
                {"type": "text", "text": "After"},
            ]
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
        vision_available = False

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
                        "vision": {"available": vision_available},
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

                if vision:
                    unavailable = await lan.post(route, json=body)
                    assert unavailable.status_code == (200 if stream else 503)
                    assert "upstream_vision_unavailable" in unavailable.text
                    assert completion_calls == 0 and not gateway.uncertain
                    vision_available = True

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
            with pytest.raises(GatewayError, match="invalid_openai_request"):
                await bridge.complete({**legacy, "messages": messages[:2]})
            with pytest.raises(ValidationError, match="existing message length limit"):
                _request(config, (PromptMessage(role="user", content="x" * 100_001),))
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
