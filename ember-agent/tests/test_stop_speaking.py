"""A room-scoped stop fences played-prefix receipts without dropping the microphone."""

import asyncio
import sqlite3
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from livekit import rtc
from livekit.agents import APIConnectOptions, ConversationItemAddedEvent, llm

from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayError
from ember_agent.realtime.contracts import (
    TASK_NOTICE_TEXT,
    RoomBinding,
    SpeechTarget,
    StopSpeakingResult,
)
from ember_agent.realtime.livekit import EmberAgent
from ember_agent.realtime.session import HostedRoom
from ember_agent.runtime.task_router import (
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolRouter,
)
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_character import FakeModel


@pytest.mark.parametrize("stop_notice", [False, True])
def test_notice_identity_precedes_pcm_and_stop_never_touches_p0_delivery(
    tmp_path: Path, stop_notice: bool
) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "memory") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
            host = HostedRoom(
                core,
                RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                AsyncMock(),
            )
            host._speech_control_peer = True
            router = TaskRouter(
                tmp_path / "tasks",
                character,
                core.context_id,
                TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
            )
            await router.start()
            signalled, acknowledge, pcm = asyncio.Event(), asyncio.Event(), asyncio.Event()
            finish_targets: list[SpeechTarget] = []

            async def rpc(**kwargs: object) -> str:
                target = SpeechTarget.model_validate_json(kwargs["payload"])
                if kwargs["method"] == "ember.reply_started":
                    signalled.set()
                    await acknowledge.wait()
                else:
                    assert kwargs["method"] == "ember.speech_finished"
                    finish_targets.append(target)
                return str(target.request_id)

            class Speech:
                def __init__(self, source: AsyncIterable[str]) -> None:
                    self.interrupted = False
                    self.done, self.played = asyncio.Event(), asyncio.Event()
                    self._tasks = [asyncio.create_task(self.run(source))]

                async def run(self, source: AsyncIterable[str]) -> None:
                    try:
                        assert [part async for part in source] == [
                            "A task result is ready in Tasks."
                        ]
                        pcm.set()
                        await self.done.wait()
                    finally:
                        self.played.set()

                async def wait_for_playout(self) -> None:
                    await self.played.wait()

                def interrupt(self, *, force: bool) -> None:
                    assert force
                    self.interrupted = True
                    self.done.set()

                def exception(self) -> None:
                    return None

            speech: Speech | None = None

            def say(source: AsyncIterable[str], **settings: object) -> Speech:
                nonlocal speech
                assert settings == {"allow_interruptions": True, "add_to_chat_ctx": False}
                speech = Speech(source)
                return speech

            host.room = SimpleNamespace(local_participant=SimpleNamespace(perform_rpc=rpc))
            host.session = SimpleNamespace(say=say, interrupt=Mock())
            host._can_announce_tasks = lambda: host._announcement is None
            worker = asyncio.create_task(host._announce_tasks(router))
            try:
                task = await router.submit("notice", "Synthetic public text")
                await asyncio.wait_for(signalled.wait(), 5)
                assert not pcm.is_set()
                notice = host._announcement
                assert notice is not None and speech is not None
                assert notice.identity != task.task_id
                acknowledge.set()
                await asyncio.wait_for(pcm.wait(), 2)
                # A notice stop must not consume a P0 receipt or join its generation.
                p0 = await core.prepare("An unrelated current conversation turn.")
                _ = [event async for event in core.stream(p0, defer_delivery=True)]
                pending = core.pending_delivery
                host.agent.gateway_llm.active_turn = p0
                host.agent.gateway_llm.generation_done.clear()
                if stop_notice:
                    target = host._speech_target(notice.identity)
                    first = asyncio.create_task(host._stop_speaking(invocation(target)))
                    second = asyncio.create_task(host._stop_speaking(invocation(target)))
                    async with asyncio.timeout(2):
                        result, duplicate = await asyncio.gather(first, second)
                    assert result == duplicate
                    assert StopSpeakingResult.model_validate_json(result).outcome == "stopped"
                    assert speech.interrupted
                else:
                    speech.done.set()
                    await asyncio.wait_for(notice.finished.wait(), 2)
                assert core.pending_delivery is pending
                assert host.agent.gateway_llm.active_turn is p0
                assert not host.agent.gateway_llm.generation_done.is_set()
                host.session.interrupt.assert_not_called()
                assert host._announcement is None
                assert [value.request_id for value in finish_targets] == [notice.identity]
                assert await router.store.claim_speech(character, core.context_id) is None
                with closing(sqlite3.connect(router.store.path)) as db:
                    state = db.execute(
                        "SELECT state FROM task_speech_delivery WHERE task_id=?",
                        (str(task.task_id),),
                    ).fetchone()
                assert state == ("uncertain" if stop_notice else "completed",)
                assert (await router.notifications(None)).items[0].task_id == task.task_id
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                await router.aclose()
                await memory.aclose()

    asyncio.run(exercise())


