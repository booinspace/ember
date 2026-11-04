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
            assert all(not old.assistant for old in core.history)
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
            assert core.history[0].assistant == text[:9] and not core.history[0].complete
            assert core.pending_delivery is None
            assert host.agent.gateway_llm.active_turn is None
            await client.aclose()

        asyncio.run(exercise())


def test_voice_turn_recovers_when_completed_speech_has_no_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            first = await core.prepare("Synthetic first voice question.")
            host.agent.gateway_llm.active_turn = first
            _ = [event async for event in core.stream(first, defer_delivery=True)]
            assert core.pending_delivery is not None

            class Speech:
                _tasks: list[asyncio.Task[object]] = []

                async def wait_for_playout(self) -> None:
                    pass

            monkeypatch.setattr(type(host.session), "current_speech", property(lambda _: Speech()))
            context = llm.ChatContext()
            message = context.add_message(role="user", content="Synthetic next question.")
            await host.agent.on_user_turn_completed(context, message)
            assert core.pending_delivery is None
            assert all(not old.assistant for old in core.history)
            assert host.agent.gateway_llm.prepared is not None
            assert host.agent.gateway_llm.prepared[1].user == "Synthetic next question."
            await client.aclose()

        asyncio.run(exercise())


def test_generation_retains_the_current_livekit_speech_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livekit.agents.voice.speech_handle import SpeechHandle

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
            speech = SpeechHandle.create()
            monkeypatch.setattr(type(host.session), "current_speech", property(lambda _: speech))
            context = llm.ChatContext()
            message = context.add_message(role="user", content="Synthetic voice question.")
            await host.agent.prepare_turn(message)
            stream = host.agent.gateway_llm.chat(chat_ctx=context)
            try:
                assert host._turn_speech is speech
            finally:
                await stream.aclose()
                await client.aclose()

        asyncio.run(exercise())


def test_voice_turn_does_not_discard_without_a_speech_completion_fence(tmp_path: Path) -> None:
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
            first = await core.prepare("Synthetic first voice question.")
            host.agent.gateway_llm.active_turn = first
            _ = [event async for event in core.stream(first, defer_delivery=True)]
            with pytest.raises(GatewayError, match="livekit_delivery_unavailable"):
                await host._before_turn()
            assert core.pending_delivery is not None
            assert host.agent.gateway_llm.active_turn is first
            assert all(not old.assistant for old in core.history)
            await client.aclose()

        asyncio.run(exercise())


def test_forced_speech_completion_waits_for_linked_sdk_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livekit.agents.voice.speech_handle import SpeechHandle

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
            first = await core.prepare("Synthetic first voice question.")
            host.agent.gateway_llm.active_turn = first
            output = "".join(
                [event.text async for event in core.stream(first, defer_delivery=True)]
            )
            release = asyncio.Event()
            speech = SpeechHandle.create()

            async def sdk_task() -> None:
                await release.wait()
                host._conversation_item(
                    ConversationItemAddedEvent(
                        item=llm.ChatMessage(role="assistant", content=[output], interrupted=False)
                    )
                )

            linked = asyncio.create_task(sdk_task())
            speech._tasks.append(linked)
            speech._mark_done()  # SDK timeout can do this before linked tasks terminate.
            host._turn_speech = speech
            monkeypatch.setattr(type(host.session), "current_speech", property(lambda _: None))
            context = llm.ChatContext()
            message = context.add_message(role="user", content="Synthetic next question.")
            pending = asyncio.create_task(host.agent.on_user_turn_completed(context, message))
            await asyncio.sleep(0.01)
            assert not pending.done() and core.pending_delivery is not None
            release.set()
            await pending
            assert len(core.history) == 2 and core.history[0].assistant == output
            assert core.pending_delivery is None
            await client.aclose()

        asyncio.run(exercise())


def test_receipt_does_not_release_unfinished_sdk_speech_task(tmp_path: Path) -> None:
    from livekit.agents.voice.speech_handle import SpeechHandle

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
            first = await core.prepare("Synthetic first voice question.")
            host.agent.gateway_llm.active_turn = first
            output = "".join(
                [event.text async for event in core.stream(first, defer_delivery=True)]
            )
            release = asyncio.Event()
            speech = SpeechHandle.create()
            linked = asyncio.create_task(release.wait())
            speech._tasks.append(linked)
            speech._mark_done()
            host._turn_speech = speech
            host._conversation_item(
                ConversationItemAddedEvent(
                    item=llm.ChatMessage(role="assistant", content=[output], interrupted=False)
                )
            )
            await host._drain_delivery()
            assert core.pending_delivery is None and host.agent.gateway_llm.active_turn is None
            context = llm.ChatContext()
            message = context.add_message(role="user", content="Synthetic next question.")
            pending = asyncio.create_task(host.agent.on_user_turn_completed(context, message))
            await asyncio.sleep(0.01)
            assert not pending.done()
            release.set()
            await pending
            assert host._turn_speech is None
            assert len(core.history) == 2 and core.history[0].assistant == output
            await client.aclose()

        asyncio.run(exercise())


