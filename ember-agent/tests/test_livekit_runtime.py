"""Phase 3 process ownership, token isolation and actual LiveKit room acceptance."""

import asyncio
import json
import os
import secrets
import shutil
import socket
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from time import monotonic
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
from ember_memory_abi.common import CanonicalId
from ember_memory_abi.memory import EntityRef
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.models import StoreInfo

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
    # Probe random user ports below the usual Windows ephemeral range. Repeated
    # bind(port=0) can return an entire consecutive UDP-excluded range, making
    # retries fail together even though most user ports remain available.
    for _ in range(100):
        candidate = 10000 + secrets.randbelow(35000)
        with socket.socket() as service, socket.socket(type=socket.SOCK_DGRAM) as datagram:
            try:
                service.bind(("127.0.0.1", candidate))
                datagram.bind(("0.0.0.0", candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError("No TCP/UDP test port outside platform exclusions was available.")


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
    ports: set[int] = set()
    while len(ports) < 3:
        ports.add(free_port())
    signal, tcp, udp = sorted(ports)
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


def portable_seed(root: Path) -> tuple[Path, StoreInfo, EntityRef, CanonicalId]:
    """Build/approve/seed synthetic library evidence, then restore its selective closure."""
    from ember_memory_abi.archive import (
        ContentPart,
        Document,
        ImportCursor,
        NormalizedArchiveBatch,
        ProcessingSpecRef,
    )
    from ember_memory_abi.evidence import (
        Evidence,
        EvidenceAssertion,
        SourceRef,
        make_evidence_span,
        source_text_sha256,
    )
    from ember_memory_abi.memory import (
        CandidateMemory,
        Entity,
        EntityRef,
        EpisodePayload,
        MemoryContent,
        MemoryDraft,
    )
    from ember_memory_engine.bundles import BundleService, restore_bundles
    from ember_memory_engine.library import (
        ApprovalRequest,
        KnowledgeSelection,
        LibraryService,
        SeedRequest,
    )

    stamp = "2026-10-08T00:00:00.000000Z"
    text = "My favorite color is cobalt blue."
    with CanonicalEngine(root) as engine:
        library = engine.create_library("synthetic-room-library")
        character = engine.create_character()
        subject = ensure_subject(engine, character.store_id)
        service = LibraryService(engine, authority=MutationAuthority(mode="confirmed_user"))
        origin = EntityRef(store_id=library.store_id, entity_id=uuid4())
        service.put_entity(
            library.store_id, Entity(reference=origin, revision=1, name="Synthetic user")
        )
        reference = SourceRef(
            source_store_id=library.store_id,
            record_kind="document",
            record_id=uuid4(),
            record_revision=1,
        )
        part = ContentPart(
            part_id=uuid4(),
            kind="text",
            status="available",
            text=text,
            content_sha256=source_text_sha256(text),
        )
        document = Document(source=reference, recorded_at=stamp, parts=(part,))
        engine.archive(library.store_id).write_batch(
            NormalizedArchiveBatch(
                source_store_id=library.store_id,
                source_namespace=library.namespace,
                idempotency_key="source",
                processing_spec=ProcessingSpecRef(
                    name="ember.synthetic-room", version="0.1.0", specification_sha256="a" * 64
                ),
                records=(document,),
                resume_cursor=ImportCursor(
                    logical_inventory_sha256="b" * 64,
                    component_sha256="c" * 64,
                    next_record_index=1,
                    adapter="synthetic",
                    adapter_version="0.1.0",
                ),
            )
        )
        evidence = Evidence(
            evidence_id=uuid4(),
            source=reference.model_copy(update={"part_id": part.part_id}),
            span=make_evidence_span(text, 0, len(text)),
        )
        draft = MemoryDraft(
            content=MemoryContent(
                kind="episode",
                payload=EpisodePayload(event=text, participants=(origin,)),
                modality="FACTUAL_ASSERTION",
                confidence=1.0,
                importance=0.7,
                evidence=(
                    EvidenceAssertion(
                        evidence=evidence,
                        relation="supports",
                        modality="FACTUAL_ASSERTION",
                        authority="synthetic_review",
                        verdict="confirmed",
                    ),
                ),
            )
        )
        candidate = CandidateMemory(
            candidate_id=uuid4(),
            owner_store_id=library.store_id,
            revision=1,
            draft=draft,
            recorded_at=stamp,
        )
        approved = service.approve(
            library.store_id, ApprovalRequest(idempotency_key="approve", candidate=candidate)
        )
        receipt = service.seed(
            character.store_id,
            SeedRequest(
                idempotency_key="seed",
                library_id=library.store_id,
                selections=(KnowledgeSelection(knowledge_id=approved.knowledge_id, revision=1),),
            ),
        )
        identity = receipt.affected[0].memory_id
    output = root.parent / "portable-seed-bundle"
    BundleService(root).export_character(character.store_id, output, mode="self_contained")
    restored = root.parent / "restored-data"
    restore_bundles((output,), restored)
    return restored, character, subject, identity


async def exercise_room(
    path: Path, *, real: bool, portable: bool = False, indexed: bool = False
) -> None:
    config = load_config(path, require_model_pin=True)
    config.runtime_root.mkdir(exist_ok=True)
    memory_root = config.data_root
    if portable:
        memory_root, character, subject, identity = await asyncio.to_thread(
            portable_seed, memory_root
        )
    else:
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
    encoder = None
    if indexed:
        from ember_memory_engine.embedding import BgeEncoder

        encoder = await asyncio.to_thread(
            BgeEncoder,
            Path(os.environ.get("EMBER_EMBEDDING_MODEL", ".local/models/bge-small-en-v1.5")),
        )
        with CanonicalEngine(memory_root) as index_owner:
            index_owner.rebuild_index(character.store_id, encoder)
    engine = CanonicalEngine(memory_root, encoder=encoder, auto_refresh=indexed)
    memory = InProcessAsyncMemoryClient(engine.character(character.store_id))
    author = MemoryAuthoringService(
        engine, subject, authority=MutationAuthority(mode="confirmed_user")
    )
    if indexed:
        from ember_memory_abi.interfaces import MemoryQuery

        response = await memory.search(MemoryQuery(text="preferred hue", mode="semantic"))
        assert response.coverage.index_status == "available" and response.results
        assert any("SEMANTIC_MATCH" in rank.reasons for rank in response.results[0].ranking)
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
        if indexed:
            deadline = monotonic() + 5
            while engine.index_jobs_pending and monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert engine.index_jobs_pending == 0 and engine.index_error_count == 0
            refreshed = await memory.search(MemoryQuery(text="otter", mode="lexical"))
            assert refreshed.results and refreshed.coverage.status == "complete"

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

    with ThreadingHTTPServer(("127.0.0.1", 0), OllamaFixture) as backend:
        thread = Thread(target=backend.serve_forever, name="synthetic-ollama")
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
                assert (
                    await client.remember("color", "My favorite color is cobalt blue.") == "applied"
                )
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

        thread.start()
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


@pytest.mark.integration
def test_actual_room_recalls_independent_seed_after_portable_restore(
    tmp_path: Path, livekit_executable: str
) -> None:
    path = room_config(tmp_path, livekit_executable, real=False)
    asyncio.run(exercise_room(path, real=False, portable=True))


@pytest.mark.integration
def test_actual_room_qwen_recalls_independent_seed_after_portable_restore(
    tmp_path: Path, livekit_executable: str
) -> None:
    if not os.environ.get("EMBER_REAL_CONFIG"):
        pytest.skip("Set EMBER_REAL_CONFIG for pinned real-model portable-seed acceptance.")
    path = room_config(tmp_path, livekit_executable, real=True)
    asyncio.run(exercise_room(path, real=True, portable=True))


@pytest.mark.end_to_end
def test_actual_room_qwen_uses_rebuilt_cpu_bge_index_after_portable_restore(
    tmp_path: Path,
    livekit_executable: str,
) -> None:
    if not os.environ.get("EMBER_REAL_CONFIG"):
        pytest.skip("Set EMBER_REAL_CONFIG for the pinned Qwen indexed room acceptance.")
    directory = Path(os.environ.get("EMBER_EMBEDDING_MODEL", ".local/models/bge-small-en-v1.5"))
    if not (directory / "embedding.json").is_file():
        pytest.skip("Install the pinned CPU BGE assets for indexed room acceptance.")
    path = room_config(tmp_path, livekit_executable, real=True)
    asyncio.run(exercise_room(path, real=True, portable=True, indexed=True))


def test_test_port_allocator_retries_occupied_tcp_port(monkeypatch: pytest.MonkeyPatch) -> None:
    with socket.socket() as occupied:
        # Reserve a user port, then make the allocator encounter that collision
        # before the independently probed free TCP/UDP candidate.
        for _ in range(100):
            blocked = 10000 + secrets.randbelow(35000)
            try:
                occupied.bind(("127.0.0.1", blocked))
                break
            except OSError:
                continue
        else:
            pytest.fail("Could not reserve a test user port.")
        candidate = free_port()
        values = iter((blocked - 10000, candidate - 10000))
        monkeypatch.setattr(secrets, "randbelow", lambda limit: next(values))
        assert free_port() == candidate


@pytest.mark.parametrize("discovery", [None, b"not-json", b"{}"])
def test_participant_discovery_failure_has_actionable_private_safe_diagnostic(
    tmp_path: Path,
    discovery: bytes | None,
) -> None:
    from ember_agent.runtime.livekit_runtime import run_participant

    config = load_config(EXAMPLE).model_copy(update={"runtime_root": tmp_path})
    if discovery is not None:
        (tmp_path / "livekit-room.json").write_bytes(discovery)
    with pytest.raises(ConfigurationError) as captured:
        asyncio.run(run_participant(config))
    message = str(captured.value)
    assert "ember start" in message and "same --config" in message
    assert str(tmp_path) not in message
    assert "not-json" not in message
    assert not (tmp_path / "gateway.token").exists()
