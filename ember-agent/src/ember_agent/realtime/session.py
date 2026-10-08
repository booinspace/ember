"""One explicitly dispatched LiveKit text session receiving an injected character core."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable

from livekit import rtc
from livekit.agents import AgentSession, llm, room_io
from pydantic import ValidationError

from ember_memory_abi.errors import MemoryError

from ..character.core import CharacterCore
from ..inference.contracts import GatewayError
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
    ) -> None:
        self.core = core
        self.binding = binding
        self.remember = remember
        self.room = rtc.Room()
        self.agent = EmberAgent(core)
        self.session: AgentSession[object] = AgentSession()
        self.busy = False
        self.closing = False
        self.seen: deque[str] = deque(maxlen=128)
        self.active: asyncio.Task[str] | None = None

    async def start(self, token: str) -> None:
        """Join the already-created room and start text-only output with recording disabled."""
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
                audio_input=False,
                audio_output=False,
                video_input=False,
                text_input=False,
                text_output=room_io.TextOutputOptions(sync_transcription=False),
                participant_identity=USER_IDENTITY,
                close_on_disconnect=False,
            ),
        )

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

    async def _reply(self, request: TurnInput) -> str:
        try:
            context = llm.ChatContext()
            message = context.add_message(role="user", content=request.text)
            await self.agent.on_user_turn_completed(context, message)
            prepared = self.agent.gateway_llm.prepared
            assert prepared is not None
            retained = prepared[1].retained_memories
            handle = self.session.generate_reply(user_input=message, allow_interruptions=False)
            await handle.wait_for_playout()
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
            # CharacterCore owns bounded history; SDK copies must not become another history store.
            await self.agent.update_chat_ctx(llm.ChatContext())
            self.busy = False

    async def aclose(self) -> None:
        """Stop admission, drain the sole turn, then close session and room."""
        self.closing = True
        try:
            if self.active is not None:
                async with asyncio.timeout(190):
                    await asyncio.shield(self.active)
        finally:
            try:
                await self.session.aclose()
            finally:
                await self.room.disconnect()
