"""Synthetic CPU speech and delivery ownership need neither CUDA nor downloaded speech models."""

import asyncio
import threading
from array import array
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from livekit import api, rtc
from livekit.agents import APIConnectOptions, ConversationItemAddedEvent, llm

from ember_agent.audio.whisper import CpuWhisperSTT
from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayError
from ember_agent.realtime.contracts import RememberInput, RoomBinding
from ember_agent.realtime.session import HostedRoom
from ember_agent.runtime.livekit_config import LiveKitCredentials
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_character import FakeModel


def test_cancelled_stt_keeps_native_admission_until_worker_finishes() -> None:
    entered = threading.Event()
    released = threading.Event()

    class Transcriber:
        def transcribe(
            self, audio: object, **settings: object
        ) -> tuple[tuple[SimpleNamespace, ...], SimpleNamespace]:
            assert settings["beam_size"] == 1
            assert settings["condition_on_previous_text"] is False
            entered.set()
            assert released.wait(5)
            return (SimpleNamespace(text=" summer 2026", start=0, end=1),), SimpleNamespace(
                language="en"
            )

    async def exercise() -> None:
        provider = CpuWhisperSTT(Transcriber())
        frame = rtc.AudioFrame(
            data=array("h", [0] * 1600).tobytes(),
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=1600,
        )
        request = asyncio.create_task(
            provider._recognize_impl(frame, conn_options=APIConnectOptions(max_retry=0, timeout=30))
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            with pytest.raises(GatewayError, match="stt_busy"):
                await provider._recognize_impl(frame, conn_options=APIConnectOptions())
            assert provider.pending is not None and not provider.pending.done()
        finally:
            released.set()
            await provider.aclose()
        assert provider.closed and provider.pending.done()
        await provider.aclose()  # Closing an already drained provider is harmless.

    asyncio.run(exercise())


def test_speech_media_tokens_cannot_publish_video_or_administer_rooms() -> None:
    value = LiveKitCredentials(api_key="synthetic-key", api_secret="x" * 40)
    claim = api.TokenVerifier(value.api_key, value.api_secret).verify(
        value.token("room", "ember-user", audio=True)
    )
    assert claim.video.can_publish
    assert claim.video.can_publish_sources == ["microphone"]
    assert not claim.video.room_admin and not claim.video.room_create


def test_sdk_receipt_archives_only_played_prefix_without_sdk_id_assumptions(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        client = InProcessAsyncMemoryClient(engine.character(character))

        async def remember(_: RememberInput) -> str:
            return "unused"

        async def exercise() -> None:
            core = CharacterCore(client, FakeModel(), model_tag="synthetic")
            host = HostedRoom(
                core,
                RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                remember,
            )
            turn = await core.prepare("Synthetic voice question.")
            host.agent.gateway_llm.active_turn = turn
            text = "".join([event.text async for event in core.stream(turn, defer_delivery=True)])
            assert not core.history
            host._conversation_item(
                ConversationItemAddedEvent(
                    item=llm.ChatMessage(
                        id="sdk-id-independent-of-gateway-uuid",
                        role="assistant",
                        content=[text[:9]],
                        interrupted=True,
                    )
                )
            )
            await host._drain_delivery()
            assert not core.history and core.pending_delivery is None
            assert host.agent.gateway_llm.active_turn is None
            await client.aclose()

        asyncio.run(exercise())


def test_permission_validator_failure_closes_output_and_rejects_new_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock, Mock

    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        client = InProcessAsyncMemoryClient(engine.character(character))

        async def remember(_: RememberInput) -> str:
            return "unused"

        async def exercise() -> None:
            core = CharacterCore(client, FakeModel(), model_tag="synthetic")
            host = HostedRoom(
                core,
                RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                remember,
            )
            turn = await core.prepare("Synthetic voice question.")
            host.agent.gateway_llm.active_turn = turn
            validator = AsyncMock(side_effect=RuntimeError("synthetic validator unavailable"))
            monkeypatch.setattr(core, "current", validator)
            disable = Mock()
            close = AsyncMock()
            disconnect = AsyncMock()
            monkeypatch.setattr(host.session.input, "set_audio_enabled", disable)
            monkeypatch.setattr(host.session, "aclose", close)
            monkeypatch.setattr(host.room, "disconnect", disconnect)
            await asyncio.wait_for(host._watch_permissions(), 1)
            assert host.permission_failure and host.delivery_failed and host.closing
            disable.assert_called_once_with(False)
            close.assert_awaited_once()
            disconnect.assert_awaited_once()
            with pytest.raises(GatewayError, match="livekit_delivery_unavailable"):
                await host._before_turn()
            await client.aclose()

        asyncio.run(exercise())


def test_cancelled_speech_startup_drains_loaded_cpu_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock, Mock

    from ember_agent.audio.components import VoiceComponents
    from ember_agent.runtime.config import load_config

    from .test_livekit_runtime import EXAMPLE

    entered, release = threading.Event(), threading.Event()
    loaded: list[CpuWhisperSTT] = []

    def load(directory: Path, *, threads: int, language: str) -> CpuWhisperSTT:
        entered.set()
        assert release.wait(5)
        provider = CpuWhisperSTT(Mock())
        loaded.append(provider)
        return provider

    monkeypatch.setattr(CpuWhisperSTT, "load", load)
    monkeypatch.setattr("ember_agent.audio.components.silero.VAD.load", lambda **settings: Mock())
    config = load_config(EXAMPLE)
    config = config.model_copy(
        update={
            "audio": config.audio.model_copy(
                update={
                    "enabled": True,
                    "stt_model_directory": tmp_path,
                }
            ),
        }
    )
    speech = AsyncMock(return_value="ready")

    async def exercise() -> None:
        starting = asyncio.create_task(VoiceComponents.create(config, start_speech=speech))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            starting.cancel()
            await asyncio.sleep(0)
            assert not starting.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert len(loaded) == 1 and loaded[0].closed
        speech.assert_not_awaited()

    asyncio.run(exercise())


def test_missing_installed_stt_does_not_launch_managed_kokoro(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock

    from ember_agent.audio.components import VoiceComponents
    from ember_agent.runtime.config import load_config

    from .test_livekit_runtime import EXAMPLE

    config = load_config(EXAMPLE)
    config = config.model_copy(
        update={
            "audio": config.audio.model_copy(
                update={
                    "enabled": True,
                    "stt_model_directory": tmp_path / "missing",
                }
            ),
            "services": config.services.model_copy(
                update={
                    "kokoro": config.services.kokoro.model_copy(update={"mode": "managed"}),
                }
            ),
        }
    )
    speech = AsyncMock(return_value="ready")
    result, status = asyncio.run(VoiceComponents.create(config, start_speech=speech))
    assert result is None and status == "stt_artifacts_unavailable"
    speech.assert_not_awaited()
