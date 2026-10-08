"""Phase 3 process ownership, token isolation and actual LiveKit room acceptance."""

import asyncio
import json
import os
import shutil
import socket
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
from livekit import api, rtc

from ember_agent.character.core import CharacterCore
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import GatewayEvent, GatewayRequest, GenerationUsage
from ember_agent.realtime.client import RoomClient
from ember_agent.realtime.contracts import (
    AGENT_IDENTITY,
    TURN_METHOD,
    USER_IDENTITY,
    RememberInput,
    RoomBinding,
    TurnInput,
)
from ember_agent.realtime.session import HostedRoom
from ember_agent.runtime.config import ConfigurationError, load_config
from ember_agent.runtime.livekit_config import credentials, server_yaml
from ember_agent.runtime.livekit_runtime import RuntimeServices
from ember_agent.runtime.processes import OwnedProcess, RuntimeLease
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject

EXAMPLE = Path(__file__).parents[1] / "config/deployment.example.toml"


def example_text() -> str:
    content = EXAMPLE.read_text()
    start = content.index("[services.livekit]")
    end = content.index("[services.kokoro]", start)
    return (
        content[:start]
        + '[services.livekit]\nendpoint = "ws://127.0.0.1:7880"\nmode = "attach"\n\n'
        + content[end:]
    )


def free_port() -> int:
    with socket.socket() as service:
        service.bind(("127.0.0.1", 0))
        return service.getsockname()[1]


def test_credentials_persist_and_tokens_have_only_room_text_authority(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    value = credentials(path, create=True)
    assert credentials(path, create=False) == value
    assert value.api_secret not in repr(value)
    room = "ember-" + uuid4().hex
    verifier = api.TokenVerifier(value.api_key, value.api_secret)
    claims = verifier.verify(value.token(room, USER_IDENTITY))
    assert claims.identity == USER_IDENTITY
    assert claims.video.room == room and claims.video.room_join
    assert claims.video.can_publish is False and claims.video.can_publish_data is True
    assert claims.video.room_admin is not True and claims.video.room_create is not True
    text = server_yaml(value, port=7880, tcp_port=7881, udp_port=7882)
    assert "127.0.0.1" in text and "devkey" not in text
    with pytest.raises(ConfigurationError, match="existing"):
        credentials(tmp_path / "missing.json", create=False)


def test_runtime_lease_and_exact_child_shutdown(tmp_path: Path) -> None:
    lease = RuntimeLease(tmp_path)
    try:
        with pytest.raises(ConfigurationError, match="owned"):
            RuntimeLease(tmp_path)
    finally:
        lease.close()
    again = RuntimeLease(tmp_path)
    again.close()
    child = OwnedProcess(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        root=tmp_path,
        component="test",
        instance=uuid4(),
    )
    try:
        child.check()
        metadata = json.loads((tmp_path / "test-process.json").read_text())
        assert metadata["pid"] == child.process.pid
        assert metadata["mode"] == "managed"
    finally:
        child.stop()
    assert child.process.poll() is not None
    child.stop()


def test_livekit_settings_resolve_paths_and_reject_port_collision(tmp_path: Path) -> None:
    path = tmp_path / "deployment.toml"
    original = example_text()
    path.write_text(
        original.replace(
            'endpoint = "ws://127.0.0.1:7880"\nmode = "attach"',
            'endpoint = "ws://127.0.0.1:7880"\nmode = "managed"\n'
            'executable = "../server.exe"\ncredentials_file = "private/keys.json"',
        )
    )
    config = load_config(path)
    assert config.services.livekit.executable == str((tmp_path / "../server.exe").resolve())
    assert config.services.livekit.credentials_file == tmp_path / "private/keys.json"
    path.write_text(
        original.replace(
            'mode = "attach"\n\n[services.kokoro]',
            'mode = "attach"\ntcp_port = 7880\n\n[services.kokoro]',
        )
    )
    with pytest.raises(ConfigurationError, match="ports"):
        load_config(path)


class FakeModel:
    def __init__(self) -> None:
        self.requests: list[GatewayRequest] = []

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        self.requests.append(request)
        yield GatewayEvent(
            request_id=request.request_id, context_id=request.context_id, sequence=0, kind="start"
        )
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=1,
            kind="text",
            text="The synthetic memory says cobalt blue.",
        )
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=2,
            kind="end",
            usage=GenerationUsage(
                prompt_tokens=20,
                output_tokens=8,
                total_seconds=0.1,
                first_visible_seconds=0.05,
                tokens_per_second=80.0,
            ),
        )