def test_unverified_notice_drain_fences_subsequent_p0(tmp_path: Path) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "memory") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
            host = HostedRoom(
                core,
                RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                AsyncMock(),
            )
            notice = SimpleNamespace(identity=uuid4(), speech=Mock(), finished=asyncio.Event())
            host._announcement = notice
            host._settle_speech = AsyncMock(
                side_effect=GatewayError("livekit_delivery_unavailable")
            )
            router = SimpleNamespace(
                store=SimpleNamespace(finish_speech=AsyncMock()), session_id=uuid4()
            )
            with pytest.raises(GatewayError, match="livekit_delivery_unavailable"):
                await host._finish_announcement(router, uuid4(), True, notice)
            assert host.delivery_failed and notice.finished.is_set()
            assert host._announcement is None
            with pytest.raises(GatewayError, match="livekit_delivery_unavailable"):
                await host._before_turn()
            assert not core.history
            await memory.aclose()

    asyncio.run(exercise())


def test_fixed_notice_uses_actual_sdk_tts_and_transcription_hooks_without_core_history() -> None:
    async def exercise() -> None:
        memory, model = Mock(), Mock()
        core = CharacterCore(memory, model, model_tag="synthetic")
        agent = EmberAgent(core, defer_delivery=True)
        notice = uuid4()
        current = [notice]
        agent.current_notice = lambda: current[0]

        async def text(value: str) -> AsyncIterator[str]:
            yield value

        assert [value async for value in agent.transcription_node(text(TASK_NOTICE_TEXT), {})] == [
            TASK_NOTICE_TEXT
        ]
        frame = rtc.AudioFrame(
            b"\0" * 480, sample_rate=24000, num_channels=1, samples_per_channel=240
        )

        class Stream:
            def __init__(self) -> None:
                self.ended = asyncio.Event()
                self.inputs: list[str] = []
                self.closed = False

            async def __aenter__(self) -> "Stream":
                return self

            async def __aexit__(self, *_: object) -> None:
                self.closed = True

            def push_text(self, value: str) -> None:
                self.inputs.append(value)

            def end_input(self) -> None:
                self.ended.set()

            async def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
                await self.ended.wait()
                yield SimpleNamespace(frame=frame)
                yield SimpleNamespace(frame=frame)

        stream = Stream()
        provider = SimpleNamespace(
            capabilities=SimpleNamespace(streaming=True),
            _set_expressive=Mock(),
            stream=Mock(return_value=stream),
        )
        activity = SimpleNamespace(
            tts=provider,
            _resolve_expressive_options=lambda: None,
            session=SimpleNamespace(
                conn_options=SimpleNamespace(tts_conn_options=APIConnectOptions())
            ),
        )
        agent._get_activity_or_raise = Mock(return_value=activity)
        # This invokes Agent.default.tts_node itself, using only a synthetic PCM provider.
        output = agent.tts_node(text(TASK_NOTICE_TEXT), {})
        assert await anext(output) is frame
        assert stream.inputs == [TASK_NOTICE_TEXT]
        current[0] = uuid4()
        with pytest.raises(GatewayError, match="livekit_delivery_unavailable"):
            await anext(output)
        assert stream.closed
        await output.aclose()
        assert not core.history and core.pending_delivery is None
        model.stream.assert_not_called()
        memory.retrieve_context.assert_not_called()

    asyncio.run(exercise())


