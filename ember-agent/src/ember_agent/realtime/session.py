"""One explicitly dispatched LiveKit text session receiving an injected character core."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Protocol, cast
from uuid import UUID, uuid4

from livekit import rtc
from livekit.agents import (
    AgentSession,
    APIConnectOptions,
    ConversationItemAddedEvent,
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

from ..character.core import CharacterCore, TurnObservation
from ..inference.contracts import GatewayError
from ..memory.deep_contracts import DeepRecallResult
from .contracts import (
    READY_METHOD,
    REMEMBER_METHOD,
    TURN_METHOD,
    USER_IDENTITY,
    RememberInput,
    RoomBinding,
    TurnInput,
    TurnResult,
)
from .livekit import EmberAgent


class VoiceProviders(Protocol):
    """Optional provider composition does not import speech plugins into text startup."""

    @property
    def streaming_stt(self) -> stt.StreamAdapter: ...

    @property
    def vad(self) -> vad.VAD: ...

    @property
    def tts(self) -> tts.TTS[object] | None: ...


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
        self.agent = EmberAgent(core, defer_delivery=True, investigate=investigate)
        self.agent.before_turn = self._before_turn
        self.agent.before_generation = self._reserve_generation
        self.agent.prepare_failed = self._prepare_failed
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
        self.delivery_tasks: set[asyncio.Task[None]] = set()
        self.delivery_failed = False
        # SDK EventEmitter callback return annotations are unspecified.
        self.session.on("conversation_item_added", self._conversation_item)  # pyright: ignore[reportUnknownMemberType]
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
        participant.register_rpc_method(READY_METHOD, self._ready)
        participant.register_rpc_method(TURN_METHOD, self._turn)
        participant.register_rpc_method(REMEMBER_METHOD, self._remember)
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
                text_output=room_io.TextOutputOptions(
                    sync_transcription=bool(self.voice and self.voice.tts)
                ),
                participant_identity=USER_IDENTITY,
                close_on_disconnect=False,
            ),
        )
        self._start_permission_watch()

    def _start_permission_watch(self) -> None:
        if self.permission_watch is None:
            self.permission_watch = asyncio.create_task(
                self._watch_permissions(), name="ember-room-permissions"
            )

    def _prepare_failed(self) -> None:
        if self.active is None:
            self.busy = False

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
        if participant.identity == USER_IDENTITY and self.connection_changed is not None:
            self.connection_changed(False)

    async def quiesce(self) -> None:
        """Drain speech and generation before any realtime reservation is released."""
        self.session.input.set_audio_enabled(False)
        await self.session.interrupt(force=True)
        await self.agent.gateway_llm.generation_done.wait()
        await self._discard_unheard()
        if self.active is not None:
            await asyncio.shield(self.active)

    async def _before_turn(self) -> None:
        previous = self.agent.gateway_llm.active_turn
        speech = self._turn_speech
        if previous is not None and speech is None:
            speech = self.session.current_speech
            if speech is None:
                raise GatewayError("livekit_delivery_unavailable")
        if speech is not None:
            await self._settle_speech(speech)
        await self._drain_delivery()
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

    async def _settle_speech(self, speech: SpeechHandle) -> None:
        """Fence SDK playout, linked tasks and native generation before reconciliation."""
        # LiveKit 1.8.5 can mark an interrupted handle done before its
        # cancelled speech tasks finish. Fence those tasks as well as playout.
        async with asyncio.timeout(190):
            await speech.wait_for_playout()
            while True:
                # SDK 1.8's task list has an unparameterized Task annotation.
                linked = cast(list[asyncio.Task[object]], speech._tasks)  # pyright: ignore[reportUnknownMemberType,reportPrivateUsage]
                tasks = tuple(task for task in linked if not task.done())
                if not tasks:
                    break
                if asyncio.current_task() in tasks:
                    raise GatewayError("livekit_delivery_unavailable")
                await asyncio.gather(
                    *(asyncio.shield(task) for task in tasks), return_exceptions=True
                )
            await self.agent.gateway_llm.generation_done.wait()

    async def _reserve_generation(self) -> None:
        # Previous delivery is drained and original input is durable before gateway
        # admission. A provider outage cannot lose the completed user utterance.
        if self.before_turn is not None:
            await self.before_turn()

    def _conversation_item(self, event: ConversationItemAddedEvent) -> None:
        message = event.item
        turn = self.agent.gateway_llm.active_turn
        if not isinstance(message, llm.ChatMessage) or message.role != "assistant" or turn is None:
            return
        # The SDK emits its own message ID after playout; the sole owned turn binds this receipt.
        text = message.raw_text_content or ""
        task = asyncio.create_task(
            self._acknowledge(turn.request.request_id, text, message.interrupted),
            name="ember-room-delivery",
        )
        self.delivery_tasks.add(task)

        def observed(completed: asyncio.Task[None]) -> None:
            self.delivery_tasks.discard(completed)
            if completed.cancelled() or completed.exception() is not None:
                self.delivery_failed = True

        task.add_done_callback(observed)

    async def _acknowledge(self, identity: UUID, text: str, interrupted: bool) -> None:
        await self.agent.gateway_llm.generation_done.wait()
        pending = self.core.pending_delivery
        if pending is None or pending[0].request.request_id != identity:
            return
        complete = not interrupted and pending[2] and text == pending[1]
        await self.core.acknowledge(pending[0].request.request_id, text, complete=complete)
        self.agent.gateway_llm.active_turn = None
        if self.active is None:
            self.busy = False

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
        self._turn_speech = None

    def _authorize(self, data: rtc.RpcInvocationData) -> None:
        if data.caller_identity != USER_IDENTITY or self.closing:
            raise rtc.RpcError(1500, "Ember participant is unavailable.")

    async def _ready(self, data: rtc.RpcInvocationData) -> str:
        self._authorize(data)
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
        try:
            # A disconnected caller does not cancel the owned turn or release native capacity.
            return await asyncio.shield(self.active)
        finally:
            if self.active.done():
                self.busy = False
                self.active = None

    async def answer_investigation(self, result: DeepRecallResult) -> str:
        """A revalidated explicit inspector action reuses the sole owned SDK session."""
        if self.busy or self.closing or result.context_id != self.core.context_id:
            raise GatewayError("character_busy")
        request = TurnInput(request_id=uuid4(), text=result.query)
        self.busy = True
        self.active = asyncio.create_task(self._reply(request, result), name="ember-deep-answer")
        try:
            return await asyncio.shield(self.active)
        finally:
            if self.active.done():
                self.active = None
                self.busy = False

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
            self.busy = False

    async def aclose(self) -> None:
        """Stop admission, drain the sole turn, then close session and room."""
        self.closing = True
        try:
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
