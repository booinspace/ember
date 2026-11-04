"""One operator capability view; listing never grants execution authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..character.core import CharacterCore
from .task_router import TaskRouter


class Capability(BaseModel):
    """Distinguish an atomic operation from a provider that can run a workflow."""

    model_config = ConfigDict(strict=True, frozen=True)
    id: str
    kind: Literal["native", "mcp", "public_search", "harness"]
    name: str
    provider: str
    status: Literal["enabled", "unconfigured", "unavailable", "disabled"]
    operations: tuple[str, ...] = ()
    reason: str | None = None
    effect: Literal["read", "write", "denied"] | None = None


def capability_catalog(core: CharacterCore, router: TaskRouter | None) -> tuple[Capability, ...]:
    """Use current discovered/granted metadata and the actual installed task owners."""
    labels = {
        "inspect": "Inspect text",
        "summarize": "Summarize text",
        "recall": "Investigate memory",
        "review": "Review a draft",
    }
    values = [
        Capability(
            id="native.inspect_text" if operation == "inspect" else "native." + operation,
            kind="native",
            name=label,
            provider="ember",
            status="enabled" if router and router.providers[operation] else "unavailable",
            operations=(operation,),
            reason=None
            if router and router.providers[operation]
            else "Task provider is unavailable.",
        )
        for operation, label in labels.items()
    ]
    values.append(
        Capability(
            id="public_search",
            kind="public_search",
            name="Public web search",
            provider=core.search_provider,
            status="enabled"
            if core.public_search
            else "unavailable"
            if core.search_unavailable
            else "unconfigured",
            operations=("web_search",),
            reason="Configured; each invocation checks the installed command."
            if core.public_search
            else core.search_unavailable or "No public search provider configured.",
        )
    )
    values.append(
        Capability(
            id="harness",
            kind="harness",
            name="Agentic delegation",
            provider=router.delegate_label if router else "Agentic harness",
            status="enabled"
            if router and router.delegate
            else "unavailable"
            if router and router.delegate_unavailable
            else "unconfigured",
            operations=("delegate", "skills.list", "skills.view")
            if router and router.skills
            else ("delegate",),
            reason=(
                "Configured; each task verifies the sealed worker profile."
                if router.delegate
                else router.delegate_unavailable
            )
            if router
            else "Task provider is unavailable.",
        )
    )
    if router and router.mcp:
        snapshot = router.mcp.snapshot()
        for server in snapshot.servers:
            if server.status != "ready" or not server.tools:
                values.append(
                    Capability(
                        id="mcp.server." + server.id,
                        kind="mcp",
                        name=server.id,
                        provider="mcp",
                        status="disabled" if server.status == "disabled" else "unavailable",
                        reason=server.status,
                    )
                )
        values.extend(
            Capability(
                id=tool.id,
                kind="mcp",
                name=tool.name,
                provider=tool.server_id,
                status="enabled" if tool.available else "unavailable",
                operations=(tool.id,),
                reason=tool.reason,
                effect=tool.effect,
            )
            for tool in snapshot.tools
        )
    else:
        values.append(
            Capability(
                id="mcp",
                kind="mcp",
                name="MCP services",
                provider="mcp",
                status="unconfigured",
                reason="No MCP servers configured.",
            )
        )
    return tuple(values)
