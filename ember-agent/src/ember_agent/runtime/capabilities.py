"""One operator capability view; listing never grants execution authority."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..character.core import CharacterCore
from ..web_evidence import PublicWebOperation
from .task_router import TaskRouter


class Capability(BaseModel):
    """Distinguish an atomic operation from a provider that can run a workflow."""

    model_config = ConfigDict(strict=True, frozen=True)
    id: str
    kind: Literal["native", "mcp", "public_search", "harness"]
    name: str
    provider: str
    provider_label: str | None = None
    binding_revision: str | None = None
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
    operations: tuple[tuple[PublicWebOperation, str, str], ...] = (
        ("search", "public_search", "Public web search"),
        ("read", "read_page", "Read public page"),
    )
    for operation, identity, label in operations:
        binding = core.public_web.binding(operation)
        ready = binding is not None and binding.ready()
        placeholder = "<public query>" if operation == "search" else "<public URL>"
        values.append(
            Capability(
                id=identity,
                kind="public_search",
                name=label,
                provider=binding.provider if binding else "unconfigured",
                provider_label=binding.label if binding else None,
                binding_revision=binding.revision if binding else None,
                status="enabled" if ready else "unavailable" if binding else "unconfigured",
                operations=("web_search" if operation == "search" else "read_page",),
                reason=(
                    f"In Chat, use {binding.label} {operation}: {placeholder}. "
                    "Only send public inputs."
                    if binding and ready
                    else "Provider unavailable."
                    if binding
                    else "No provider configured."
                ),
            )
        )
    values.append(
        Capability(
            id="harness",
            kind="harness",
            name="Agentic delegation",
            provider=router.delegate_label if router else "Agentic harness",
            status="enabled"
            if router and router.delegation_available
            else "unavailable"
            if router and (router.delegate or router.delegate_unavailable)
            else "unconfigured",
            operations=("delegate", "skills.list", "skills.view")
            if router and router.skills
            else ("delegate",),
            reason=(
                "Configured for read-only workspace investigation."
                if router.delegation_available
                else router.delegate_unavailable or "Task provider is unavailable."
            )
            if router
            else "Task provider is unavailable.",
        )
    )
    if router and (router.build_available or router.build_unavailable or router.providers["build"]):
        values.append(
            Capability(
                id="harness.build",
                kind="harness",
                name="Isolated coding tasks",
                provider="OpenCode",
                status="enabled" if router.build_available else "unavailable",
                operations=("build",),
                reason=(
                    "Configured isolated Build workspace."
                    if router.build_available
                    else router.build_unavailable or "Build cleanup could not be verified."
                ),
            )
        )

    if router and (
        router.research_available or router.research_unavailable or router.providers["research"]
    ):
        values.append(
            Capability(
                id="harness.research",
                kind="harness",
                name="Public web research",
                provider=router.research_provider or "OpenCode",
                provider_label=router.research_label,
                binding_revision=router.research_revision,
                status="enabled" if router.research_available else "unavailable",
                operations=("research",),
                reason=(
                    "Configured public evidence retrieval through Ember."
                    if router.research_available
                    else router.research_unavailable or "Research worker unavailable."
                ),
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