@pytest.fixture
def livekit_executable() -> str:
    configured = os.environ.get("EMBER_LIVEKIT_EXE")
    found = configured or shutil.which("livekit-server")
    if not found:
        pytest.skip("Set EMBER_LIVEKIT_EXE to the explicitly installed LiveKit Server.")
    return found


def room_config(tmp_path: Path, executable: str, *, real: bool) -> Path:
    if real:
        original = load_config(Path(os.environ["EMBER_REAL_CONFIG"]), require_model_pin=True)
    else:
        original = None
    content = example_text()
    content = content.replace("../../.local/data", "data").replace(
        "../../.local/runtime", "runtime"
    )
    content = content.replace('tag = ""', 'tag = "synthetic"')
    content = content.replace('expected_digest = ""', 'expected_digest = "' + "a" * 64 + '"')
    signal, tcp, udp = free_port(), free_port(), free_port()
    while len({signal, tcp, udp}) != 3:
        signal, tcp, udp = free_port(), free_port(), free_port()
    content = content.replace(
        'endpoint = "ws://127.0.0.1:7880"\nmode = "attach"',
        f'endpoint = "ws://127.0.0.1:{signal}"\nmode = "managed"\n'
        f"executable = {json.dumps(executable)}\ntcp_port = {tcp}\nudp_port = {udp}",
    )
    if original:
        content = content.replace('tag = "synthetic"', 'tag = "' + original.model_tag + '"')
        content = content.replace("a" * 64, original.model_digest)
        content = content.replace("http://127.0.0.1:11435", original.services.gateway.endpoint)
        (tmp_path / "runtime").mkdir(exist_ok=True)
        shutil.copyfile(original.runtime_root / "gateway.token", tmp_path / "runtime/gateway.token")
    path = tmp_path / "deployment.toml"
    path.write_text(content, encoding="utf-8")
    return path


