"""Versioned P0 transport contracts; model output is never execution authority."""

from collections.abc import AsyncGenerator
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import UUID4, Field, JsonValue, model_validator

from ember_memory_abi.common import UtcTimestamp

from ..runtime.config import RuntimeModel


class GatewayError(RuntimeError):
    """Safe fixed diagnostics; never include upstream body, prompts, credentials or paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Inference gateway: " + code + ".")


class PromptMessage(RuntimeModel):
    """One explicit request context item, without backend runner options."""

    role: Literal["system", "user", "assistant", "tool"]
    content: Annotated[str, Field(max_length=100000)] = Field(repr=False)


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

    version: Literal["0.1.0", "0.2.0"] = "0.2.0"
    request_id: UUID4
    context_id: UUID4
    workload: Literal["P0", "P1", "P2", "P3", "P4"] = "P0"
    model: str
    messages: Annotated[tuple[PromptMessage, ...], Field(min_length=1, max_length=64)]
    max_output_tokens: Annotated[int, Field(gt=0, le=1192)] = 512
    deadline_at: UtcTimestamp
    output: Literal["text", "json"] = "text"
    output_schema: dict[str, JsonValue] | None = None
    tools: Annotated[tuple[ToolDefinition, ...], Field(max_length=16)] = ()


class ToolCall(RuntimeModel):
    """Complete validated observation; transport never executes this call."""

    call_id: UUID4
    name: str
    arguments: dict[str, JsonValue] = Field(repr=False)


class GenerationUsage(RuntimeModel):
    """Content-free measured counters and timings, separate from model context text."""

    prompt_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    total_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    first_visible_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    tokens_per_second: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    load_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0


class GatewayEvent(RuntimeModel):
    """Ordered visible stream envelope; only complete tools/JSON are published."""

    request_id: UUID4
    context_id: UUID4
    sequence: Annotated[int, Field(ge=0)]
    kind: Literal["start", "progress", "text", "tools", "end", "error"]
    text: str = Field(default="", repr=False)
    calls: tuple[ToolCall, ...] = ()
    usage: GenerationUsage | None = None
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")] | None = None

    @model_validator(mode="after")
    def _event_fields(self) -> Self:
        if self.kind == "end" and self.usage is None:
            raise ValueError("A completed generation requires counters.")
        if self.kind == "error" and self.code is None:
            raise ValueError("A failed generation requires a safe code.")
        if self.kind != "text" and self.text:
            raise ValueError("Only text events carry visible text.")
        if self.kind != "tools" and self.calls:
            raise ValueError("Only complete tool events carry calls.")
        if self.kind != "end" and self.usage is not None:
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
    supported_workloads: tuple[Literal["P0", "P3", "P4"], ...] = ("P0", "P3", "P4")
    queued_p3: int = 0
    queued_p4: int = 0
    realtime_sessions: tuple[UUID, ...] = ()
    scheduling: Literal["conservative"] = "conservative"


class ModelClient(Protocol):
    """Character/LiveKit depend on transport, never Ollama or concrete gateway storage."""

    def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        """Emit visible ordered events or raise a safe typed failure; no fallback backend."""
        ...
