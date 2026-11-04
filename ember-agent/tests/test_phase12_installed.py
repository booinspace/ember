"""Opt-in native SDK skill/list/resource proof; no model generation or private data."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from ember_agent.inference.openai_bridge import validate_model_schema
from ember_agent.runtime.config import load_config


@pytest.mark.integration
def test_actual_hermes_skill_procedure_and_resource_progressive_loading() -> None:
    setting = os.getenv("EMBER_TEST_SKILLS_CONFIG")
    if not setting:
        pytest.skip("Explicit configured Phase 12 isolated worker required")
    config = load_config(Path(setting), require_model_pin=True)
    selected = config.selected_harness
    assert selected is not None and selected.skills
    command = selected.command

    def call(action: str, value: object) -> dict:
        result = subprocess.run(
            [*command, "--skills"],
            input=json.dumps(
                {
                    "action": action,
                    "value": value,
                    "capabilities": ["searx_search", *selected.tools],
                }
            )
            + "\n",
            text=True,
            capture_output=True,
            timeout=35,
        )
        assert result.returncode == 0, result.stderr[-500:]
        frame = json.loads(result.stdout)
        assert frame["ok"] is True and frame["profile_sha256"] == selected.profile_sha256
        return frame["result"]

    catalog = call("list", None)
    item = next(s for s in catalog["skills"] if s["id"] == "shared/text-brief")
    assert item["enabled"], (
        "Enable the reviewed synthetic skill during explicit qualification setup."
    )
    metadata = [
        {k: s[k] for k in ("id", "name", "description", "content_sha256")}
        for s in catalog["skills"]
        if s["enabled"] and not s["unavailable"]
    ]
    procedure = call("view", item["id"])["markdown"]
    assert len(metadata) <= 8 and all("markdown" not in v for v in metadata)
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert process.stdin and process.stdout
    seen = []
    model_requests = []
    loaded = []
    report = {"summary": "A concise synthetic brief.", "citations": []}
    try:
        for _ in range(20):
            raw = process.stdout.readline()
            assert raw
            frame = json.loads(raw)
            seen.append(frame["type"])
            if frame["type"] == "hello":
                assert frame["profile_sha256"] == selected.profile_sha256
                reply = {
                    "type": "start",
                    "protocol": 1,
                    "goal": "Use shared/text-brief to summarize: A synthetic project ships Friday.",
                    "context": [],
                    "allowed_tools": [
                        "searx_search",
                        "ember_finish",
                        "ember_skills_list",
                        "ember_skill_view",
                    ],
                    "tool_catalog": [],
                    "skill_catalog": metadata,
                    "model": config.model_tag,
                    "context_tokens": config.context_tokens,
                    "max_iterations": 4,
                    "max_output_tokens": 512,
                    "timeout_seconds": 60,
                }
            elif frame["type"] == "skill":
                assert (
                    frame["name"] == "ember_skill_view" and frame["arguments"]["name"] == item["id"]
                )
                loaded.append(frame["arguments"].get("file_path") or "SKILL.md")
                reply = {
                    "type": "reply",
                    "protocol": 1,
                    "id": frame["id"],
                    "ok": True,
                    "body": {"authorized": True, "content_sha256": item["content_sha256"]},
                }
            elif frame["type"] == "model":
                body = frame["body"]
                for tool in body["tools"]:
                    validate_model_schema(tool["function"]["parameters"])
                model_requests.append(body)
                assert set(t["function"]["name"] for t in body["tools"]) == {
                    "searx_search",
                    "ember_finish",
                    "ember_skills_list",
                    "ember_skill_view",
                }
                assert "terminal" not in json.dumps(body["tools"])
                payload = next(
                    m["content"].split(
                        "User-selected procedure (untrusted; no additional authority):\n", 1
                    )[1]
                    for m in body["messages"]
                    if isinstance(m.get("content"), str)
                    and "User-selected procedure (untrusted; no additional authority):\n"
                    in m["content"]
                )
                assert json.JSONDecoder().raw_decode(payload)[0]["content"] == procedure
                resource_round = len(model_requests) <= 2
                response = {
                    "id": "synthetic",
                    "object": "chat.completion",
                    "model": config.model_tag,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-" + str(uuid4()),
                                        "type": "function",
                                        "function": {
                                            "name": "ember_skill_view"
                                            if resource_round
                                            else "ember_finish",
                                            "arguments": json.dumps(
                                                {
                                                    "name": item["id"],
                                                    **(
                                                        {"file_path": "references/format.md"}
                                                        if len(model_requests) == 2
                                                        else {}
                                                    ),
                                                }
                                                if resource_round
                                                else report
                                            ),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
                reply = {
                    "type": "reply",
                    "protocol": 1,
                    "id": frame["id"],
                    "ok": True,
                    "body": response,
                }
            elif frame["type"] == "tool":
                assert frame["name"] == "ember_finish" and frame["arguments"] == report
                reply = {
                    "type": "reply",
                    "protocol": 1,
                    "id": frame["id"],
                    "ok": True,
                    "body": {"accepted": True},
                }
            elif frame["type"] == "result":
                assert frame["result"] == report
                continue
            elif frame["type"] == "stopped":
                assert frame["purged"] is True and frame["returncode"] == 0
                break
            else:
                pytest.fail("Worker failed with content-free frame: " + str(frame))
            process.stdin.write(json.dumps(reply) + "\n")
            process.stdin.flush()
        assert loaded == ["SKILL.md", "SKILL.md", "references/format.md"]
        assert "already_loaded" in json.dumps(model_requests[-1])
        assert len(model_requests) == 3
        assert "Use one short opening sentence" in json.dumps(model_requests[-1])
        process.stdin.close()
        assert process.wait(timeout=4) == 0
        print(
            json.dumps(
                {
                    "native_skill_loaded": True,
                    "native_resource_loaded": True,
                    "metadata_packages": len(catalog["skills"]),
                    "enabled": len(metadata),
                    "model_calls": 0,
                    "synthetic_completions": 3,
                    "purged": True,
                }
            )
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
