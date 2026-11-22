"""LiveKit RAG hook and owned visible stream, with no default provider or auxiliary generation."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable
from contextlib import aclosing
from dataclasses import replace
from uuid import UUID

from livekit import rtc
from livekit.agents import Agent, APIConnectOptions, ModelSettings, UserTurnExceededEvent, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

from ember_memory_abi.errors import MemoryError

from ..character.core import CharacterCore, PreparedTurn, TurnObservation
from ..character.recall_intent import deep_recall_query
from ..inference.contracts import GatewayError
from ..memory.deep_contracts import DeepRecallResult
from .contracts import TASK_NOTICE_TEXT, USER_IDENTITY


class GatewayLLM(llm.LLM[object]):
    """SDK LLM whose single-use authorization comes from a completed user-turn hook."""

    def __init__(self, core: CharacterCore, *, defer_delivery: bool = False) -> None:
        super().__init__()
        self.core = core
        self.completed_request_id: UUID | None = None
        self.prepared: tuple[str, PreparedTurn] | None = None
        self.defer_delivery = defer_delivery
        self.active_turn: PreparedTurn | None = None
        self.on_generation_start: Callable[[], None] | None = None
        self.before_reply: Callable[[PreparedTurn], Awaitable[None]] | None = None
        self.generation_done = asyncio.Event()
        self.generation_done.set()

    def authorize(self, message_id: str, turn: PreparedTurn) -> None:
        """Bind one user input identity; prompts/tools from auxiliary SDK calls cannot bypass P0."""
        # A newer completed user turn supersedes authorization that never started generation.
        self.prepared = (message_id, turn)

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        **options: object,
    ) -> llm.LLMStream:
        """Consume one prepared turn; disable SDK automatic retry to avoid duplicate delivery."""
        del options
        latest = next(
            (
                item
                for item in reversed(chat_ctx.items)
                if isinstance(item, llm.ChatMessage) and item.role == "user"
            ),
            None,
        )
        if tools or latest is None or self.prepared is None or latest.id != self.prepared[0]:
            raise GatewayError("livekit_unprepared_generation")
        _, turn = self.prepared
        if latest.text_content != turn.user:
            raise GatewayError("livekit_turn_changed")
        self.prepared = None
        self.active_turn = turn
        if self.on_generation_start is not None:
            self.on_generation_start()
        self.generation_done.clear()
        return GatewayStream(
            self,
            core=self.core,
            turn=turn,
            chat_ctx=chat_ctx,
            conn_options=replace(conn_options, max_retry=0, timeout=190),
        )


class GatewayStream(llm.LLMStream):
    """Use SDK-owned task/channel lifecycle; only gateway-visible text reaches LiveKit."""

    def __init__(
        self,
        model: GatewayLLM,
        *,
        core: CharacterCore,
        turn: PreparedTurn,
        chat_ctx: llm.ChatContext,
        conn_options: APIConnectOptions,
    ) -> None:
        self.core = core
        self.model = model
        self.turn = turn
        # SDK base constructor annotations omit its LLM generic parameter.
        super().__init__(model, chat_ctx=chat_ctx, tools=[], conn_options=conn_options)  # pyright: ignore[reportUnknownMemberType]
        # The SDK can cancel this task before it enters _run.
        self._task.add_done_callback(self._generation_finished)

    def _generation_finished(self, _: asyncio.Task[None]) -> None:
        if self.model.active_turn is self.turn:
            self.model.generation_done.set()

    def _record_genai_request(self, span: object) -> None:
        """Disable SDK prompt/output capture even if an outer host enables tracing content."""
        del span
        self._record_content = False

    async def _run(self) -> None:
        if self.model.before_reply is not None:
            await self.model.before_reply(self.turn)
        await self._generate()

    async def _generate(self) -> None:
        async with aclosing(
            self.core.stream(self.turn, defer_delivery=self.model.defer_delivery)
        ) as events:
            async for event in events:
                if event.kind == "text":
                    self._event_ch.send_nowait(
                        llm.ChatChunk(
                            id=str(event.request_id),
                            delta=llm.ChoiceDelta(role="assistant", content=event.text),
                        )
                    )
                elif event.kind == "end" and event.usage is not None:
                    usage = event.usage
                    self._event_ch.send_nowait(
                        llm.ChatChunk(
                            id=str(event.request_id),
                            usage=llm.CompletionUsage(
                                prompt_tokens=usage.prompt_tokens,
                                completion_tokens=usage.output_tokens,
                                total_tokens=usage.prompt_tokens + usage.output_tokens,
                            ),
                        )
                    )

        self.model.completed_request_id = self.turn.request.request_id


class EmberAgent(Agent):
    """LiveKit receives the same injected core; no source/provider owns personality or identity."""

    def __init__(
        self,
        core: CharacterCore,
        *,
        defer_delivery: bool = False,
        investigate: Callable[[str, TurnObservation], Awaitable[DeepRecallResult]] | None = None,
    ) -> None:
        self.core = core
        self.investigate = investigate
        self.before_turn: Callable[[], Awaitable[None]] | None = None
        self.before_generation: Callable[[], Awaitable[None]] | None = None
        self.prepare_failed: Callable[[], None] | None = None
        self.gateway_llm = GatewayLLM(core, defer_delivery=defer_delivery)
        self.reply_participant: rtc.LocalParticipant | None = None
        self.current_notice: Callable[[], UUID | None] | None = None
        # SDK constructor annotations omit generic parameters for optional plugins.
        super().__init__(instructions=core.personality.instructions, llm=self.gateway_llm, tools=[])  # pyright: ignore[reportUnknownMemberType]

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Recall before generation; SDK chat history is not a second memory/prompt owner."""
        del turn_ctx
        await self.prepare_turn(new_message)

    async def prepare_turn(
        self,
        new_message: llm.ChatMessage,
        *,
        request_id: UUID | None = None,
        investigation: DeepRecallResult | None = None,
    ) -> None:
        """Text and final speech use identical observation and explicit deep-command routing."""
        text = new_message.text_content
        if text is None or new_message.role != "user":
            raise GatewayError("invalid_user_turn")
        if self.before_turn is not None:
            await self.before_turn()
        try:
            observation = await self.core.observe_input(text, request_id=request_id)
            if self.before_generation is not None:
                await self.before_generation()
            query = deep_recall_query(
                text, natural=self.core.recall_enabled and self.core.natural_deep_recall
            )
            if investigation is None and query is not None:
                if not query or self.investigate is None:
                    raise GatewayError("deep_recall_unavailable")
                investigation = await self.investigate(query, observation)
            turn = await self.core.prepare(
                text, investigation=investigation, observation=observation
            )
            if turn.prompts is not None:
                await self.update_instructions(turn.prompts.instructions)
        except (GatewayError, MemoryError, asyncio.CancelledError):
            if self.prepare_failed is not None:
                self.prepare_failed()
            raise
        self.gateway_llm.authorize(new_message.id, turn)

    async def transcription_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncGenerator[str, None]:
        """Publish validated read-ahead text without changing SDK played-prefix receipts."""
        del model_settings
        participant = self.reply_participant
        turn = self.gateway_llm.active_turn
        notice = self.current_notice() if self.current_notice else None
        if turn is None and notice is not None:
            yield await self._fixed_notice(text, notice)
            return
        if participant is None or turn is None:
            raise GatewayError("livekit_delivery_unavailable")
        writer = await participant.stream_text(
            topic="ember.reply",
            destination_identities=[USER_IDENTITY],
            attributes={"lk.segment_id": str(turn.request.request_id)},
        )
        try:
            async for chunk in text:
                if not await self.core.current(turn.dependencies, turn.certificate):
                    raise GatewayError("context_invalidated")
                await writer.write(chunk)
                yield chunk
        finally:
            await writer.aclose()

    async def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncGenerator[rtc.AudioFrame, None]:
        """Revalidate synthesized frames; queued transport audio also needs runtime fencing."""
        turn = self.gateway_llm.active_turn
        notice = self.current_notice() if self.current_notice else None
        if turn is None:
            if notice is None:
                raise GatewayError("livekit_delivery_unavailable")
            fixed = await self._fixed_notice(text, notice)

            async def source() -> AsyncGenerator[str, None]:
                yield fixed

            text = source()
        async with aclosing(Agent.default.tts_node(self, text, model_settings)) as audio:
            async for frame in audio:
                if turn is not None:
                    if self.gateway_llm.active_turn is not turn or not await self.core.current(
                        turn.dependencies, turn.certificate
                    ):
                        raise GatewayError("context_invalidated")
                elif (
                    self.gateway_llm.active_turn is not None
                    or self.current_notice is None
                    or self.current_notice() != notice
                ):
                    raise GatewayError("livekit_delivery_unavailable")
                yield frame

    async def _fixed_notice(self, text: AsyncIterable[str], identity: UUID) -> str:
        """Only the owned fixed status sentence can bypass canonical reply validation."""
        value = ""
        async for chunk in text:
            value += chunk
            if (
                not TASK_NOTICE_TEXT.startswith(value)
                or self.current_notice is None
                or self.current_notice() != identity
                or self.gateway_llm.active_turn is not None
            ):
                raise GatewayError("livekit_delivery_unavailable")
        if value != TASK_NOTICE_TEXT:
            raise GatewayError("livekit_delivery_unavailable")
        return value

    async def on_user_turn_exceeded(self, ev: UserTurnExceededEvent) -> None:
        """Suppress the SDK's automatic auxiliary reply; only completed user turns are P0."""
        del ev