async def exercise_room(path: Path, *, real: bool) -> None:
    config = load_config(path, require_model_pin=True)
    config.runtime_root.mkdir(exist_ok=True)
    # Seed an isolated synthetic observation and reopen to prove canonical restart.
    with CanonicalEngine(config.data_root) as engine:
        character = engine.create_character()
        subject = ensure_subject(engine, character.store_id)
        author = MemoryAuthoringService(
            engine, subject, authority=MutationAuthority(mode="confirmed_user")
        )
        receipt = author.create(
            NewMemoryInput(idempotency_key="favorite", text="My favorite color is cobalt blue.")
        )
        identity = receipt.affected[0].memory_id
    engine = CanonicalEngine(config.data_root)
    memory = InProcessAsyncMemoryClient(engine.character(character.store_id))
    author = MemoryAuthoringService(
        engine, subject, authority=MutationAuthority(mode="confirmed_user")
    )
    fake = FakeModel()
    client = None
    if real:
        client = HttpGatewayClient(
            config.services.gateway.endpoint,
            (config.runtime_root / "gateway.token").read_text().strip(),
        )
    core = CharacterCore(memory, client or fake, model_tag=config.model_tag)
    services = RuntimeServices(config, path)
    # Fake gateway readiness on the room-only acceptance; no fake-model request goes to Ollama.
    if not real:
        from unittest.mock import patch

        from ember_agent.inference.contracts import GatewayHealth

        async def ready(_self: HttpGatewayClient) -> GatewayHealth:
            return GatewayHealth(
                status="ready",
                model=config.model_tag,
                digest=config.model_digest,
                context_tokens=8192,
            )

        (config.runtime_root / "gateway.token").write_text("a" * 40)
        context = patch(
            "ember_agent.runtime.livekit_runtime.port_open",
            side_effect=lambda endpoint: endpoint == config.services.gateway.endpoint,
        )
        health_patch = patch.object(HttpGatewayClient, "health", ready)
        context.start()
        health_patch.start()
    service = None
    host = None
    participant = None
    try:
        secret = await services.start()
        binding = RoomBinding(
            instance_id=services.instance,
            character_id=character.store_id,
            room="ember-" + uuid4().hex,
            endpoint=config.services.livekit.endpoint,
        )
        service = api.LiveKitAPI(
            binding.endpoint.replace("ws://", "http://"), secret.api_key, secret.api_secret
        )
        await service.room.create_room(api.CreateRoomRequest(name=binding.room, max_participants=2))

        async def remember(request: RememberInput) -> str:
            response = await asyncio.to_thread(
                author.create, NewMemoryInput(idempotency_key=request.key, text=request.text)
            )
            return response.outcome

        host = HostedRoom(core, binding, remember)
        await host.start(secret.token(binding.room, AGENT_IDENTITY, agent=True))
        participant = RoomClient(binding)
        await participant.connect(secret.token(binding.room, USER_IDENTITY))
        output, result = await participant.ask("What is my favorite color? Answer briefly.")
        assert result.retained_memories == 1
        assert "cobalt" in output.lower()
        assert (await memory.get_sources(identity))[0].quote == "My favorite color is cobalt blue."
        assert (
            await participant.remember("another", "My synthetic animal is an otter.") == "applied"
        )
        if not real:
            replay = TurnInput(request_id=result.request_id, text="What is my favorite color?")
            with pytest.raises(rtc.RpcError):
                await participant.room.local_participant.perform_rpc(
                    destination_identity=AGENT_IDENTITY,
                    method=TURN_METHOD,
                    payload=replay.model_dump_json(),
                )
            with pytest.raises(rtc.RpcError):
                await host._remember(
                    rtc.RpcInvocationData(
                        request_id="unauthorized",
                        caller_identity="intruder",
                        payload="{}",
                        response_timeout=5,
                    )
                )
            assert len(fake.requests) == 1
            assert "cobalt blue" in fake.requests[0].messages[1].content
        owned = services.children[0]
    finally:
        if participant:
            await participant.aclose()
        if host:
            await host.aclose()
        if service:
            await service.aclose()
        await memory.aclose()
        await asyncio.to_thread(engine.close)
        if client:
            await client.aclose()
        await services.aclose()
        if not real:
            health_patch.stop()
            context.stop()
    assert owned.process.poll() is not None


@pytest.mark.integration
def test_actual_room_recall_and_explicit_authoring_after_restart(
    tmp_path: Path, livekit_executable: str
) -> None:
    path = room_config(tmp_path, livekit_executable, real=False)
    asyncio.run(exercise_room(path, real=False))


@pytest.mark.integration
def test_actual_room_qwen_recall_after_restart(tmp_path: Path, livekit_executable: str) -> None:
    if not os.environ.get("EMBER_REAL_CONFIG"):
        pytest.skip("Set EMBER_REAL_CONFIG and run the matching gateway.")
    path = room_config(tmp_path, livekit_executable, real=True)
    asyncio.run(exercise_room(path, real=True))