def test_p0_preparation_waits_for_notice_sdk_and_durable_finish_fences() -> None:
    async def exercise() -> None:
        core = CharacterCore(Mock(), Mock(), model_tag="synthetic")
        character = uuid4()
        host = HostedRoom(
            core,
            RoomBinding(
                instance_id=uuid4(),
                character_id=character,
                room="ember-" + uuid4().hex,
                endpoint="ws://127.0.0.1:7880",
            ),
            AsyncMock(),
        )
        played, receipt = asyncio.Event(), asyncio.Event()
        speech = SimpleNamespace(
            interrupt=Mock(), wait_for_playout=played.wait, _tasks=[], interrupted=False
        )
        notice = SimpleNamespace(identity=uuid4(), speech=speech, finished=receipt)
        host._announcement = notice
        host.session = SimpleNamespace(current_speech=speech)
        assert host._current_notice() == notice.identity
        host.session.current_speech = object()
        assert host._current_notice() is None
        host.session.current_speech = speech
        host.agent.gateway_llm.generation_done.clear()
        preparing = asyncio.create_task(host._before_turn())
        await asyncio.sleep(0)
        speech.interrupt.assert_called_once_with(force=True)
        assert not preparing.done() and not core.history
        played.set()
        await asyncio.sleep(0)
        assert not preparing.done()  # A played handle alone is not the final outbox receipt.
        host._announcement = None
        receipt.set()
        await asyncio.wait_for(preparing, 1)
        assert host.busy and not core.history
        assert not host.agent.gateway_llm.generation_done.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "value, authorized",
    [
        (TASK_NOTICE_TEXT + " Private suffix.", True),
        (TASK_NOTICE_TEXT[:-1], True),
        ("Another utterance.", True),
        (TASK_NOTICE_TEXT, False),
    ],
)
def test_fixed_notice_scope_never_authorizes_arbitrary_say_text(
    value: str, authorized: bool
) -> None:
    async def exercise() -> None:
        core = CharacterCore(Mock(), Mock(), model_tag="synthetic")
        agent = EmberAgent(core, defer_delivery=True)
        identity = uuid4()
        if authorized:
            agent.current_notice = lambda: identity
        activity = Mock()
        agent._get_activity_or_raise = Mock(return_value=activity)

        async def text() -> AsyncIterator[str]:
            yield value

        with pytest.raises(GatewayError, match="livekit_delivery_unavailable"):
            _ = [chunk async for chunk in agent.transcription_node(text(), {})]
        with pytest.raises(GatewayError, match="livekit_delivery_unavailable"):
            _ = [chunk async for chunk in agent.tts_node(text(), {})]
        agent._get_activity_or_raise.assert_not_called()
        assert not core.history and core.pending_delivery is None

    asyncio.run(exercise())


def invocation(target: SpeechTarget, *, caller: str = "ember-user") -> rtc.RpcInvocationData:
    return rtc.RpcInvocationData(
        request_id="synthetic-rpc",
        caller_identity=caller,
        payload=target.model_dump_json(),
        response_timeout=190,
    )


def test_microphone_reply_emits_exact_finished_identity_without_a_turn_rpc(tmp_path: Path) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "memory") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
            host = HostedRoom(
                core,
                RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                AsyncMock(),
            )
            host._speech_control_peer = True
            rpc = AsyncMock()
            host.room = SimpleNamespace(local_participant=SimpleNamespace(perform_rpc=rpc))
            turn = await core.prepare("Synthetic microphone utterance.")
            host.agent.gateway_llm.active_turn = turn
            text = "".join([event.text async for event in core.stream(turn, defer_delivery=True)])
            host._conversation_item(
                ConversationItemAddedEvent(
                    item=llm.ChatMessage(role="assistant", content=[text], interrupted=False)
                )
            )
            await host._drain_delivery()
            assert core.history[0].assistant == text and core.history[0].complete
            assert core.pending_delivery is None and host.agent.gateway_llm.active_turn is None
            rpc.assert_awaited_once_with(
                destination_identity="ember-user",
                method="ember.speech_finished",
                payload=host._speech_target(turn.request.request_id).model_dump_json(),
                response_timeout=5,
            )
            await memory.aclose()

    asyncio.run(exercise())


