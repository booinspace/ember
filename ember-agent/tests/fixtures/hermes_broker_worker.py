"""Synthetic extended wire peer; real SDK/AppContainer qualification is a separate gate."""

import json
import sys


def emit(value: dict[str, object]) -> None:
    print(json.dumps({"protocol": 1, **value}), flush=True)


sequence = 0


def call(kind: str, **values: object) -> dict[str, object]:
    global sequence
    sequence += 1
    emit({"type": kind, "id": sequence, **values})
    reply = json.loads(sys.stdin.readline())
    assert reply["ok"] and reply["id"] == sequence
    return reply["body"]


emit(
    {
        "type": "hello",
        "revision": "71736c9f32597bbca9d89cede5aa136e55946b2f",
        "profile_sha256": "a" * 64,
        "tools": ["searx_search", "ember_recall", "ember_finish", "ember_tool"],
    }
)
start = json.loads(sys.stdin.readline())
mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
catalog = start["tool_catalog"]
assert "ember_tool" in start["allowed_tools"] and len(catalog) == 1
messages = [{"role": "user", "content": start["goal"]}]
if mode == "oversized":
    messages[0]["content"] += "x" * 8000
definitions = [
    {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
    for name in start["allowed_tools"]
]


def model() -> None:
    call(
        "model",
        method="POST",
        path="/v1/chat/completions",
        body={
            "model": start["model"],
            "messages": messages,
            "tools": definitions,
            "max_tokens": 32,
        },
    )


if mode != "premodel":
    model()
arguments = {"tool_id": catalog[0]["id"], "arguments": {"text": "one two three"}}
result = call("tool", name="ember_tool", arguments=arguments)
messages.extend(
    [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "native_call",
                    "type": "function",
                    "function": {"name": "ember_tool", "arguments": json.dumps(arguments)},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "native_call", "content": json.dumps(result)},
    ]
)
model()
if mode == "private_search":
    call("tool", name="searx_search", arguments={"query": "PRIVATE_MCP_RESULT"})
report = {"summary": "The text contains three words.", "citations": [result["citation_id"]]}
call("tool", name="ember_finish", arguments=report)
emit({"type": "result", "result": report})
emit({"type": "stopped", "purged": True, "returncode": 0})
