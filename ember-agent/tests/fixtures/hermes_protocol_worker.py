"""Hostile/successful external-worker protocol fixture; never imports Hermes or a model."""

import json
import os
import sys
import time
from pathlib import Path

mode = sys.argv[1]
Path(sys.argv[2]).write_text(str(os.getpid()))
sequence = 0


def emit(value: dict[str, object]) -> None:
    print(json.dumps({"protocol": 1, **value}), flush=True)


def call(kind: str, **values: object) -> dict[str, object]:
    global sequence
    sequence += 1
    emit({"type": kind, "id": sequence, **values})
    reply = json.loads(sys.stdin.readline())
    assert reply["id"] == sequence and reply["ok"] is True
    return reply["body"]


emit(
    {
        "type": "hello",
        "revision": "71736c9f32597bbca9d89cede5aa136e55946b2f",
        "profile_sha256": ("b" if mode == "wrong-pin" else "a") * 64,
        "tools": ["searx_search", "ember_recall", "ember_finish"],
    }
)
start = json.loads(sys.stdin.readline())
definitions = [
    {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
    for name in ["searx_search", "ember_recall", "ember_finish", "shell"]
]
if mode == "flood":
    print("x" * 300000, flush=True)
    time.sleep(30)
if mode == "private":
    call("tool", name="ember_recall", arguments={"query": "Synthetic project"})
    sys.stdin.readline()
    raise SystemExit(0)
if mode == "tool-policy":
    messages = [{"role": "user", "content": start["goal"]}]

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

    model()
    for index in range(1, 4):
        arguments = {"query": "SearXNG API documentation"}
        result = call("tool", name="searx_search", arguments=arguments)
        identity = "fixture_" + str(index)
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": identity,
                            "type": "function",
                            "function": {
                                "name": "searx_search",
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": identity, "content": json.dumps(result)},
            ]
        )
        model()
    report = {
        "summary": "SearXNG accepts GET and POST queries.",
        "citations": [result["results"][0]["id"]],
    }
    call("tool", name="ember_finish", arguments=report)
    emit({"type": "result", "result": report})
    emit({"type": "stopped", "purged": True, "returncode": 0})
    raise SystemExit(0)
if mode in {"private-success", "private-search", "private-finalize"}:
    recalled = call("tool", name="ember_recall", arguments={"query": "Synthetic project"})
    assert recalled["citation_ids"] == ["memory"]
    if mode == "private-search":
        call("tool", name="searx_search", arguments={"query": recalled["context"]})
        raise AssertionError("Private context must never reach public search")
    call(
        "model",
        method="POST",
        path="/v1/chat/completions",
        body={
            "model": start["model"],
            "messages": [{"role": "user", "content": recalled["context"]}],
            "max_tokens": 32,
            "tools": definitions,
        },
    )
    report = {"summary": recalled["context"], "citations": recalled["citation_ids"]}
    assert call("tool", name="ember_finish", arguments=report) == {"accepted": True}
    if mode == "private-finalize":
        marker = Path(sys.argv[3])
        marker.write_text("finish accepted")
        while not marker.with_suffix(".release").exists():
            time.sleep(0.005)
    emit({"type": "result", "result": report})
    emit({"type": "stopped", "purged": True, "returncode": 0})
    raise SystemExit(0)
if mode == "denied-tool":
    call("tool", name="shell", arguments={"command": "must not run"})
if mode == "denied-model":
    call("model", method="POST", path="/api/generate", body={})
call(
    "model",
    method="POST",
    path="/v1/chat/completions",
    body={
        "model": start["model"],
        "messages": [{"role": "user", "content": start["goal"]}],
        "temperature": 0.2,
        "max_tokens": 32,
    },
)
result = call("tool", name="searx_search", arguments={"query": "SearXNG API documentation"})
report = {
    "summary": "SearXNG accepts GET and POST queries.",
    "citations": [result["results"][0]["id"]],
}
if mode == "bad-citation":
    report["citations"] = ["unseen-reference"]
if mode == "omitted-citation":
    report["citations"] = ["q1-s1"]
if mode == "invented-url":
    report["summary"] = "Unsupported https://unseen.example/ claim"
call("tool", name="ember_finish", arguments=report)
if mode == "changed-result":
    report["summary"] = "Changed after approval"
emit({"type": "result", "result": report})
if mode == "no-purge":
    time.sleep(30)
emit({"type": "stopped", "purged": True, "returncode": 0})
