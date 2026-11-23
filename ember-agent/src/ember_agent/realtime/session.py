"""One explicitly dispatched LiveKit text session receiving an injected character core."""

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID, uuid4

from livekit import rtc
from livekit.agents import (
    AgentSession,
    AgentStateChangedEvent,
    APIConnectOptions,
    ConversationItemAddedEvent,
    SpeechCreatedEvent,
    UserStateChangedEvent,
    llm,
    room_io,
    stt,
    tts,
    vad,
)
from livekit.agents.types import NOT_GIVEN
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.agents.voice.speech_handle import SpeechHandle
from pydantic import ValidationError

from ember_memory_abi.errors import MemoryError

from ..character.core import CharacterCore, PreparedTurn, TurnObservation
from ..inference.contracts import GatewayError
from ..memory.deep_contracts import DeepRecallResult
from ..runtime.speech_settings import SpeechOptions, SpeechSelection
from ..runtime.worker_process import settled
from .contracts import (
    READY_METHOD,
    REMEMBER_METHOD,
    REPLY_STARTED_METHOD,
    SPEECH_FINISHED_METHOD,
    STOP_SPEAKING_METHOD,
    TASK_NOTICE_TEXT,
    TURN_METHOD,
    USER_IDENTITY,
    ReadyInput,
    RememberInput,
    RoomBinding,
    SpeechTarget,
    StopSpeakingResult,
    TurnInput,
    TurnResult,
)
from .livekit import EmberAgent

if TYPE_CHECKING:
    from ..runtime.task_router import TaskRouter


@dataclass(frozen=True)
class _Announcement:
    """One fixed notice has independent speech ownership and no conversation delivery."""

    identity: UUID
    speech: SpeechHandle
    finished: asyncio.Event = field(default_factory=asyncio.Event)


class VoiceProviders(Protocol):
    """Optional provider composition does not import speech plugins into text startup."""

    @property
    def streaming_stt(self) -> stt.StreamAdapter: ...

    @property
    def vad(self) -> vad.VAD: ...

    @property
    def tts(self) -> tts.TTS[object] | None: ...

    @property
    def speech_options(self) -> SpeechOptions | None: ...

    def configure_speech(self, selection: SpeechSelection) -> SpeechOptions: ...


