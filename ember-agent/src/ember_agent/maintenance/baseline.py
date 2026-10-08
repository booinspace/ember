"""Synthetic real-model acceptance through the gateway; content-free reports."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ..character.core import Personality
from ..inference.client import HttpGatewayClient
from ..inference.contracts import (
    GatewayRequest,
    GenerationUsage,
    PromptMessage,
    ToolDefinition,
    ToolFunction,
)
from ..runtime.config import DeploymentConfig, RuntimeModel


class BaselineCase(RuntimeModel):
    """Public diagnostic outcome with counters, no generated/source content."""

    name: str
    usage: GenerationUsage
    visible: bool
    output_constraint_passed: bool


class BaselineReport(RuntimeModel):
    """Measured selected profile only; other profiles require their own owned gateway run."""

    profile: str
    model: str
    digest: str
    context_tokens: int
    measured_at: str
    cases: tuple[BaselineCase, ...]
    voice_transport: str = "not_exercised"
    tokenizer_profile: str = "conservative_utf8_guard"


async def measure(config: DeploymentConfig, client: HttpGatewayClient) -> BaselineReport:
    """Run finite explicit foreground acceptance cases through the P0 gateway."""
    import json

    results: list[BaselineCase] = []
    for name in ("text", "json", "tool"):
        request = GatewayRequest(
            request_id=uuid4(),
            context_id=uuid4(),
            model=config.model_tag,
            messages=(
                PromptMessage(role="system", content=Personality().instructions),
                PromptMessage(
                    role="user",
                    content="Reply with the word ready."
                    if name == "text"
                    else "Return a JSON object with a single boolean field ready set to true."
                    if name == "json"
                    else "Call record_probe with ready true. Do not add explanatory text.",
                ),
            ),
            max_output_tokens=96,
            output="json" if name == "json" else "text",
            tools=(
                ToolDefinition(
                    function=ToolFunction(
                        name="record_probe",
                        parameters={
                            "type": "object",
                            "properties": {"ready": {"type": "boolean"}},
                            "required": ["ready"],
                            "additionalProperties": False,
                        },
                    )
                ),
            )
            if name == "tool"
            else (),
            deadline_at=(datetime.now(UTC) + timedelta(seconds=180))
            .isoformat()
            .replace("+00:00", "Z"),
        )
        usage: GenerationUsage | None = None
        visible = ""
        calls = 0
        async for event in client.stream(request):
            visible += event.text
            calls += len(event.calls)
            if event.usage:
                usage = event.usage
        if usage is None:
            raise ValueError("Baseline has no completion counters.")
        valid = (
            ("<think>" not in visible and "<analysis>" not in visible)
            if name == "text"
            else (json.loads(visible) == {"ready": True} if name == "json" else calls == 1)
        )
        results.append(
            BaselineCase(
                name=name,
                usage=usage,
                visible=bool(visible or calls),
                output_constraint_passed=valid,
            )
        )
    return BaselineReport(
        profile=config.active_profile,
        model=config.model_tag,
        digest=config.model_digest,
        context_tokens=config.context_tokens,
        measured_at=datetime.now(UTC).isoformat(),
        cases=tuple(results),
    )
