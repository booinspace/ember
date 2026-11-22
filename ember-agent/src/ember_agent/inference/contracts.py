"""Versioned P0 transport contracts; model output is never execution authority."""

from collections.abc import AsyncGenerator
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import UUID4, Field, JsonValue, model_validator

from ember_memory_abi.common import UtcTimestamp

from ..runtime.config import RuntimeModel

NON_CODING_MESSAGE_LIMIT = 64


class GatewayError(RuntimeError):
    """Safe fixed diagnostics; never include upstream body, prompts, credentials or paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Inference gateway: " + code + ".")


class UpstreamPromptOverflow(GatewayError):
    """Pinned backend rejected the rendered prompt before starting generation."""

    def __init__(self) -> None:
        super().__init__("prompt_overflow")


type PromptCallId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class PromptToolCall(RuntimeModel):
    """One complete historical call; its arguments and ID confer no execution authority."""

    call_id: PromptCallId
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
    arguments: dict[str, JsonValue] = Field(repr=False)


class PromptMessage(RuntimeModel):
    """One explicit request context item, without backend runner options."""

    role: Literal["system", "user", "assistant", "tool"]
    content: Annotated[str, Field(max_length=100000)] = Field(repr=False)
    tool_calls: Annotated[tuple[PromptToolCall, ...], Field(max_length=16)] = Field(
        default=(), exclude_if=lambda value: not value
    )
    tool_call_id: PromptCallId | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _tool_role(self) -> Self:
        if self.tool_calls and self.role != "assistant":
            raise ValueError("Only assistant messages contain historical tool calls.")
        if self.tool_call_id is not None and self.role != "tool":
            raise ValueError("Only tool results reference a historical call ID.")
        return self


class ToolFunction(RuntimeModel):
    """Definition and JSON schema only; partial calls cannot trigger execution."""

    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
    description: str = ""
    parameters: dict[str, JsonValue]


class ToolDefinition(RuntimeModel):
    """Allowlisted function definition supplied by trusted conversation composition."""

    type: Literal["function"] = "function"
    function: ToolFunction


class GatewayRequest(RuntimeModel):
    """Bounded immutable generation request; authenticated P0 scope is server-owned."""

    version: Literal["0.1.0", "0.2.0", "0.3.0", "0.4.0"] = "0.3.0"
    request_id: UUID4
    context_id: UUID4
    workload: Literal["P0", "P1", "P2", "P3", "P4", "P5"] = "P0"
    model: str
    messages: Annotated[tuple[PromptMessage, ...], Field(min_length=1)]
    max_output_tokens: Annotated[int, Field(gt=0, le=8192)] = 512
    deadline_at: UtcTimestamp
    output: Literal["text", "json"] = "text"
    output_schema: dict[str, JsonValue] | None = None
    tools: Annotated[tuple[ToolDefinition, ...], Field(max_length=16)] = ()
    thinking: bool = Field(default=False, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def _workload_limits(self) -> Self:
        if self.workload != "P5" and len(self.messages) > NON_CODING_MESSAGE_LIMIT:
            raise ValueError("Non-coding requests retain the existing message limit.")
        if self.thinking:
            if (
                self.version != "0.4.0"
                or self.workload != "P0"
                or self.output != "text"
                or self.tools
            ):
                raise ValueError("Live thinking requires a version 0.4.0 plain P0 text request.")
        elif self.workload != "P5" and self.max_output_tokens > 1192:
            raise ValueError("Non-thinking requests retain the existing output limit.")
        return self

    @model_validator(mode="after")
    def _tool_history(self) -> Self:
        if any(message.tool_calls or message.tool_call_id is not None for message in self.messages):
            seen: set[str] = set()
            pending: set[str] = set()
            for message in self.messages:
                if message.role == "tool":
                    if message.tool_call_id not in pending:
                        raise ValueError("Tool results must match one pending historical call.")
                    pending.remove(message.tool_call_id)
                else:
                    if pending:
                        raise ValueError(
                            "Historical calls require complete tool results before continuation."
                        )
                    for call in message.tool_calls:
                        if call.call_id in seen:
                            raise ValueError("Historical tool call IDs must be unique.")
                        seen.add(call.call_id)
                        pending.add(call.call_id)
            if pending:
                raise ValueError("Historical tool calls require complete results.")
        return self


class ToolCall(RuntimeModel):
    """Complete validated observation; transport never executes this call."""

    call_id: UUID4
    name: str
    arguments: dict[str, JsonValue] = Field(repr=False)


def missing_prefill(value: float | None) -> bool:
    """Legacy completion JSON omits new counters rather than sending unknown fields."""
    return value is None


class GenerationUsage(RuntimeModel):
    """Content-free measured counters and timings, separate from model context text."""

    prompt_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    total_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    first_visible_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    tokens_per_second: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    load_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    queue_seconds: Annotated[float | None, Field(ge=0, allow_inf_nan=False)] = Field(
        default=None, exclude_if=missing_prefill
    )
    prefill_seconds: Annotated[float | None, Field(ge=0, allow_inf_nan=False)] = Field(
        default=None, exclude_if=missing_prefill
    )


class GatewayEvent(RuntimeModel):
    """Ordered visible stream envelope; only complete tools/JSON are published."""

    request_id: UUID4
    context_id: UUID4
    sequence: Annotated[int, Field(ge=0)]
    kind: Literal["start", "progress", "thinking", "text", "tools", "end", "error"]
    text: str = Field(default="", repr=False)
    calls: tuple[ToolCall, ...] = ()
    usage: GenerationUsage | None = None
    truncated: bool = Field(default=False, exclude_if=lambda value: not value)
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")] | None = None

    @model_validator(mode="after")
    def _event_fields(self) -> Self:
        if self.kind == "end" and self.usage is None:
            raise ValueError("A completed generation requires counters.")
        if self.kind == "error" and self.code is None:
            raise ValueError("A failed generation requires a safe code.")
        if self.kind not in {"text", "thinking"} and self.text:
            raise ValueError("Only text and opt-in thinking events carry stream text.")
        if self.kind != "tools" and self.calls:
            raise ValueError("Only complete tool events carry calls.")
        if self.kind != "end" and (self.usage is not None or self.truncated):
            raise ValueError("Only completion events carry counters.")
        if self.kind != "error" and self.code is not None:
            raise ValueError("Only failure events carry codes.")
        return self


class GatewayHealth(RuntimeModel):
    """Admission/upstream identity state; it does not imply voice or full application readiness."""

    version: Literal["0.1.0", "0.2.0"] = "0.2.0"
    status: Literal["ready", "busy", "quiescence_required", "draining", "upstream_unavailable"]
    model: str
    digest: str
    context_tokens: int
    active_request_id: UUID | None = None
    journal_status: Literal["available", "failed"] = "available"
    supported_workloads: tuple[Literal["P0", "P1", "P2", "P3", "P4"], ...] = ("P0", "P3", "P4")
    queued_p1: int = 0
    queued_p2: int = 0
    queued_p3: int = 0
    queued_p4: int = 0
    realtime_sessions: tuple[UUID, ...] = ()
    scheduling: Literal["conservative"] = "conservative"
    features: tuple[str, ...] = Field(default=(), exclude_if=lambda value: not value)


class ModelClient(Protocol):
    """Character/LiveKit depend on transport, never Ollama or concrete gateway storage."""

    def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        """Emit visible ordered events or raise a safe typed failure; no fallback backend."""
        ...
