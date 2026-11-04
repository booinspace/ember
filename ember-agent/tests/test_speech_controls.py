"""Session-only speech controls preserve authenticated ownership and turn stability."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ember_agent.audio.components import VoiceComponents
from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayError
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.realtime.session import HostedRoom
from ember_agent.runtime.browser_control import BrowserControl
from ember_agent.runtime.speech_settings import SpeechOptions, SpeechSelection
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_character import FakeModel
from .test_phase_eight_browser import ORIGIN, Controls


def test_read_ahead_and_authenticated_voice_changes_retain_turn_boundaries(tmp_path: Path) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            identity = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(identity))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
            binding = RoomBinding(
                instance_id=uuid4(),
                character_id=identity,
                room="ember-" + uuid4().hex,
                endpoint="ws://127.0.0.1:7880",
            )
            options = SpeechOptions(
                model="gradium/phonon-31m-en",
                voices=("default", "Juniper"),
                selection=SpeechSelection(voice="default"),
            )
            provider = Mock()
            voice = VoiceComponents(
                Mock(), Mock(), Mock(), provider, "ready", speech_options=options
            )
            host = HostedRoom(core, binding, AsyncMock(return_value="unused"))
            host.voice = voice
            deep = DeepRecallService(identity, lambda _: memory, validation=memory)
            owner = BrowserControl(
                origin=ORIGIN,
                assets=tmp_path,
                binding=binding,
                core=core,
                deep=deep,
                controls=Controls(),
                room_token=lambda: "unused",
                answer=AsyncMock(return_value="unused"),
                speech_options=lambda: voice.speech_options,
                configure_speech=host.configure_speech,
            )
            with TestClient(owner.app, base_url=ORIGIN, headers={"origin": ORIGIN}) as client:
                selection = {"voice": "Juniper", "speed": 1.25}
                assert client.post("/api/speech", json=selection).status_code == 401
                client.post("/api/bootstrap", json={"token": owner.activation})
                assert (
                    client.post(
                        "/api/speech", json=selection, headers={"origin": "https://other.invalid"}
                    ).status_code
                    == 403
                )
                assert client.post("/api/speech", json={**selection, "speed": 2}).status_code == 400
                assert (
                    client.post("/api/speech", json={**selection, "voice": "missing"}).status_code
                    == 409
                )
                host.busy = True
                assert (
                    client.post("/api/speech", json=selection).json()["error"]
                    == "speech_settings_busy"
                )
                host.busy = False
                assert client.post("/api/speech", json=selection).json()["selection"] == selection
                assert client.get("/api/state").json()["speech"]["selection"] == selection
                provider.update_options.assert_called_once_with(**selection)
            assert not core.history

            async def verify_start() -> None:
                host.room = SimpleNamespace(
                    connect=AsyncMock(), local_participant=Mock(), on=Mock()
                )
                host.session.start = AsyncMock()
                host._start_permission_watch = Mock()
                await host.start("unused")
                room_options = host.session.start.call_args.kwargs["room_options"]
                assert room_options.audio_output is not False
                assert room_options.text_output.sync_transcription is True
                host.closing = True
                with pytest.raises(GatewayError, match="speech_controls_unavailable"):
                    host.configure_speech(SpeechSelection(voice="default"))
                await owner.aclose()
                await deep.aclose()
                await memory.aclose()

            await verify_start()

    asyncio.run(exercise())


def test_read_ahead_stream_is_immediate_validated_and_closed_on_revocation(tmp_path: Path) -> None:
    from ember_agent.realtime.livekit import EmberAgent

    with CanonicalEngine(tmp_path / "data") as engine:
        identity = engine.create_character().store_id
        memory = InProcessAsyncMemoryClient(engine.character(identity))

        async def exercise() -> None:
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
            agent = EmberAgent(core, defer_delivery=True)
            turn = await core.prepare("Synthetic question.")
            agent.gateway_llm.active_turn = turn
            writer = SimpleNamespace(write=AsyncMock(), aclose=AsyncMock())
            participant = SimpleNamespace(stream_text=AsyncMock(return_value=writer))
            agent.reply_participant = participant

            async def chunks() -> AsyncIterator[str]:
                yield "Read this before playback finishes."
                yield "This suffix is revoked."

            stream = agent.transcription_node(chunks(), {})
            assert await anext(stream) == "Read this before playback finishes."
            writer.write.assert_awaited_once_with("Read this before playback finishes.")
            assert not core.history[0].assistant and core.pending_delivery is None
            core.current = AsyncMock(return_value=False)
            with pytest.raises(GatewayError, match="context_invalidated"):
                await anext(stream)
            writer.aclose.assert_awaited_once()
            writer.write.assert_awaited_once()
            await memory.aclose()

        asyncio.run(exercise())
