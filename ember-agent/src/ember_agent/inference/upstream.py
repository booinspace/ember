"""Backend-neutral generation frames; hidden reasoning is always ephemeral."""

from collections.abc import AsyncIterator
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .contracts import GatewayRequest


class BackendModel(BaseModel):
    """Narrow external response immediately; stream-only thinking never becomes a prompt."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)


class NativeFunction(BackendModel):
    name: str
    arguments: dict[str, JsonValue] | str


class NativeTool(BackendModel):
    index: int = 0
    function: NativeFunction


class NativeMessage(BackendModel):
    role: Literal["assistant"] = "assistant"
    content: str = Field(default="", repr=False)
    thinking: str = Field(default="", repr=False, exclude=True)
    tool_calls: list[NativeTool] = Field(default_factory=list[NativeTool])


class NativeFrame(BackendModel):
    model: str | None = None
    message: NativeMessage = Field(default_factory=NativeMessage)
    done: bool = False
    error: str | None = Field(default=None, repr=False)
    prompt_eval_count: int = 0
    prompt_eval_duration: int | None = None
    eval_count: int = 0
    eval_duration: int = 0
    decode_tokens_per_second: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    load_duration: int = 0
    done_reason: str | None = None


class Upstream(Protocol):
    """Owned metadata/generation boundary, replaceable by deterministic acceptance fakes."""

    async def verify(self, *, loaded: bool = False) -> None:
        """Verify configured tag/digest, and the loaded identity after generation when requested."""
        ...

    def generate(self, request: GatewayRequest) -> AsyncIterator[NativeFrame]:
        """Yield bounded native frames, keeping hidden reasoning outside contracts."""
        ...

    async def aclose(self) -> None:
        """Close this adapter's owned network connections only."""
        ...