def test_stop_fences_late_played_prefix_and_next_turn_without_replaying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "memory") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
            host = HostedRoom(
                core,
                RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                AsyncMock(),
            )
            host._speech_control_peer = True
            turn = await core.prepare("Keep this original user message.")
            host.agent.gateway_llm.active_turn = turn
            text = "".join([event.text async for event in core.stream(turn, defer_delivery=True)])
            host.agent.gateway_llm.generation_done.clear()
            playout = asyncio.Event()

            class Speech:
                _tasks: list[asyncio.Task[object]] = []
                interrupt = Mock()

                async def wait_for_playout(self) -> None:
                    await playout.wait()

            speech = Speech()
            host._turn_speech = speech
            newer_speech = SimpleNamespace(interrupt=Mock())
            monkeypatch.setattr(
                type(host.session), "current_speech", property(lambda _: newer_speech)
            )
            host.session.interrupt = Mock()
            host.session.input.set_audio_enabled = Mock()
            host.room = SimpleNamespace(
                disconnect=AsyncMock(),
                local_participant=SimpleNamespace(perform_rpc=AsyncMock()),
            )
            target = SpeechTarget(
                instance_id=host.binding.instance_id,
                character_id=character,
                context_id=core.context_id,
                request_id=turn.request.request_id,
            )
            stop = asyncio.create_task(host._stop_speaking(invocation(target)))
            await asyncio.sleep(0)
            duplicate = asyncio.create_task(host._stop_speaking(invocation(target)))
            next_message = llm.ChatMessage(role="user", content=["The next user message."])
            next_turn = asyncio.create_task(
                host.agent.on_user_turn_completed(llm.ChatContext(), next_message)
            )
            await asyncio.sleep(0)
            speech.interrupt.assert_called_once_with(force=True)
            assert not stop.done() and not next_turn.done()
            # The SDK may deliver the actual heard prefix after interruption was requested.
            heard = text[:9]
            host._conversation_item(
                ConversationItemAddedEvent(
                    item=llm.ChatMessage(role="assistant", content=[heard], interrupted=True)
                )
            )
            playout.set()
            await asyncio.sleep(0)
            assert not stop.done() and not next_turn.done()
            host.agent.gateway_llm.generation_done.set()
            stopped, repeated, _ = await asyncio.gather(stop, duplicate, next_turn)
            assert stopped == repeated
            assert StopSpeakingResult.model_validate_json(stopped).outcome == "stopped"
            assert core.history[0].user == "Keep this original user message."
            assert core.history[0].assistant == heard and not core.history[0].complete
            assert core.history[1].user == "The next user message."
            assert core.pending_delivery is None
            assert host.agent.gateway_llm.prepared is not None
            newer = host.agent.gateway_llm.prepared[1]
            host.agent.gateway_llm.active_turn = newer
            # Neither a duplicate old request nor an unknown ID can stop a newer reply.
            assert await host._stop_speaking(invocation(target)) == stopped
            stale = target.model_copy(update={"request_id": uuid4()})
            assert (
                StopSpeakingResult.model_validate_json(
                    await host._stop_speaking(invocation(stale))
                ).outcome
                == "not_current"
            )
            speech.interrupt.assert_called_once()
            host.session.interrupt.assert_not_called()
            newer_speech.interrupt.assert_not_called()
            assert host.agent.gateway_llm.active_turn is newer
            host.session.input.set_audio_enabled.assert_not_called()
            host.room.disconnect.assert_not_awaited()
            await memory.aclose()

    asyncio.run(exercise())


