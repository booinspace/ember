"""The real OpenAI SDK can use the isolated stdio lane without opening network sockets."""

import importlib
import json
import socket
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from openai import OpenAI
from pydantic import JsonValue


class RPC:
    def __init__(self) -> None:
        self.calls: list[dict[str, JsonValue]] = []

    def request(self, kind: str, **values: JsonValue) -> JsonValue:
        assert kind == "model"
        self.calls.append(values)
        if values["method"] == "GET":
            return {
                "object": "list",
                "data": [{"id": "synthetic", "created": 0, "object": "model", "owned_by": "ember"}],
            }
        return {
            "id": "chatcmpl-synthetic",
            "created": 0,
            "object": "chat.completion",
            "model": "synthetic",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_synthetic",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"query":"example"}'},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
        }


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    scripts = Path(__file__).resolve().parents[2] / "scripts" / "hermes"
    monkeypatch.syspath_prepend(str(scripts))
    return importlib.import_module("hermes_transport")


@pytest.mark.parametrize("streaming", [False, True])
def test_real_sdk_metadata_and_tool_calls_use_stdio_only(
    transport: ModuleType, monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    def deny_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("The stdio transport must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", deny_socket)
    rpc = RPC()
    client = OpenAI(
        api_key="worker-local-dummy",
        base_url=transport.ENDPOINT,
        max_retries=0,
        http_client=httpx.Client(transport=transport.StdioTransport(rpc), trust_env=False),
    )
    try:
        assert client.models.list().data[0].id == "synthetic"
        if streaming:
            chunks = list(
                client.chat.completions.create(
                    model="synthetic",
                    messages=[{"role": "user", "content": "Synthetic"}],
                    max_tokens=32,
                    stream=True,
                    stream_options={"include_usage": True},
                )
            )
            call = chunks[0].choices[0].delta.tool_calls[0]
            assert call.id == "call_synthetic" and call.function.arguments == '{"query":"example"}'
            assert chunks[-1].choices == [] and chunks[-1].usage.total_tokens == 15
        else:
            completion = client.chat.completions.create(
                model="synthetic",
                messages=[{"role": "user", "content": "Synthetic"}],
                max_tokens=32,
            )
            assert completion.choices[0].message.tool_calls[0].id == "call_synthetic"
        assert [call["path"] for call in rpc.calls] == ["/v1/models", "/v1/chat/completions"]
        assert rpc.calls[-1]["body"]["max_tokens"] == 32
        assert "Authorization" not in json.dumps(rpc.calls)
    finally:
        client.close()


def test_pinned_agent_subclass_recreates_fresh_inert_clients(
    transport: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Agent:
        pass

    monkeypatch.setitem(sys.modules, "run_agent", SimpleNamespace(AIAgent=Agent))
    rpc = RPC()
    factory = transport.create_agent_factory(rpc)
    agent = factory()
    assert isinstance(agent, Agent)
    first = agent._build_keepalive_http_client(transport.ENDPOINT)
    second = agent._build_keepalive_http_client(transport.ENDPOINT)
    try:
        assert first is not second
        first.close()
        assert second.get(transport.ENDPOINT + "/models").status_code == 200
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize(
    "path", ["/api/generate", "/v1/shutdown", "/collective_rpc", "/v1/models?other=1"]
)
def test_unadvertised_routes_never_reach_the_parent(transport: ModuleType, path: str) -> None:
    rpc = RPC()
    with httpx.Client(transport=transport.StdioTransport(rpc), trust_env=False) as client:
        assert client.get("http://ember.invalid" + path).status_code == 404
    assert not rpc.calls


def test_oversized_request_and_hidden_reasoning_are_not_forwarded(transport: ModuleType) -> None:
    rpc = RPC()
    with httpx.Client(transport=transport.StdioTransport(rpc), trust_env=False) as client:
        response = client.post(
            transport.ENDPOINT + "/chat/completions", content=b"x" * (transport.LIMIT + 1)
        )
        assert response.status_code == 400 and not rpc.calls
    completion = rpc.request("model", method="POST")
    completion["choices"][0]["message"]["reasoning"] = "PRIVATE_REASONING_SENTINEL"
    assert b"PRIVATE_REASONING_SENTINEL" not in transport.completion_sse(completion)