def test_missing_managed_binary_fails_without_starting_children(tmp_path: Path) -> None:
    path = room_config(tmp_path, str(tmp_path / "missing.exe"), real=False)
    config = load_config(path, require_model_pin=True)
    config.runtime_root.mkdir()
    services = RuntimeServices(config, path)

    async def run() -> None:
        try:
            with pytest.raises(ConfigurationError, match="executable"):
                await services.start()
        finally:
            await services.aclose()
        assert not services.children

    asyncio.run(run())


@pytest.mark.integration
def test_runtime_starts_managed_servers_and_drains_only_owned_processes(
    tmp_path: Path, livekit_executable: str
) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from ember_agent.runtime.livekit_runtime import run_runtime

    requests: list[dict[str, object]] = []

    class OllamaFixture(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def do_GET(self) -> None:
            model = {"name": "synthetic", "digest": "a" * 64, "context_length": 8192}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"models": [model]}).encode())

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            self.send_response(200)
            self.end_headers()
            value = {
                "model": "synthetic",
                "message": {"role": "assistant", "content": "Your favorite color is cobalt blue."},
                "done": True,
                "prompt_eval_count": 100,
                "eval_count": 8,
                "eval_duration": 1000000000,
            }
            self.wfile.write(json.dumps(value).encode() + b"\n")

    backend = ThreadingHTTPServer(("127.0.0.1", 0), OllamaFixture)
    thread = Thread(target=backend.serve_forever, name="synthetic-ollama")
    thread.start()
    path = room_config(tmp_path, livekit_executable, real=False)
    gateway_port = free_port()
    content = (
        path.read_text()
        .replace("http://127.0.0.1:11434", f"http://127.0.0.1:{backend.server_port}")
        .replace("http://127.0.0.1:11435", f"http://127.0.0.1:{gateway_port}")
    )
    path.write_text(content)
    config = load_config(path, require_model_pin=True)

    async def run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(run_runtime(config, path, stop=stop))
        client = None
        try:
            discovery = config.runtime_root / "livekit-room.json"
            async with asyncio.timeout(30):
                while not discovery.exists():
                    if task.done():
                        await task
                    await asyncio.sleep(0.05)
            binding = RoomBinding.model_validate_json(discovery.read_bytes())
            secret = credentials(config.services.livekit.credentials_file, create=False)
            client = RoomClient(binding)
            await client.connect(secret.token(binding.room, USER_IDENTITY))
            assert await client.remember("color", "My favorite color is cobalt blue.") == "applied"
            output, result = await client.ask("What is my favorite color?")
            assert "cobalt" in output and result.retained_memories == 1
        finally:
            if client:
                await client.aclose()
            stop.set()
            await asyncio.wait_for(task, 20)
        assert not (config.runtime_root / "livekit-room.json").exists()
        state = json.loads((config.runtime_root / "gateway-state.json").read_text())
        assert state["status"] == "complete"
        with socket.socket() as connection:
            assert connection.connect_ex(("127.0.0.1", gateway_port)) != 0

    try:
        asyncio.run(run())
    finally:
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=5)
    assert len(requests) == 1 and "cobalt blue" in json.dumps(requests[0]["messages"])


def test_memory_owner_is_released_even_when_dialogue_drain_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ember_agent.runtime.composition import LocalRuntime

    path = room_config(tmp_path, str(tmp_path / "unused.exe"), real=False)
    config = load_config(path, require_model_pin=True)
    config.runtime_root.mkdir()
    (config.runtime_root / "gateway.token").write_text("a" * 40)
    runtime = LocalRuntime(config)
    identity = runtime.memory.character_id
    original = runtime.sink.aclose

    async def failed_drain() -> None:
        await original()
        raise RuntimeError("synthetic drain failure")

    monkeypatch.setattr(runtime.sink, "aclose", failed_drain)
    try:
        with pytest.raises(RuntimeError, match="synthetic drain"):
            asyncio.run(runtime.aclose())
    finally:
        runtime.close_workers()
    with CanonicalEngine(config.data_root) as reopened:
        assert reopened.character(identity).character_id == identity
