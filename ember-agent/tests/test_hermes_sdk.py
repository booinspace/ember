"""Real installed SDK qualification shared by the supported isolated worker transports."""

import asyncio
import json
import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from ember_agent.inference.contracts import GatewayError, GatewayEvent, GatewayRequest
from ember_agent.inference.openai_bridge import OpenAIModelBridge
from ember_agent.runtime.config import load_config
from ember_agent.runtime.hermes_tasks import (
    available_model_tools,
    broker_continuation_fits,
    copy_search_results,
)
from ember_agent.runtime.task_router import BrokerTool, ToolContext, ToolReceipt


@pytest.mark.integration
@pytest.mark.parametrize("model_rounds,broker", [(1, False), (2, False), (4, False), (2, True)])
def test_configured_sdk_socket_free_structured_completion(
    model_rounds: int, broker: bool, tmp_path: Path
) -> None:
    setting = os.getenv("EMBER_TEST_HERMES_CONFIG")
    if not setting:
        pytest.skip("Explicit installed isolated worker configuration required.")
    settings = load_config(Path(setting), require_model_pin=True).selected_harness
    assert settings is not None
    check_sdk_completion(settings.command, settings.profile_sha256, model_rounds, broker, tmp_path)


def _maximum_search_receipt() -> dict:
    """Three provider-shaped rows reach the real broker's 4096-byte receipt ceiling."""
    context = ToolContext(
        character_id=uuid4(), session_id=uuid4(), turn_id=uuid4(), task_id=uuid4()
    )
    prefix = "https://docs.searxng.org/dev/search_api.html?padding="
    rows = [
        {
            "id": "s" + str(index),
            "url": prefix + "x" * (512 - len(prefix)),
            "title": "t" * 128,
            "snippet": "x" * 256,
        }
        for index in range(1, 4)
    ]
    receipt = ToolReceipt(context=context, tool="searx_search", data={"results": rows})
    remaining = 4096 - len(receipt.model_dump_json().encode())
    for row in rows:
        snippet = list(row["snippet"])
        for index in range(len(snippet)):
            if remaining == 0:
                break
            size = min(remaining, 3)
            snippet[index] = "😀" if size == 3 else "雨" if size == 2 else "é"
            remaining -= size
        row["snippet"] = "".join(snippet)
    receipt = ToolReceipt(context=context, tool="searx_search", data={"results": rows})
    assert remaining == 0 and len(receipt.model_dump_json().encode()) == 4096
    return receipt.data


