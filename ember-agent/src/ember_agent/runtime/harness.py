"""Select the installed provider at composition, not in CharacterCore or TaskRouter."""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

from ..inference.contracts import GatewayRequest, ModelClient
from .config import DeploymentConfig
from .opencode_harness import open_opencode
from .task_router import AgenticTaskProvider
from .task_store import TaskConflict


@asynccontextmanager
async def open_harness(
    config: DeploymentConfig,
    model: ModelClient,
    observe: Callable[[GatewayRequest], None],
) -> AsyncGenerator[tuple[AgenticTaskProvider, Callable[[], bool]]]:
    """Own the configured provider's lifecycle without exposing its API to tasks."""
    settings = config.selected_harness
    if settings is None or settings.provider != "opencode":
        raise TaskConflict("harness_provider_unsupported")
    async with open_opencode(config, settings, model, observe) as provider:
        yield provider, lambda: provider.available