@pytest.mark.parametrize("interrupted", [False, True])
def test_new_voice_turn_waits_for_playout_and_preserves_late_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        client = InProcessAsyncMemoryClient(engine.character(character))

        async def remember(_: RememberInput) -> str:
            return "unused"

        async def exercise() -> None:
            class Sink:
                def __init__(self) -> None:
                    self.deliveries: list[tuple[str, bool]] = []

                async def begin_input(
                    self, context_id: object, request_id: object, observed_at: str, user: str
                ) -> None:
                    pass

                async def commit_delivery(
                    self,
                    context_id: object,
                    request_id: object,
                    observed_at: str,
                    delivered: str,
                    complete: bool,
                ) -> None:
                    self.deliveries.append((delivered, complete))

            sink = Sink()
            core = CharacterCore(client, FakeModel(), model_tag="synthetic", sink=sink)
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
            first = await core.prepare("Synthetic first voice question.")
            host.agent.gateway_llm.active_turn = first
            output = "".join(
                [event.text async for event in core.stream(first, defer_delivery=True)]
            )
            host.agent.gateway_llm.generation_done.clear()
            release = asyncio.Event()
            entered = asyncio.Event()

            class Speech:
                _tasks: list[asyncio.Task[object]] = []

                async def wait_for_playout(self) -> None:
                    entered.set()
                    await release.wait()

            monkeypatch.setattr(type(host.session), "current_speech", property(lambda _: Speech()))
            context = llm.ChatContext()
            message = context.add_message(role="user", content="Synthetic next question.")
            pending = asyncio.create_task(host.agent.on_user_turn_completed(context, message))
            await asyncio.wait_for(entered.wait(), 1)
            assert not pending.done() and core.pending_delivery is not None
            heard = output[:9] if interrupted else output
            host._conversation_item(
                ConversationItemAddedEvent(
                    item=llm.ChatMessage(role="assistant", content=[heard], interrupted=interrupted)
                )
            )
            release.set()
            await asyncio.sleep(0)
            assert not pending.done() and core.pending_delivery is not None
            host.agent.gateway_llm.generation_done.set()
            await pending
            assert core.pending_delivery is None
            assert sink.deliveries == [(heard, not interrupted)]
            assert (
                len(core.history) == 2
            )  # Previous delivery plus independently observed next input.
            assert core.history[0].assistant == heard
            assert core.history[0].complete is not interrupted
            assert host.agent.gateway_llm.prepared is not None
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


def test_missing_installed_stt_does_not_launch_managed_tts(tmp_path: Path) -> None:
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
                    "tts": config.services.tts.model_copy(update={"mode": "managed"}),
                }
            ),
        }
    )
    speech = AsyncMock(return_value="ready")
    result, status = asyncio.run(VoiceComponents.create(config, start_speech=speech))
    assert result is None and status == "stt_artifacts_unavailable"
    speech.assert_not_awaited()


@pytest.mark.parametrize(
    "seconds, expected_seconds",
    [(30.5, [30.0, 0.5]), (75.0, [30.0, 30.0, 15.0]), (300.5, [30.0] * 10 + [0.5])],
)
def test_long_speech_is_transcribed_in_order_without_losing_audio(
    seconds: float, expected_seconds: list[float]
) -> None:
    from ember_agent.audio.whisper import BoundedCpuSTT

    class Recorder(BoundedCpuSTT):
        def __init__(self) -> None:
            super().__init__()
            self.sizes: list[int] = []

        def transcribe(self, pcm: bytes, language: str) -> str:
            assert language == "en"
            self.sizes.append(len(pcm))
            return f"part{len(self.sizes)}"

    async def exercise() -> None:
        provider = Recorder()
        samples = int(seconds * 16000)
        frame = rtc.AudioFrame(
            data=bytes(samples * 2),
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=samples,
        )
        try:
            event = await provider._recognize_impl(frame, conn_options=APIConnectOptions())
            assert provider.sizes == [int(value * 32000) for value in expected_seconds]
            assert event.alternatives[0].text == " ".join(
                f"part{index}" for index in range(1, len(expected_seconds) + 1)
            )
            assert event.alternatives[0].end_time == seconds
        finally:
            await provider.aclose()

    asyncio.run(exercise())


def test_speech_audio_beyond_the_bounded_envelope_is_rejected() -> None:
    from ember_agent.audio.whisper import BoundedCpuSTT

    class Recorder(BoundedCpuSTT):
        def transcribe(self, pcm: bytes, language: str) -> str:
            raise AssertionError("oversized input must not reach the provider")

    async def exercise() -> None:
        provider = Recorder()
        samples = int(300.5 * 16000) + 1
        frame = rtc.AudioFrame(
            data=bytes(samples * 2),
            sample_rate=16000,
            num_channels=1,
            samples_per_channel=samples,
        )
        try:
            with pytest.raises(GatewayError, match="stt_audio_bound"):
                await provider._recognize_impl(frame, conn_options=APIConnectOptions())
        finally:
            await provider.aclose()

    asyncio.run(exercise())
