"""LiveKit RAG hook and owned visible stream, with no default provider or auxiliary generation."""

from contextlib import aclosing
from dataclasses import replace
from uuid import UUID

from livekit.agents import Agent, APIConnectOptions, UserTurnExceededEvent, llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

from ..character.core import CharacterCore, PreparedTurn
from ..inference.contracts import GatewayError


class GatewayLLM(llm.LLM[object]):
    """SDK LLM whose single-use authorization comes from a completed user-turn hook."""

    def __init__(self, core: CharacterCore) -> None:
        super().__init__()
        self.core = core
        self.completed_request_id: UUID | None = None
        self.prepared: tuple[str, PreparedTurn] | None = None

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

    def _record_genai_request(self, span: object) -> None:
        """Disable SDK prompt/output capture even if an outer host enables tracing content."""
        del span
        self._record_content = False

    async def _run(self) -> None:
        async with aclosing(self.core.stream(self.turn)) as events:
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

    def __init__(self, core: CharacterCore) -> None:
        self.core = core
        self.gateway_llm = GatewayLLM(core)
        # SDK constructor annotations omit generic parameters for optional plugins.
        super().__init__(instructions=core.personality.instructions, llm=self.gateway_llm, tools=[])  # pyright: ignore[reportUnknownMemberType]

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Recall before generation; SDK chat history is not a second memory/prompt owner."""
        del turn_ctx
        text = new_message.text_content
        if text is None or new_message.role != "user":
            raise GatewayError("invalid_user_turn")
        turn = await self.core.prepare(text)
        self.gateway_llm.authorize(new_message.id, turn)

    async def on_user_turn_exceeded(self, ev: UserTurnExceededEvent) -> None:
        """Suppress the SDK's automatic auxiliary reply; only completed user turns are P0."""
        del ev
