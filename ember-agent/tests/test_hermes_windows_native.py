"""Opt-in native proofs use synthetic canaries and the exact installed boundary primitives."""

import asyncio
import ctypes
import importlib
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

from ember_agent.inference.contracts import GatewayError, GatewayEvent, GatewayRequest
from ember_agent.inference.openai_bridge import OpenAIModelBridge
from ember_agent.runtime.config import load_config
from ember_agent.runtime.hermes_tasks import available_model_tools, copy_search_results
from ember_agent.runtime.task_router import ToolContext, ToolReceipt

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "win32", reason="Native Windows AppContainer proof."),
    pytest.mark.skipif(
        not os.getenv("EMBER_HERMES_PROFILE"), reason="Explicit installed profile required."
    ),
]

_PROBE = r"""
import json,os,socket,sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1]).parent))
from hermes_windows import verify_appcontainer
verify_appcontainer()
args=json.loads(sys.stdin.readline())
result={'type':'boundary_probe','pid':os.getpid(),'task_identity':Path.cwd().name,
        'token_is_appcontainer':True,'capabilities':0}
for key in ['canonical','credential','sibling']:
 p=Path(args[key]); read=write=False
 try: p.read_bytes(); read=True
 except OSError: pass
 try: p.write_bytes(b'REPLACED'); write=True
 except OSError: pass
 result[key+'_read']=read; result[key+'_write']=write
sdk=Path(args['sdk_write']); writable=False
try: sdk.write_bytes(b'BAD'); writable=True
except OSError: pass
result['sdk_write']=writable
home=Path.cwd(); (home/'synthetic-context.txt').write_text('SYNTHETIC_PRIVATE_CONTEXT')
result['own_home_write']=(home/'synthetic-context.txt').read_text()=='SYNTHETIC_PRIVATE_CONTEXT'
for key in ['loopback','lan']:
 host,port=args[key]; s=socket.socket(); s.settimeout(.3)
 try: result[key+'_connected']=s.connect_ex((host,port))==0
 except OSError: result[key+'_connected']=False
 finally: s.close()
print(json.dumps(result),flush=True)
sys.stdin.read()
"""


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, object, str, ModuleType]:
    receipt = Path(os.environ["EMBER_HERMES_PROFILE"])
    bridge = receipt.parent / "bridge"
    monkeypatch.syspath_prepend(str(bridge))
    setup = importlib.import_module("hermes_setup")
    windows = importlib.import_module("hermes_windows")
    profile, digest = setup.load_profile(receipt)
    return receipt, bridge, profile, digest, windows


def _controller(receipt: Path, bridge: Path) -> tuple[subprocess.Popen[str], queue.Queue]:
    # Only this qualification process changes the verified flags to run a hostile probe.
    # Production load_profile accepts exactly [-B]; actual SDK proof runs those normal flags.
    code = (
        "import json,sys; from pathlib import Path; "
        f"sys.path.insert(0,{str(bridge)!r}); "
        "from hermes_setup import load_profile; from hermes_windows import launch; "
        f"p,d=load_profile(Path({str(receipt)!r})); "
        f"p=p.model_copy(update={{'python_flags':['-B','-c',{_PROBE!r}]}}); "
        "c,purged=launch(p,d); "
        "print(json.dumps({'type':'stopped','purged':purged,'returncode':c}),flush=True); "
        "raise SystemExit(0 if c==0 and purged else 1)"
    )
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    frames = queue.Queue()

    def read() -> None:
        for line in process.stdout:
            frames.put(json.loads(line))

    threading.Thread(target=read, daemon=True).start()
    return process, frames


