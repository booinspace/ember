"""Degraded HTTP text uses canonical capture, delivery receipts and connection admission."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from ember_agent.character.core import CharacterCore, TurnObservation
from ember_agent.inference.contracts import GatewayError
from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_agent.memory.deep_contracts import DeepRecallResult
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.runtime.browser_control import BrowserControl
from ember_agent.runtime.capture_lifecycle import CaptureLifecycle
from ember_agent.runtime.config import ConfigurationError, load_config
from ember_agent.runtime.livekit_runtime import RuntimeServices, run_runtime
from ember_agent.runtime.text_session import DirectTextSession
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import ensure_subject
from ember_memory_engine.capture import CaptureService

from .test_character import FakeModel
from .test_livekit_runtime import EXAMPLE


@pytest.mark.parametrize("model_available", [False, True])
def test_degraded_startup_selects_browser_without_starting_room(
    tmp_path: Path, model_available: bool
) -> None:
    config = load_config(EXAMPLE).model_copy(
        update={
            "data_root": tmp_path / "data",
            "runtime_root": tmp_path / "runtime",
            "system_prompt_file": tmp_path / "prompts/system.md",
            "browser": load_config(EXAMPLE).browser.model_copy(update={"enabled": True}),
        }
    )
    config.runtime_root.mkdir()
    if model_available:
        (config.runtime_root / "gateway.token").write_text("synthetic", encoding="utf-8")
    gateway = AsyncMock(side_effect=None if model_available else ConfigurationError("synthetic"))
    fallback = AsyncMock()
    with (
        patch.object(
            RuntimeServices,
            "start",
            AsyncMock(side_effect=ConfigurationError("LiveKit unavailable")),
        ),
        patch.object(RuntimeServices, "start_gateway", gateway),
        patch("ember_agent.runtime.livekit_runtime.HttpGatewayClient.session", AsyncMock()),
        patch("ember_agent.runtime.livekit_runtime._run_room", AsyncMock()) as room,
        patch("ember_agent.runtime.livekit_runtime._run_text_browser", fallback),
    ):
        asyncio.run(run_runtime(config, tmp_path / "deployment.toml"))
    room.assert_not_called()
    assert fallback.await_args.kwargs["gateway_available"] == model_available
    assert not (config.runtime_root / "livekit-room.json").exists()


def test_authenticated_http_turn_requires_delivery_receipt_and_blocks_background(
    tmp_path: Path,
) -> None:
    from unittest.mock import Mock

    from ember_agent.runtime.controls import CaptureControl, ProcessingStatus, RuntimeControls
    from ember_memory_abi.selection import RecallSelection

    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            sink = ArchiveDialogueSink(engine.archive(character), ensure_subject(engine, character))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic", sink=sink)
            deep = DeepRecallService(character, lambda _: memory, validation=memory)

            async def investigate(query: str, observation: TurnObservation) -> DeepRecallResult:
                assert query == "NAS" and observation.text == "/recall NAS"
                return DeepRecallResult(
                    request_id=observation.request_id,
                    character_id=character,
                    context_id=core.context_id,
                    query=query,
                    outcome="deferred",
                    reasons=("deep_deferred_for_foreground",),
                )

            text = DirectTextSession(core, deep, investigate, model_available=True)
            reserved = []
            capture_calls = []

            async def reserve(_: object, connected: bool) -> None:
                reserved.append(connected)

            async def consolidate(_: object) -> int:
                capture_calls.append(True)
                return 0

            lifecycle = CaptureLifecycle(uuid4(), reserve, text.quiesce, consolidate)
            text.before_turn = lifecycle.before_turn
            text.connection_changed = lifecycle.connection_changed
            lifecycle.connection_changed(True)
            lifecycle.start()
            controls = Mock(spec=RuntimeControls)
            controls.recall_settings = AsyncMock(return_value=RecallSelection())
            controls.capture_settings = AsyncMock(
                return_value=CaptureControl(enabled=True, revision=0)
            )
            controls.libraries = AsyncMock(return_value=())
            controls.processing_status = AsyncMock(
                return_value=ProcessingStatus(counts={}, failure_codes=(), index_status="missing")
            )
            origin = "http://127.0.0.1:8787"
            owner = BrowserControl(
                origin=origin,
                assets=tmp_path / "unbuilt",
                binding=RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                core=core,
                deep=deep,
                controls=controls,
                room_token=lambda: "unused",
                answer=AsyncMock(),
                text_session=text,
            )
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=owner.app),
                    base_url=origin,
                    headers={"origin": origin},
                ) as client:
                    assert (await client.post("/api/text/connect", json={})).status_code == 401
                    await client.post("/api/bootstrap", json={"token": owner.activation})
                    state = (await client.get("/api/state")).json()
                    assert state["transport"] == "http" and state["text_available"]
                    identity = (await client.post("/api/text/connect", json={})).json()[
                        "session_id"
                    ]
                    assert (await client.post("/api/text/connect", json={})).status_code == 409
                    request_id = str(uuid4())
                    body = {
                        "session_id": identity,
                        "request_id": request_id,
                        "text": "Milo is my shark plushie.",
                    }
                    response = await client.post("/api/text/turn", json=body)
                    assert (
                        response.status_code == 200
                        and response.json()["text"] == "Synthetic visible reply"
                    )
                    assert core.history[0].assistant == "" and core.pending_delivery is not None
                    assert len(CaptureService(engine, character).pending()) == 1
                    assert (await client.post("/api/text/turn", json=body)).status_code == 409
                    receipt = {"session_id": identity, "request_id": request_id}
                    assert (
                        await client.post("/api/text/acknowledge", json=receipt)
                    ).status_code == 200
                    assert len(core.history) == 1
                    assert (await client.post("/api/text/turn", json=body)).status_code == 409
                    assert not capture_calls and reserved[-1]
                    deep_id = str(uuid4())
                    deep_reply = await client.post(
                        "/api/text/turn",
                        json={"session_id": identity, "request_id": deep_id, "text": "/recall NAS"},
                    )
                    assert deep_reply.status_code == 200 and "deferred" in deep_reply.json()["text"]
                    assert (
                        await client.post(
                            "/api/text/acknowledge",
                            json={"session_id": identity, "request_id": deep_id},
                        )
                    ).status_code == 200
                    body["request_id"] = str(uuid4())
                    body["text"] = "A second synthetic question."
                    assert (await client.post("/api/text/turn", json=body)).status_code == 200
                    entered, release = asyncio.Event(), asyncio.Event()
                    original = sink.commit_delivery

                    async def blocked_receipt(*args: object) -> None:
                        entered.set()
                        await release.wait()
                        await original(*args)

                    with patch.object(sink, "commit_delivery", blocked_receipt):
                        acknowledging = asyncio.create_task(
                            client.post(
                                "/api/text/acknowledge",
                                json={"session_id": identity, "request_id": body["request_id"]},
                            )
                        )
                        await asyncio.wait_for(entered.wait(), 2)
                        disconnecting = asyncio.create_task(
                            client.post("/api/text/disconnect", json={"session_id": identity})
                        )
                        await asyncio.sleep(0)
                        release.set()
                        assert (await acknowledging).status_code == 200
                        assert (await disconnecting).status_code == 200
                    assert len(core.history) == 3 and core.pending_delivery is None
                    async with asyncio.timeout(2):
                        while not capture_calls:
                            await asyncio.sleep(0)
                    assert not reserved[-1]
            finally:
                await owner.aclose()
                await lifecycle.aclose()
                await deep.aclose()
                await sink.aclose()
                await memory.aclose()

    asyncio.run(exercise())


def test_expired_browser_lease_cancels_and_drains_unheard_generation(tmp_path: Path) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            model = FakeModel()
            model.release.clear()
            core = CharacterCore(memory, model, model_tag="synthetic")
            deep = DeepRecallService(character, lambda _: memory, validation=memory)
            text = DirectTextSession(core, deep, AsyncMock(), model_available=True, lease_seconds=1)
            text.before_turn = AsyncMock()
            text.connection_changed = lambda _: None
            identity = await text.connect()
            task = asyncio.create_task(text.turn(identity, uuid4(), "Synthetic question"))
            async with asyncio.timeout(2):
                while not model.requests:
                    await asyncio.sleep(0)
            with pytest.raises(GatewayError, match="character_awaiting_delivery"):
                await text.turn(identity, uuid4(), "Overlapping question")
            with pytest.raises(asyncio.CancelledError):
                await task
            await text.aclose()
            assert not core.busy and core.pending_delivery is None
            assert core.history[0].user == "Synthetic question" and not core.history[0].assistant
            assert text.lease.done()
            with pytest.raises(GatewayError):
                text.heartbeat(identity)
            await deep.aclose()
            await memory.aclose()

    asyncio.run(exercise())


def test_real_browser_control_listener_survives_unavailable_livekit_and_gateway(
    tmp_path: Path,
) -> None:
    import json
    import socket

    async def exercise() -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        original = load_config(EXAMPLE)
        config = original.model_copy(
            update={
                "data_root": tmp_path / "data",
                "runtime_root": tmp_path / "runtime",
                "system_prompt_file": tmp_path / "prompts/system.md",
                "browser": original.browser.model_copy(
                    update={
                        "enabled": True,
                        "endpoint": f"http://127.0.0.1:{port}",
                        "assets": tmp_path / "unbuilt",
                    }
                ),
            }
        )
        stop = asyncio.Event()
        activation = config.runtime_root / "browser-activation.json"
        with (
            patch.object(
                RuntimeServices,
                "start",
                AsyncMock(side_effect=ConfigurationError("LiveKit unavailable")),
            ),
            patch.object(
                RuntimeServices,
                "start_gateway",
                AsyncMock(side_effect=ConfigurationError("Gateway unavailable")),
            ),
        ):
            task = asyncio.create_task(run_runtime(config, tmp_path / "deployment.toml", stop=stop))
            try:
                async with asyncio.timeout(5):
                    while not activation.exists():
                        if task.done():
                            await task
                        await asyncio.sleep(0.01)
                token = json.loads(activation.read_text(encoding="utf-8"))["url"].split(
                    "#activate=", 1
                )[1]
                async with httpx.AsyncClient(
                    base_url=config.browser.endpoint,
                    headers={"origin": config.browser.endpoint},
                    trust_env=False,
                ) as client:
                    assert (
                        await client.post("/api/bootstrap", json={"token": token})
                    ).status_code == 200
                    response = await client.get("/api/state")
                    assert response.status_code == 200
                    assert response.json()["transport"] == "http"
                    assert not response.json()["text_available"]
                    assert (await client.post("/api/text/connect", json={})).status_code == 409
            finally:
                stop.set()
                await asyncio.wait_for(task, 5)
            assert not activation.exists()

    asyncio.run(exercise())
