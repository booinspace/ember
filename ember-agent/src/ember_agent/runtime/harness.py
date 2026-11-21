"""Select one installed task adapter; provider SDKs never enter the character runtime."""

import shutil
from collections.abc import Awaitable, Callable

from ember_memory_abi.related import ContextDependencies

from ..inference.contracts import GatewayRequest, ModelClient
from .config import DeploymentConfig
from .hermes_tasks import HermesTask, PersonalContext
from .skills import InstalledSkillProvider, Skills
from .task_router import AgenticTaskProvider, HarnessToolBroker, ToolContext, ToolRouter
from .task_store import TaskConflict


def create_task_provider(
    config: DeploymentConfig,
    client: ModelClient,
    tools: ToolRouter,
    *,
    broker: HarnessToolBroker,
    recall: Callable[[str, ToolContext], Awaitable[PersonalContext | None]],
    validate: Callable[[ContextDependencies], Awaitable[bool]],
    observe: Callable[[GatewayRequest, Callable[[], Awaitable[bool]]], None],
    skills: Skills | None = None,
) -> AgenticTaskProvider:
    """Keep provider selection here; task, memory and gateway contracts stay unchanged."""
    settings = config.selected_harness
    if settings is None or settings.provider != "hermes":
        raise TaskConflict("harness_provider_unsupported")
    executable = shutil.which(settings.command[0])
    if executable is None:
        raise TaskConflict("harness_executable_unavailable")
    settings = settings.model_copy(update={"command": (executable, *settings.command[1:])})
    config = config.model_copy(update={"harness": settings, "hermes": None})
    broker.catalog(settings.tools)
    return HermesTask(
        config,
        client,
        tools,
        broker=broker,
        recall=recall,
        validate=validate,
        observe=observe,
        skills=skills,
    )


def create_skill_provider(config: DeploymentConfig) -> Skills | None:
    """One neutral protocol for harness-owned inventory; future adapters need no Core changes."""
    settings = config.selected_harness
    if settings is None or not settings.skills:
        return None
    executable = shutil.which(settings.command[0])
    if executable is None:
        raise TaskConflict("skills_executable_unavailable")
    capabilities = ("searx_search", "skills.view", "skills.list", *settings.tools)
    return Skills(
        settings.provider,
        InstalledSkillProvider(
            (executable, *settings.command[1:]), settings.profile_sha256, capabilities
        ),
    )