@pytest.mark.parametrize("shutdown", ["eof", "controller_crash"])
def test_native_denial_and_owned_shutdown(
    native: tuple[Path, Path, object, str, ModuleType], tmp_path: Path, shutdown: str
) -> None:
    receipt, bridge, profile, digest, windows = native
    api = windows.WindowsAPI()
    outside = tmp_path / "private-host"
    windows._private_directory(outside)
    canonical, credential = outside / "canonical.sqlite", outside / "credential.txt"
    canonical.write_bytes(b"SYNTHETIC_CANONICAL")
    credential.write_bytes(b"SYNTHETIC_CREDENTIAL")
    sibling = windows.Boundary(profile, identity=uuid4().hex)
    process = None
    child_handle = None
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    lan = ("10.254.254.10", 8095)
    with socket.create_connection(lan, timeout=2):
        pass  # Host positive control: the worker's network failure must not be an offline host.
    with socket.create_connection(listener.getsockname(), timeout=2):
        pass
    try:
        sibling.prepare()
        sibling_canary = sibling.home / "private-other-actor.txt"
        sibling_canary.write_bytes(b"SYNTHETIC_OTHER_ACTOR")
        sdk_write = Path(profile.source) / ("denied-probe-" + uuid4().hex + ".txt")
        process, frames = _controller(receipt, bridge)
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(
                {
                    "canonical": str(canonical),
                    "credential": str(credential),
                    "sibling": str(sibling_canary),
                    "sdk_write": str(sdk_write),
                    "loopback": listener.getsockname(),
                    "lan": lan,
                }
            )
            + "\n"
        )
        process.stdin.flush()
        frame = frames.get(timeout=35)
        assert frame["type"] == "boundary_probe"
        assert frame["token_is_appcontainer"] and frame["capabilities"] == 0
        assert frame["own_home_write"]
        for key in [
            "canonical_read",
            "canonical_write",
            "credential_read",
            "credential_write",
            "sibling_read",
            "sibling_write",
            "sdk_write",
            "loopback_connected",
            "lan_connected",
        ]:
            assert frame[key] is False, key
        assert canonical.read_bytes() == b"SYNTHETIC_CANONICAL"
        assert credential.read_bytes() == b"SYNTHETIC_CREDENTIAL"
        assert not sdk_write.exists()
        leases = Path(os.environ["LOCALAPPDATA"]) / "ember-hermes-leases"
        live = {item.name for item in leases.glob("*.json")}
        assert live
        own_lease = leases / (frame["task_identity"] + ".json")
        with own_lease.open("rb") as ledger:
            ledger.seek(1)  # The controller deliberately locks byte zero for its lifetime.
            record = json.loads(ledger.read())
        recovered = windows.Boundary(profile, identity=frame["task_identity"])
        recovered.restore()  # Derive SID identity without creating any new OS object.
        sid_text = recovered.sid_text
        api.security.FreeSid(recovered.sid)
        api.userenv.GetAppContainerFolderPath.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        api.userenv.GetAppContainerFolderPath.restype = ctypes.c_long
        folder_pointer = ctypes.c_wchar_p()
        assert api.userenv.GetAppContainerFolderPath(sid_text, ctypes.byref(folder_pointer)) == 0
        profile_folder = Path(folder_pointer.value)
        ole = ctypes.WinDLL("ole32")
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree(folder_pointer)
        assert profile_folder.exists()
        windows.recover_orphans(profile)  # A locked live controller must be skipped.
        assert own_lease.exists()
        assert process.poll() is None
        api.kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        api.kernel.OpenProcess.restype = ctypes.c_void_p
        child_handle = api.kernel.OpenProcess(0x100000, False, frame["pid"])
        assert child_handle
        started = time.monotonic()
        if shutdown == "eof":
            process.stdin.close()
            stopped = frames.get(timeout=4)
            assert stopped["type"] == "stopped" and stopped["purged"]
            process.wait(timeout=1)
        else:
            process.kill()  # Popen's retained native controller handle, never a saved PID.
            process.wait(timeout=2)
            windows.recover_orphans(profile)
        assert api.kernel.WaitForSingleObject(child_handle, 0) == 0
        elapsed = time.monotonic() - started
        assert elapsed < 4
        assert not own_lease.exists()
        assert not profile_folder.exists()
        for root in record["roots"]:
            acl = subprocess.check_output(
                [str(Path(os.environ["SystemRoot"]) / "System32/icacls.exe"), root], text=True
            )
            assert sid_text not in acl
        homes = Path(os.environ["LOCALAPPDATA"]) / "ember-hermes-tasks"
        assert not (homes / frame["task_identity"]).exists()
        assert sibling.home.exists()
        print(
            json.dumps(
                {
                    "shutdown": shutdown,
                    "cleanup_seconds": elapsed,
                    "child_signalled": True,
                    "canary_denials": 9,
                    "purged": True,
                    "profile_folder_absent": True,
                    "sid_grants_absent": True,
                }
            )
        )
    finally:
        listener.close()
        if child_handle:
            api.kernel.CloseHandle(child_handle)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        windows.recover_orphans(profile)
        assert sibling.close()


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


@pytest.mark.parametrize("model_rounds", [1, 2, 4])
def test_actual_sdk_socket_free_structured_completion(
    native: tuple, model_rounds: int, tmp_path: Path
) -> None:
    """The real pinned AIAgent uses only broker RPC and finishes before purge acknowledgement."""
    receipt, bridge, profile, digest, windows = native
    process = subprocess.Popen(
        [sys.executable, "-B", str(bridge / "hermes_worker.py"), "--profile", str(receipt)],
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
                                citation_ids=tuple(
                                    row["id"]
                                    for payload in copied_payloads
                                    for row in payload["results"]
                                ),
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
                                                "name": "searx_search"
                                                if search_turn
                                                else "ember_finish",
                                                "arguments": json.dumps(
                                                    {"query": "public SearXNG docs"}
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
                    if frame["name"] == "searx_search":
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
        windows.recover_orphans(profile)