class HostedRoom:
    """Own one SDK session and reject overlapping turns, replay, and unauthorized callers.

    Explicit dispatch avoids SDK per-job processes opening the same canonical root.
    Text RPC calls prepare the RAG hook explicitly: SDK generate_reply does not invoke
    the completed-audio-turn hook for programmatic text input.
    """

    def __init__(
        self,
        core: CharacterCore,
        binding: RoomBinding,
        remember: Callable[[RememberInput], Awaitable[str]],
        *,
        voice: VoiceProviders | None = None,
        investigate: Callable[[str, TurnObservation], Awaitable[DeepRecallResult]] | None = None,
        before_turn: Callable[[], Awaitable[None]] | None = None,
        connection_changed: Callable[[bool], None] | None = None,
    ) -> None:
        self.core = core
        self.binding = binding
        self.remember = remember
        self.room = rtc.Room()
        self.voice = voice
        self.before_turn = before_turn
        self.connection_changed = connection_changed
        self.capture_fence: Callable[[], Awaitable[None]] | None = None
        self.capture_wake: Callable[[], None] | None = None
        self._preparing = 0
        self._capture_speeches: set[SpeechHandle] = set()
        self.voice_priority: Callable[[bool], Awaitable[None]] | None = None
        self.voice_hold: asyncio.Task[None] | None = None
        self.voice_activity = 0.0
        self.agent = EmberAgent(core, defer_delivery=True, investigate=investigate)
        self.agent.before_turn = self._before_turn
        self.agent.before_generation = self._reserve_generation
        self.agent.prepare_finished = self._prepare_finished
        self.permission_watch: asyncio.Task[None] | None = None
        self.permission_failure = False
        connection = SessionConnectOptions(
            stt_conn_options=APIConnectOptions(max_retry=0, timeout=180),
            llm_conn_options=APIConnectOptions(max_retry=0, timeout=190),
            tts_conn_options=APIConnectOptions(max_retry=0, timeout=10),
        )
        self.session: AgentSession[object] = AgentSession(
            stt=voice.streaming_stt if voice else NOT_GIVEN,
            vad=voice.vad if voice else None,
            tts=voice.tts if voice and voice.tts is not None else NOT_GIVEN,
            conn_options=connection,
            turn_handling={
                "turn_detection": "vad" if voice else "manual",
                "preemptive_generation": {"enabled": False},
                "interruption": {"enabled": True},
            },
            user_away_timeout=None,
        )
        self._turn_speech: SpeechHandle | None = None
        self.agent.gateway_llm.on_generation_start = self._capture_speech
        self.agent.gateway_llm.before_reply = self._announce_reply
        self.agent.current_notice = self._current_notice
        self._speech_control_peer = False
        self._stops: dict[UUID, asyncio.Task[str]] = {}
        self._announcement: _Announcement | None = None
        self.task_announcer: asyncio.Task[None] | None = None
        self.delivery_tasks: set[asyncio.Task[None]] = set()
        self.delivery_failed = False
        # SDK EventEmitter callback return annotations are unspecified.
        self.session.on("conversation_item_added", self._conversation_item)  # pyright: ignore[reportUnknownMemberType]
        self.session.on("user_state_changed", self._voice_user_state)  # pyright: ignore[reportUnknownMemberType]
        self.session.on("agent_state_changed", self._capture_agent_state)  # pyright: ignore[reportUnknownMemberType]
        self.session.on("speech_created", self._capture_speech_created)  # pyright: ignore[reportUnknownMemberType]
        self.busy = False
        self.closing = False
        self.seen: deque[str] = deque(maxlen=128)
        self.active: asyncio.Task[str] | None = None

    async def start(self, token: str) -> None:
        """Join one room; only configured CPU STT and local TTS enable media."""
        # Room EventEmitter annotations do not type callback return values.
        self.room.on("participant_connected", self._participant_joined)  # pyright: ignore[reportUnknownMemberType]
        self.room.on("participant_disconnected", self._participant_left)  # pyright: ignore[reportUnknownMemberType]
        await self.room.connect(self.binding.endpoint, token)
        participant = self.room.local_participant
        self.agent.reply_participant = participant
        participant.register_rpc_method(READY_METHOD, self._ready)
        participant.register_rpc_method(TURN_METHOD, self._turn)
        participant.register_rpc_method(REMEMBER_METHOD, self._remember)
        participant.register_rpc_method(STOP_SPEAKING_METHOD, self._stop_speaking)
        # SDK capture_run=True overload omits its result generic; this call returns None.
        await self.session.start(  # pyright: ignore[reportUnknownMemberType]
            self.agent,
            room=self.room,
            record=False,
            session_host=False,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(sample_rate=16000, num_channels=1)
                if self.voice
                else False,
                audio_output=room_io.AudioOutputOptions(sample_rate=24000)
                if self.voice and self.voice.tts
                else False,
                video_input=False,
                text_input=False,
                # Keep played-prefix receipts independent of browser read-ahead text.
                text_output=room_io.TextOutputOptions(
                    sync_transcription=bool(self.voice and self.voice.tts)
                ),
                participant_identity=USER_IDENTITY,
                close_on_disconnect=False,
            ),
        )
        self._start_permission_watch()

    def capture_idle(self) -> bool:
        """Preparation, queued speech and verified delivery are foreground work."""
        model = self.agent.gateway_llm
        speech = self.session.current_speech
        connected = USER_IDENTITY in self.room.remote_participants
        return (
            not self.closing
            and not self.delivery_failed
            and not self._preparing
            and not self.busy
            and self.active is None
            and not self.core.busy
            and self.core.pending_delivery is None
            and not self.delivery_tasks
            and model.prepared is None
            and model.active_turn is None
            and (speech is None or speech.done())
            and not self._capture_speeches
            and self._announcement is None
            and not any(not task.done() for task in self._stops.values())
            and (
                not connected
                or (
                    self.session.agent_state == "listening"
                    and self.session.user_state != "speaking"
                )
            )
            and (self.voice_hold is None or self.voice_hold.done())
        )

    def _wake_capture(self) -> None:
        if self.capture_wake is not None:
            self.capture_wake()

    def _capture_agent_state(self, _: AgentStateChangedEvent) -> None:
        self._wake_capture()

    def _capture_speech_created(self, event: SpeechCreatedEvent) -> None:
        if self.capture_wake is None:
            return
        speech = event.speech_handle
        prepared = self.agent.gateway_llm.prepared if event.source == "generate_reply" else None
        self._capture_speeches.add(speech)
        speech.add_done_callback(lambda done: self._capture_speech_finished(done, prepared))
        self._wake_capture()

    def _capture_speech_finished(
        self, speech: SpeechHandle, prepared: tuple[str, PreparedTurn] | None
    ) -> None:
        # LiveKit can mark an interrupted handle done before its linked tasks settle.
        linked = self._linked_speech_tasks(speech)
        if linked:
            linked[0].add_done_callback(lambda _: self._capture_speech_finished(speech, prepared))
            return
        self._capture_speeches.discard(speech)
        model = self.agent.gateway_llm
        if prepared is not None and model.prepared is prepared:
            model.prepared = None  # The exact queued authorization never reached generation.
            if self.active is None and not self._preparing:
                self.busy = False
        if (
            self._turn_speech is speech
            and not self.delivery_tasks
            and model.active_turn is not None
        ):
            # No SDK receipt: settle as unheard, never promote generated text to history.
            self._queue_delivery(model.active_turn.request.request_id, "", interrupted=True)
        self._wake_capture()

    def prompt_idle(self) -> None:
        """Fence preparation, authorized generation and verified speech delivery together."""
        if (
            self.closing
            or self.busy
            or self.core.busy
            or self.core.pending_delivery is not None
            or self.agent.gateway_llm.prepared is not None
            or self.agent.gateway_llm.active_turn is not None
            or self.session.current_speech is not None
            or self._announcement is not None
        ):
            raise GatewayError("prompt_settings_busy")

    def configure_speech(self, selection: SpeechSelection) -> SpeechOptions:
        """Keep every sentence of a reply on one voice/pace, including buffered playout."""
        if self.closing or self.voice is None:
            raise GatewayError("speech_controls_unavailable")
        if (
            self.busy
            or self.core.busy
            or self.core.pending_delivery is not None
            or self.session.current_speech is not None
            or self._announcement is not None
        ):
            raise GatewayError("speech_settings_busy")
        return self.voice.configure_speech(selection)

    def start_task_announcements(self, router: "TaskRouter") -> None:
        """Own a single speech worker for the connected task session."""
        if self.task_announcer is not None:
            raise RuntimeError("Task announcements already started")
        if self.voice is not None and self.voice.tts is not None:
            self.task_announcer = asyncio.create_task(
                self._announce_tasks(router), name="ember-task-announcements"
            )
            self.task_announcer.add_done_callback(
                lambda done: None if done.cancelled() else done.exception()
            )

    def _can_announce_tasks(self) -> bool:
        return (
            not self.closing
            and not self.busy
            and not self.core.busy
            and self.core.pending_delivery is None
            and self.agent.gateway_llm.prepared is None
            and self.agent.gateway_llm.active_turn is None
            and self.session.current_speech is None
            and self._announcement is None
            and not any(not stop.done() for stop in self._stops.values())
            and self.session.agent_state == "listening"
            and self.session.user_state == "listening"
            and any(p.identity == USER_IDENTITY for p in self.room.remote_participants.values())
        )

    async def _announce_tasks(self, router: "TaskRouter") -> None:
        while not self.closing:
            await asyncio.sleep(1)
            if not self._can_announce_tasks():
                continue
            if await router.confirmations():
                continue
            turn = self.core.history[-1].request_id if self.core.history else None
            identity = await router.store.claim_speech(
                router.character_id, router.session_id, turn_id=turn
            )
            if identity is None:
                continue
            completed = False
            announcement: _Announcement | None = None
            try:
                current = self.core.history[-1].request_id if self.core.history else None
                if (
                    self._can_announce_tasks()
                    and current == turn
                    and not await router.confirmations()
                ):
                    current = self.core.history[-1].request_id if self.core.history else None
                    if not self._can_announce_tasks() or current != turn:
                        continue
                    speech_id = uuid4()
                    speech = self.session.say(
                        self._notice_text(speech_id),
                        allow_interruptions=True,
                        add_to_chat_ctx=False,
                    )
                    announcement = _Announcement(speech_id, speech)
                    self._announcement = announcement
                    try:
                        await speech.wait_for_playout()
                    except asyncio.CancelledError:
                        speech.interrupt(force=True)
                        raise
                    completed = not speech.interrupted and speech.exception() is None
            finally:
                # A disconnected/stopped announcer still owns its exact delivery receipt.
                await settled(
                    asyncio.create_task(
                        self._finish_announcement(router, identity, completed, announcement)
                    )
                )

    async def _notice_text(self, identity: UUID) -> AsyncGenerator[str, None]:
        if self.capture_fence is not None:
            await self.capture_fence()
        await self._announce_speech(identity)
        current = self._announcement
        if current is not None and current.identity == identity and not current.speech.interrupted:
            yield TASK_NOTICE_TEXT

    def _current_notice(self) -> UUID | None:
        notice = self._announcement
        if (
            notice is not None
            and self.agent.gateway_llm.active_turn is None
            and self.session.current_speech is notice.speech
            and not notice.speech.interrupted
        ):
            return notice.identity
        return None

    async def _finish_announcement(
        self,
        router: "TaskRouter",
        task_id: UUID,
        completed: bool,
        announcement: _Announcement | None,
    ) -> None:
        verified = False
        try:
            if announcement is not None:
                await self._settle_speech(announcement.speech, native_generation=False)
            verified = completed
        except Exception:
            self.delivery_failed = True
            raise
        finally:
            try:
                await router.store.finish_speech(task_id, router.session_id, completed=verified)
            except Exception:
                self.delivery_failed = True
                raise
            finally:
                if announcement is not None:
                    try:
                        await self._speech_finished(announcement.identity)
                    finally:
                        if self._announcement is announcement:
                            self._announcement = None
                        announcement.finished.set()
                        self._wake_capture()

    async def stop_task_announcements(self) -> None:
        """Stop the owned worker and retain uncertain delivery after interruption."""
        if self.task_announcer is not None:
            self.task_announcer.cancel()
            await asyncio.gather(self.task_announcer, return_exceptions=True)
            self.task_announcer = None

    def _start_permission_watch(self) -> None:
        if self.permission_watch is None:
            self.permission_watch = asyncio.create_task(
                self._watch_permissions(), name="ember-room-permissions"
            )

    def _prepare_finished(self, prepared: bool) -> None:
        self._preparing -= 1
        if not prepared and self.active is None:
            self.busy = False
        self._wake_capture()

    async def _watch_permissions(self) -> None:
        # Generation can finish before buffered audio plays. A failed validator must
        # fence the transport too; it cannot silently stop this owned watcher.
        try:
            while not self.closing:
                await asyncio.sleep(0.05)
                turn = self.agent.gateway_llm.active_turn
                if turn is not None and not await self.core.current(
                    turn.dependencies, turn.certificate
                ):
                    self.permission_failure = True
                    await self.session.interrupt(force=True)
                    await self.agent.gateway_llm.generation_done.wait()
                    await self._discard_unheard()
        except Exception:
            self.permission_failure = True
            self.delivery_failed = True
            self.closing = True
            self.session.input.set_audio_enabled(False)
            try:
                await self.room.disconnect()
            finally:
                await self.session.aclose()

    def _participant_joined(self, participant: rtc.RemoteParticipant) -> None:
        if participant.identity == USER_IDENTITY and not self.closing:
            if self.connection_changed is not None:
                self.connection_changed(True)
            if self.voice is not None:
                self.session.input.set_audio_enabled(True)

    def _participant_left(self, participant: rtc.RemoteParticipant) -> None:
        if participant.identity == USER_IDENTITY:
            self._speech_control_peer = False
            if self.connection_changed is not None:
                self.connection_changed(False)

    def _voice_user_state(self, event: UserStateChangedEvent) -> None:
        self._wake_capture()
        priority = self.voice_priority
        if (
            priority is None
            or event.new_state != "speaking"
            or self.closing
            or USER_IDENTITY not in self.room.remote_participants
        ):
            return
        self.voice_activity = monotonic()
        if self.voice_hold is None or self.voice_hold.done():
            self.voice_hold = asyncio.create_task(
                self._hold_voice(priority), name="ember-voice-priority"
            )
            self.voice_hold.add_done_callback(lambda _: self._wake_capture())

    async def _hold_voice(self, priority: Callable[[bool], Awaitable[None]]) -> None:
        try:
            await priority(True)
            renew_at = monotonic() + 1.0
            while not self.closing and USER_IDENTITY in self.room.remote_participants:
                now = monotonic()
                if (
                    self.session.user_state == "speaking"
                    or self.session.agent_state in {"thinking", "speaking"}
                    or self.busy
                    or self.agent.gateway_llm.active_turn is not None
                ):
                    self.voice_activity = now
                if now - self.voice_activity >= 2.0:
                    break  # Also releases false starts that never produce a turn.
                if now >= renew_at:
                    await priority(True)
                    renew_at = monotonic() + 1.0
                await asyncio.sleep(0.05)
        except GatewayError:
            return  # The usual gateway admission and its durable fence still apply.
        finally:
            try:
                await priority(False)
            except GatewayError:
                pass  # Disconnect also releases the reservation via the session lease.

    async def quiesce(self) -> None:
        """Drain speech and generation before any realtime reservation is released."""
        self.session.input.set_audio_enabled(False)
        await self.session.interrupt(force=True)
        await self.agent.gateway_llm.generation_done.wait()
        if self.active is not None:
            await asyncio.shield(self.active)
        for speech in tuple(self._capture_speeches):
            await self._settle_speech(speech)
        await self._discard_unheard()

    async def _before_turn(self) -> None:
        self._preparing += 1
        if self.capture_fence is not None:
            await self.capture_fence()
        await self._drain_stops()
        announcement = self._announcement
        if announcement is not None:
            announcement.speech.interrupt(force=True)
            await self._settle_speech(announcement.speech, native_generation=False)
            await announcement.finished.wait()
        previous = self.agent.gateway_llm.active_turn
        speech = self._turn_speech
        if previous is not None and speech is None:
            speech = self.session.current_speech
            if speech is None:
                raise GatewayError("livekit_delivery_unavailable")
        if speech is not None:
            await self._settle_speech(speech)
        await self._drain_delivery()
        # A stop can arrive while the previous playout fence is being awaited.
        # Finish that exact operation before authorizing a successor reply.
        await self._drain_stops()
        if previous is not None and self.agent.gateway_llm.active_turn is previous:
            pending = self.core.pending_delivery
            if pending is not None:
                if pending[0].request.request_id != previous.request.request_id:
                    raise GatewayError("delivery_receipt_unknown")
                await self.core.acknowledge(previous.request.request_id, "", complete=False)
            self.agent.gateway_llm.active_turn = None
        if self._turn_speech is speech:
            self._turn_speech = None
        self.permission_failure = False
        if self.closing or self.delivery_failed:
            raise GatewayError("livekit_delivery_unavailable")
        self.busy = True

    def _capture_speech(self) -> None:
        """Retain the SDK handle even if its scheduler clears the current pointer."""
        self._turn_speech = self.session.current_speech

    @staticmethod
    def _linked_speech_tasks(speech: SpeechHandle) -> tuple[asyncio.Task[object], ...]:
        # LiveKit's handle can finish before its linked tasks on forced interruption.
        linked = cast(list[asyncio.Task[object]], speech._tasks)  # pyright: ignore[reportUnknownMemberType,reportPrivateUsage]
        return tuple(task for task in linked if not task.done())

    async def _settle_speech(self, speech: SpeechHandle, *, native_generation: bool = True) -> None:
        """Fence SDK playout, linked tasks and native generation before reconciliation."""
        async with asyncio.timeout(190):
            await speech.wait_for_playout()
            while tasks := self._linked_speech_tasks(speech):
                if asyncio.current_task() in tasks:
                    raise GatewayError("livekit_delivery_unavailable")
                await asyncio.gather(
                    *(asyncio.shield(task) for task in tasks), return_exceptions=True
                )
            if native_generation:
                await self.agent.gateway_llm.generation_done.wait()

    async def _reserve_generation(self) -> None:
        # Previous delivery is drained and original input is durable before gateway
        # admission. A provider outage cannot lose the completed user utterance.
        if self.before_turn is not None:
            if USER_IDENTITY not in self.room.remote_participants:
                raise GatewayError("livekit_delivery_unavailable")
            await self.before_turn()

    def _conversation_item(self, event: ConversationItemAddedEvent) -> None:
        message = event.item
        turn = self.agent.gateway_llm.active_turn
        if not isinstance(message, llm.ChatMessage) or message.role != "assistant" or turn is None:
            return
        # The SDK emits its own message ID after playout; the sole owned turn binds this receipt.
        self._queue_delivery(
            turn.request.request_id, message.raw_text_content or "", message.interrupted
        )

    def _queue_delivery(self, identity: UUID, text: str, interrupted: bool) -> None:
        task = asyncio.create_task(
            self._acknowledge(identity, text, interrupted), name="ember-room-delivery"
        )
        self.delivery_tasks.add(task)

        def observed(completed: asyncio.Task[None]) -> None:
            self.delivery_tasks.discard(completed)
            if completed.cancelled() or completed.exception() is not None:
                self.delivery_failed = True
            self._wake_capture()

        task.add_done_callback(observed)

    async def _acknowledge(self, identity: UUID, text: str, interrupted: bool) -> None:
        model = self.agent.gateway_llm
        await model.generation_done.wait()
        turn = model.active_turn
        if turn is None or turn.request.request_id != identity:
            return
        pending = self.core.pending_delivery
        if pending is not None:
            if pending[0].request.request_id != identity:
                raise GatewayError("delivery_receipt_unknown")
            complete = not interrupted and pending[2] and text == pending[1]
            await self.core.acknowledge(identity, text, complete=complete)
        model.active_turn = None
        if self.active is None:
            self.busy = False
        await self._speech_finished(identity)

    async def _drain_delivery(self) -> None:
        tasks = tuple(self.delivery_tasks)
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))
        if self.delivery_failed:
            raise GatewayError("livekit_delivery_unavailable")

    async def _discard_unheard(self) -> None:
        if self._turn_speech is not None:
            await self._settle_speech(self._turn_speech)
        await self._drain_delivery()
        pending = self.core.pending_delivery
        if pending is not None:
            await self.core.acknowledge(pending[0].request.request_id, "", complete=False)
        self.agent.gateway_llm.active_turn = None
        self.agent.gateway_llm.prepared = None
        self._turn_speech = None
        if self.active is None and not self._preparing:
            self.busy = False
        self._wake_capture()

    def _authorize(self, data: rtc.RpcInvocationData) -> None:
        if data.caller_identity != USER_IDENTITY or self.closing:
            raise rtc.RpcError(1500, "Ember participant is unavailable.")

    async def _announce_reply(self, turn: PreparedTurn) -> None:
        """Give the scoped browser its target before admitting generation or emitting PCM."""
        await self._announce_speech(turn.request.request_id)

    def _speech_target(self, identity: UUID) -> SpeechTarget:
        return SpeechTarget(
            instance_id=self.binding.instance_id,
            character_id=self.binding.character_id,
            context_id=self.core.context_id,
            request_id=identity,
        )

    async def _announce_speech(self, identity: UUID) -> None:
        if not self._speech_control_peer:
            return
        target = self._speech_target(identity)
        try:
            receipt = await self.room.local_participant.perform_rpc(
                destination_identity=USER_IDENTITY,
                method=REPLY_STARTED_METHOD,
                payload=target.model_dump_json(),
                response_timeout=5,
            )
            if receipt != str(target.request_id):
                raise GatewayError("livekit_delivery_unavailable")
        except rtc.RpcError as error:
            raise GatewayError("livekit_delivery_unavailable") from error

    async def _speech_finished(self, identity: UUID) -> None:
        if not self._speech_control_peer:
            return
        try:
            await self.room.local_participant.perform_rpc(
                destination_identity=USER_IDENTITY,
                method=SPEECH_FINISHED_METHOD,
                payload=self._speech_target(identity).model_dump_json(),
                response_timeout=5,
            )
        except rtc.RpcError:
            # The exact notice has ended even when the participant has left.
            pass

    async def _drain_stops(self) -> None:
        pending = tuple(task for task in self._stops.values() if not task.done())
        if pending:
            await asyncio.gather(*(asyncio.shield(task) for task in pending))

    async def _stop_speaking(self, data: rtc.RpcInvocationData) -> str:
        self._authorize(data)
        try:
            target = SpeechTarget.model_validate_json(data.payload)
        except ValidationError as error:
            raise rtc.RpcError(1502, "Invalid speech target.") from error
        if (
            target.instance_id != self.binding.instance_id
            or target.character_id != self.binding.character_id
            or target.context_id != self.core.context_id
            or not self._speech_control_peer
        ):
            raise rtc.RpcError(1500, "Speech target is unavailable.")
        previous = self._stops.get(target.request_id)
        if previous is not None:
            return await asyncio.shield(previous)
        announcement = self._announcement
        turn = self.agent.gateway_llm.active_turn
        is_announcement = announcement is not None and announcement.identity == target.request_id
        if not is_announcement and (turn is None or turn.request.request_id != target.request_id):
            return StopSpeakingResult(
                request_id=target.request_id, outcome="not_current"
            ).model_dump_json()
        speech = announcement.speech if is_announcement and announcement else self._turn_speech
        if speech is None:
            raise rtc.RpcError(1503, "Speech interruption could not be verified.")
        # Interrupt the retained exact handle. Session.interrupt also cancels queued
        # speeches, which could belong to a newer microphone utterance.
        # No await separates the identity check from the targeted SDK interruption.
        speech.interrupt(force=True)
        finishing = (
            self._finish_announcement_stop(announcement)
            if is_announcement and announcement is not None
            else self._finish_stop(target.request_id, speech)
        )
        task = asyncio.create_task(finishing, name="ember-stop-speaking")
        self._stops[target.request_id] = task

        # The RPC caller may disconnect; keep observing the owned fence.
        def stop_finished(done: asyncio.Task[str]) -> None:
            if not done.cancelled():
                done.exception()
            self._wake_capture()

        task.add_done_callback(stop_finished)
        # Keep bounded idempotence receipts; never evict a live owned operation.
        for identity in tuple(self._stops):
            if len(self._stops) <= 128:
                break
            if self._stops[identity].done():
                del self._stops[identity]
        return await asyncio.shield(task)

    async def _finish_announcement_stop(self, announcement: _Announcement) -> str:
        try:
            async with asyncio.timeout(190):
                await self._settle_speech(announcement.speech, native_generation=False)
                await announcement.finished.wait()
            return StopSpeakingResult(
                request_id=announcement.identity, outcome="stopped"
            ).model_dump_json()
        except Exception as error:
            self.delivery_failed = True
            raise rtc.RpcError(1503, "Speech interruption could not be verified.") from error

    async def _finish_stop(self, identity: UUID, speech: SpeechHandle) -> str:
        try:
            await self._settle_speech(speech)
            await self.agent.gateway_llm.generation_done.wait()
            await self._drain_delivery()
            turn = self.agent.gateway_llm.active_turn
            if turn is not None and turn.request.request_id == identity:
                pending = self.core.pending_delivery
                if pending is not None and pending[0].request.request_id == identity:
                    await self.core.acknowledge(identity, "", complete=False)
                self.core.turn_view.stage(identity, "interrupted")
                self.agent.gateway_llm.active_turn = None
            if self._turn_speech is speech:
                self._turn_speech = None
            if self.active is None:
                self.busy = False
            return StopSpeakingResult(request_id=identity, outcome="stopped").model_dump_json()
        except Exception as error:
            self.delivery_failed = True
            raise rtc.RpcError(1503, "Speech interruption could not be verified.") from error

    async def _ready(self, data: rtc.RpcInvocationData) -> str:
        self._authorize(data)
        if data.payload:
            try:
                ReadyInput.model_validate_json(data.payload)
            except ValidationError as error:
                raise rtc.RpcError(1502, "Invalid room readiness input.") from error
        self._speech_control_peer = bool(data.payload)
        async with asyncio.timeout(5):
            await self.session.room_io.wait_for_ready()
        return self.binding.model_dump_json()

    async def _remember(self, data: rtc.RpcInvocationData) -> str:
        self._authorize(data)
        if self.busy:
            raise rtc.RpcError(1501, "Ember is busy.")
        try:
            request = RememberInput.model_validate_json(data.payload)
        except ValidationError as error:
            raise rtc.RpcError(1502, "Invalid memory input.") from error
        self.busy = True
        try:
            return await self.remember(request)
        except MemoryError as error:
            raise rtc.RpcError(1503, "Memory operation failed.") from error
        finally:
            self.busy = False
            self._wake_capture()

    def _reply_finished(self, task: asyncio.Task[str]) -> None:
        # RPC cancellation does not cancel the turn. Resume only after it settles.
        if not task.cancelled():
            task.exception()
        if self.active is task:
            self.active = None
            self.busy = False
            self._wake_capture()

    async def _turn(self, data: rtc.RpcInvocationData) -> str:
        self._authorize(data)
        try:
            request = TurnInput.model_validate_json(data.payload)
        except ValidationError as error:
            raise rtc.RpcError(1502, "Invalid turn input.") from error
        identity = str(request.request_id)
        if self.busy or identity in self.seen:
            raise rtc.RpcError(1501, "Ember is busy or this turn was already submitted.")
        self.busy = True
        self.seen.append(identity)
        self.active = asyncio.create_task(self._reply(request), name="ember-room-turn")
        self.active.add_done_callback(self._reply_finished)
        # A disconnected caller does not cancel the owned turn or release native capacity.
        return await asyncio.shield(self.active)

    async def answer_investigation(self, result: DeepRecallResult) -> str:
        """A revalidated explicit inspector action reuses the sole owned SDK session."""
        if self.busy or self.closing or result.context_id != self.core.context_id:
            raise GatewayError("character_busy")
        request = TurnInput(request_id=uuid4(), text=result.query)
        self.busy = True
        self.active = asyncio.create_task(self._reply(request, result), name="ember-deep-answer")
        self.active.add_done_callback(self._reply_finished)
        return await asyncio.shield(self.active)

    async def _reply(
        self, request: TurnInput, investigation: DeepRecallResult | None = None
    ) -> str:
        try:
            context = llm.ChatContext()
            message = context.add_message(role="user", content=request.text)
            await self.agent.prepare_turn(
                message, request_id=request.request_id, investigation=investigation
            )
            prepared = self.agent.gateway_llm.prepared
            assert prepared is not None
            retained = prepared[1].retained_memories
            handle = self.session.generate_reply(user_input=message, allow_interruptions=False)
            await handle.wait_for_playout()
            await self._drain_delivery()
            if self.permission_failure:
                raise GatewayError("context_invalidated")
            if (
                handle.interrupted
                or self.agent.gateway_llm.completed_request_id != prepared[1].request.request_id
            ):
                if request.request_id in self._stops:
                    raise GatewayError("speech_stopped")
                raise GatewayError("livekit_delivery_incomplete")
            return TurnResult(
                request_id=request.request_id, outcome="complete", retained_memories=retained
            ).model_dump_json()
        except (GatewayError, MemoryError) as error:
            code = error.code if isinstance(error, GatewayError) else "memory_unavailable"
            return TurnResult(
                request_id=request.request_id, outcome="error", code=code
            ).model_dump_json()
        finally:
            await self._discard_unheard()
            # CharacterCore owns bounded history; SDK copies must not become another history store.
            await self.agent.update_chat_ctx(llm.ChatContext())

    async def aclose(self) -> None:
        """Stop admission, drain the sole turn, then close session and room."""
        self.closing = True
        if self.voice_hold is not None:
            await self.voice_hold
        await self.stop_task_announcements()
        try:
            await self._drain_stops()
            if self.permission_watch is not None:
                await asyncio.shield(self.permission_watch)
                self.permission_watch = None
            if self.active is not None:
                async with asyncio.timeout(190):
                    await asyncio.shield(self.active)
        finally:
            try:
                await self.session.aclose()
                await self._discard_unheard()
            finally:
                await self.room.disconnect()
