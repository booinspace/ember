"""Local terminal participant using room-scoped tokens and the private text protocol."""

import asyncio
from collections.abc import Callable
from uuid import uuid4

from livekit import rtc
from pydantic import ValidationError

from ..inference.contracts import GatewayError
from .contracts import (
    AGENT_IDENTITY,
    READY_METHOD,
    REMEMBER_METHOD,
    TURN_METHOD,
    RememberInput,
    RoomBinding,
    TurnInput,
    TurnResult,
)


class RoomClient:
    """Own text-reader tasks, bounded output, and one non-retried foreground request."""

    def __init__(self, binding: RoomBinding) -> None:
        self.binding = binding
        self.room = rtc.Room()
        self.tasks: set[asyncio.Task[None]] = set()
        self.output: list[str] = []
        self.finished = asyncio.Event()
        self.pending = False
        self.failed = False
        self.on_text: Callable[[str], None] | None = None
        self.room.register_text_stream_handler("lk.transcription", self._received)

    async def connect(self, token: str) -> None:
        """Join, then verify the live worker identity against private discovery metadata."""
        try:
            async with asyncio.timeout(10):
                await self.room.connect(self.binding.endpoint, token)
                value = await self.room.local_participant.perform_rpc(
                    destination_identity=AGENT_IDENTITY, method=READY_METHOD, payload=""
                )
                if RoomBinding.model_validate_json(value) != self.binding:
                    raise GatewayError("livekit_room_identity_mismatch")
        except (rtc.ConnectError, rtc.RpcError, ValidationError, TimeoutError) as error:
            raise GatewayError("livekit_room_unavailable") from error

    def _received(self, reader: rtc.TextStreamReader, identity: str) -> None:
        if identity != AGENT_IDENTITY or not self.pending:
            return
        task = asyncio.create_task(self._read(reader), name="ember-room-output")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _read(self, reader: rtc.TextStreamReader) -> None:
        try:
            size = 0
            async with asyncio.timeout(190):
                async for text in reader:
                    size += len(text.encode("utf-8"))
                    if size > 128 * 1024:
                        raise GatewayError("livekit_output_too_large")
                    self.output.append(text)
                    if self.on_text:
                        self.on_text(text)
        except (GatewayError, TimeoutError, rtc.RpcError):
            self.failed = True
        finally:
            self.finished.set()

    async def ask(
        self, text: str, *, on_text: Callable[[str], None] | None = None
    ) -> tuple[str, TurnResult]:
        """Send one explicit prompt and wait for both the reply acknowledgement and text EOF."""
        if self.pending:
            raise GatewayError("livekit_client_busy")
        request = TurnInput(request_id=uuid4(), text=text)
        self.pending = True
        self.output = []
        self.finished.clear()
        self.failed = False
        self.on_text = on_text
        try:
            value = await self.room.local_participant.perform_rpc(
                destination_identity=AGENT_IDENTITY,
                method=TURN_METHOD,
                payload=request.model_dump_json(),
                response_timeout=190,
            )
            result = TurnResult.model_validate_json(value)
            if result.request_id != request.request_id:
                raise GatewayError("livekit_reply_identity")
            if result.outcome != "complete":
                raise GatewayError(result.code or "livekit_generation_failed")
            async with asyncio.timeout(10):
                await self.finished.wait()
            if self.failed:
                raise GatewayError("livekit_output_incomplete")
            return "".join(self.output), result
        except (rtc.RpcError, ValidationError, TimeoutError) as error:
            raise GatewayError("livekit_turn_failed") from error
        finally:
            self.pending = False
            self.on_text = None

    async def remember(self, key: str, text: str) -> str:
        """Invoke the explicit authoring capability rather than parsing dialogue as commands."""
        if self.pending:
            raise GatewayError("livekit_client_busy")
        return await self.room.local_participant.perform_rpc(
            destination_identity=AGENT_IDENTITY,
            method=REMEMBER_METHOD,
            payload=RememberInput(key=key, text=text).model_dump_json(),
            response_timeout=15,
        )

    async def aclose(self) -> None:
        """Disconnect and cancel/join only this participant's owned readers."""
        await self.room.disconnect()
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