def check_sdk_completion(
    command: tuple[str, ...], digest: str, model_rounds: int, broker: bool, tmp_path: Path
) -> None:
    """The real pinned AIAgent uses only broker RPC and finishes before purge acknowledgement."""
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    start = {
        "type": "start",
        "protocol": 1,
        "goal": "Call ember_finish with summary Synthetic check passed and no citations.",
        "context": [],
        "allowed_tools": ["searx_search", "ember_finish"],
        "model": "qwen3.8-rvn:27b-iq3_s-mtp",
        "context_tokens": 131072,
        "max_iterations": 12,
        "max_output_tokens": 1192,
        "timeout_seconds": 30,
    }
    report = {"summary": "Synthetic check passed.", "citations": []}
    catalog = (
        (
            BrokerTool(
                id="mcp.synthetic",
                description="Return a synthetic bounded value.",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                effect="read",
            ),
        )
        if broker
        else ()
    )
    if broker:
        start["allowed_tools"].append("ember_tool")
        start["tool_catalog"] = [tool.model_dump(mode="json") for tool in catalog]
    example = Path(__file__).parents[1] / "config/deployment.example.toml"
    config_path = tmp_path / "deployment.toml"
    config_path.write_text(
        example.read_text()
        .replace('tag = ""', 'tag = "' + start["model"] + '"')
        .replace('expected_digest = ""', 'expected_digest = "' + "a" * 64 + '"')
        .replace("context_tokens = 8192", "context_tokens = 131072")
        .replace("max_input_tokens = 7000", "max_input_tokens = 16384")
        .replace("output_reserve_tokens = 1024", "output_reserve_tokens = 1192")
    )

    class NoGeneration:
        calls = 0

        async def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayEvent]:
            self.calls += 1
            raise AssertionError("This CPU-only SDK proof must not dispatch generation")
            yield

    client = NoGeneration()
    mapper = OpenAIModelBridge(
        client,
        load_config(config_path, require_model_pin=True),
        context_id=uuid4(),
        deadline_at="2099-01-01T00:00:00.000000Z",
    )
    kinds, methods = [], []
    request_sizes = []
    request_costs = []
    projected_costs = []
    copied_sizes = []
    copied_payloads = []
    sequence = 0
    body_bytes = system_chars = 0
    try:
        for _ in range(16):
            line = process.stdout.readline()
            assert line
            frame = json.loads(line)
            kind = frame["type"]
            kinds.append(kind)
            if kind == "hello":
                assert frame["profile_sha256"] == digest
                process.stdin.write(json.dumps(start) + "\n")
                process.stdin.flush()
            elif kind in {"model", "tool"}:
                sequence += 1
                assert type(frame["id"]) is int and frame["id"] == sequence
                if kind == "model":
                    methods.append((frame["method"], frame["path"]))
                    assert methods == [("POST", "/v1/chat/completions")] * len(methods)
                    assert len(methods) <= model_rounds
                    body = frame["body"]
                    body_bytes = len(
                        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
                    )
                    system_chars = sum(
                        len(message["content"])
                        for message in body["messages"]
                        if message["role"] == "system"
                    )
                    assert body_bytes <= 16384
                    _, normalized = mapper._request(body)
                    history = [message for message in body["messages"] if message["role"] == "tool"]
                    assert len(history) == len(copied_payloads)
                    for message, payload in zip(history, copied_payloads, strict=True):
                        assert json.loads(message["content"]) == payload
                    serialized = json.dumps(
                        {
                            "messages": [
                                message.model_dump(mode="json") for message in normalized.messages
                            ],
                            "tools": [tool.model_dump(mode="json") for tool in normalized.tools],
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    cost = len(serialized.encode()) + 512 + 64 * len(normalized.messages)
                    assert cost <= 16384 and normalized.max_output_tokens == 1192
                    assert cost + normalized.max_output_tokens < start["context_tokens"]
                    request_costs.append(cost)
                    projected = {
                        **body,
                        "tools": [
                            tool.model_dump(mode="json")
                            for tool in available_model_tools(
                                normalized.tools,
                                search_remaining=3 - len(copied_payloads),
                                recall_available=False,
                                evidence_available=bool(copied_payloads),
                                citation_ids=("tool-1",)
                                if broker and copied_payloads
                                else tuple(
                                    row["id"]
                                    for payload in copied_payloads
                                    for row in payload["results"]
                                ),
                                broker_tools=catalog,
                            )
                        ],
                    }
                    _, authorized = mapper._request(projected)
                    assert authorized.messages == normalized.messages
                    assert authorized.context_id == normalized.context_id == mapper.context_id
                    assert authorized.model == normalized.model and authorized.workload == "P2"
                    serialized = json.dumps(
                        {
                            "messages": [
                                message.model_dump(mode="json") for message in authorized.messages
                            ],
                            "tools": [tool.model_dump(mode="json") for tool in authorized.tools],
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    projected_cost = len(serialized.encode()) + 512 + 64 * len(authorized.messages)
                    assert projected_cost <= 16384 and authorized.max_output_tokens == 1192
                    projected_costs.append(projected_cost)
                    if broker and len(methods) == 1:
                        assert broker_continuation_fits(
                            max(cost, projected_cost), "mcp.synthetic", 16384
                        )
                    if len(methods) == 1:
                        oversized = json.loads(json.dumps(body))
                        oversized["messages"][-1]["content"] = "😀" * 8192
                        with pytest.raises(GatewayError, match="prompt_overflow"):
                            asyncio.run(mapper.complete(oversized))
                        assert client.calls == 0
                    request_sizes.append(body_bytes)
                    assert {tool["function"]["name"] for tool in body["tools"]} == set(
                        start["allowed_tools"]
                    )
                    search_turn = len(methods) < model_rounds
                    reply = {
                        "id": "synthetic",
                        "object": "chat.completion",
                        "model": start["model"],
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": str(uuid4()),
                                            "type": "function",
                                            "function": {
                                                "name": "ember_tool"
                                                if broker and search_turn
                                                else "searx_search"
                                                if search_turn
                                                else "ember_finish",
                                                "arguments": json.dumps(
                                                    {
                                                        "tool_id": "mcp.synthetic",
                                                        "arguments": {"text": "synthetic"},
                                                    }
                                                    if broker and search_turn
                                                    else {"query": "public SearXNG docs"}
                                                    if search_turn
                                                    else report
                                                ),
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    }
                else:
                    if frame["name"] == "ember_tool":
                        context = ToolContext(
                            character_id=uuid4(),
                            session_id=uuid4(),
                            turn_id=uuid4(),
                            task_id=uuid4(),
                        )
                        value = '"\\' * 1000
                        while True:
                            receipt = ToolReceipt(
                                context=context, tool="mcp.synthetic", data={"value": value}
                            )
                            size = len(receipt.model_dump_json().encode())
                            if size <= 4096:
                                break
                            value = value[:-1]
                        assert size > 4000
                        reply = {
                            "receipt": receipt.model_dump(mode="json"),
                            "citation_id": "tool-1",
                        }
                        copied_payloads.append(reply)
                        report["citations"] = ["tool-1"]
                    elif frame["name"] == "searx_search":
                        assert model_rounds > 1 and frame["arguments"] == {
                            "query": "public SearXNG docs"
                        }
                        reply = (
                            _maximum_search_receipt()
                            if model_rounds == 4
                            else {
                                "results": [
                                    {
                                        "id": "q1-s1",
                                        "url": "https://docs.searxng.org/dev/search_api.html",
                                        "title": "Search API",
                                        "snippet": "Synthetic public result",
                                    }
                                ]
                            }
                        )
                        original = json.dumps(reply, ensure_ascii=False)
                        raw_rows = reply["results"]
                        selected = copy_search_results(raw_rows, len(methods))
                        assert json.dumps(reply, ensure_ascii=False) == original
                        assert selected
                        for selected_row in selected:
                            source_id = selected_row["id"].removeprefix(
                                "q" + str(len(methods)) + "-"
                            )
                            assert any(
                                selected_row == {**row, "id": selected_row["id"]}
                                and row["id"] == source_id
                                for row in raw_rows
                            )
                        reply = {"results": selected}
                        copied_payloads.append(reply)
                        copied_sizes.append(len(json.dumps(reply, ensure_ascii=False).encode()))
                        assert copied_sizes[-1] <= 2048
                        if len(methods) == 1:
                            report["citations"] = [selected[0]["id"]]
                    else:
                        assert frame["name"] == "ember_finish" and frame["arguments"] == report
                        reply = {"accepted": True}
                process.stdin.write(
                    json.dumps(
                        {"type": "reply", "protocol": 1, "id": sequence, "ok": True, "body": reply}
                    )
                    + "\n"
                )
                process.stdin.flush()
            elif kind == "result":
                assert frame["result"] == report
            elif kind == "stopped":
                assert frame["purged"] and frame["returncode"] == 0
                break
            else:
                pytest.fail("Unexpected content-free worker frame: " + kind)
        assert kinds == ["hello"] + ["model", "tool"] * model_rounds + ["result", "stopped"]
        process.stdin.close()
        assert process.wait(timeout=3) == 0
        print(
            json.dumps(
                {
                    "sdk_synthetic": "passed",
                    "body_bytes": request_sizes,
                    "bridge_input_cost_bytes": request_costs,
                    "host_projected_input_cost_bytes": projected_costs,
                    "copied_search_context_bytes": copied_sizes,
                    "model_rounds": model_rounds,
                    "system_chars": system_chars,
                    "rpc_ids": "strict_integers",
                    "purged": True,
                    "process_exit": 0,
                }
            )
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