def test_disconnected_stop_caller_cannot_cancel_owned_generation_drain(tmp_path: Path) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "memory") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
            host = HostedRoom(
                core,
                RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                AsyncMock(),
            )
            host._speech_control_peer = True
            turn = await core.prepare("Original input survives a disconnected Stop caller.")
            host.agent.gateway_llm.active_turn = turn
            _ = [event async for event in core.stream(turn, defer_delivery=True)]
            host.agent.gateway_llm.generation_done.clear()
            entered = asyncio.Event()
            release = asyncio.Event()

            class Speech:
                _tasks: list[asyncio.Task[object]] = []
                interrupt = Mock()

                async def wait_for_playout(self) -> None:
                    entered.set()
                    await release.wait()

            host._turn_speech = Speech()
            target = SpeechTarget(
                instance_id=host.binding.instance_id,
                character_id=character,
                context_id=core.context_id,
                request_id=turn.request.request_id,
            )
            caller = asyncio.create_task(host._stop_speaking(invocation(target)))
            await entered.wait()
            caller.cancel()
            await asyncio.sleep(0)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            owned = host._stops[target.request_id]
            assert not owned.done() and core.pending_delivery is not None
            release.set()
            await asyncio.sleep(0)
            assert not owned.done()  # Capacity remains fenced until native drain finishes.
            host.agent.gateway_llm.generation_done.set()
            await host._drain_stops()
            assert StopSpeakingResult.model_validate_json(owned.result()).outcome == "stopped"
            assert core.pending_delivery is None
            assert not core.history[0].assistant and not core.history[0].complete
            assert core.history[0].user.startswith("Original input survives")
            await memory.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("field", ["instance_id", "character_id", "context_id", "caller"])
def test_stop_rejects_other_actor_session_instance_and_participant(
    tmp_path: Path, field: str
) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "memory") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
            host = HostedRoom(
                core,
                RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                AsyncMock(),
            )
            host._speech_control_peer = True
            turn = await core.prepare("Synthetic input.")
            host.agent.gateway_llm.active_turn = turn
            host.session.interrupt = Mock()
            target = SpeechTarget(
                instance_id=host.binding.instance_id,
                character_id=character,
                context_id=core.context_id,
                request_id=turn.request.request_id,
            )
            if field != "caller":
                target = target.model_copy(update={field: uuid4()})
            with pytest.raises(rtc.RpcError):
                await host._stop_speaking(
                    invocation(target, caller="intruder" if field == "caller" else "ember-user")
                )
            host.session.interrupt.assert_not_called()
            await memory.aclose()

    asyncio.run(exercise())


def test_reply_identity_is_acknowledged_before_model_generation(tmp_path: Path) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "memory") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            model = FakeModel()
            core = CharacterCore(memory, model, model_tag="synthetic")
            host = HostedRoom(
                core,
                RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                AsyncMock(),
            )
            host._speech_control_peer = True
            announced = asyncio.Event()
            release = asyncio.Event()

            async def reply_started(**kwargs: str) -> str:
                assert kwargs["destination_identity"] == "ember-user"
                assert kwargs["method"] == "ember.reply_started"
                target = SpeechTarget.model_validate_json(kwargs["payload"])
                assert target.context_id == core.context_id
                assert target.character_id == character
                announced.set()
                await release.wait()
                return str(target.request_id)

            host.room = SimpleNamespace(
                local_participant=SimpleNamespace(perform_rpc=reply_started)
            )
            context = llm.ChatContext()
            message = context.add_message(role="user", content="Synthetic input.")
            await host.agent.prepare_turn(message)
            stream = host.agent.gateway_llm.chat(chat_ctx=context)

            async def collect() -> None:
                async for _ in stream:
                    pass

            collecting = asyncio.create_task(collect())
            await asyncio.wait_for(announced.wait(), 2)
            assert not model.requests and not collecting.done()
            release.set()
            await collecting
            assert len(model.requests) == 1
            await stream.aclose()
            await memory.aclose()

    asyncio.run(exercise())
