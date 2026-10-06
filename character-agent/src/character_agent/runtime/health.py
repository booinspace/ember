"""Truthful scaffold readiness; no runtime probes are performed in phase -1."""

from typing import Literal

from .config import DeploymentConfig, ManagementMode, RuntimeModel, ServiceName


class ServiceHealth(RuntimeModel):
    """Configured ownership is separate from health, which is not probed here."""

    mode: ManagementMode
    health: Literal["not_probed"] = "not_probed"


class ReadinessReport(RuntimeModel):
    """Serialized preparation readiness; no field claims a functioning runtime."""

    phase: Literal[-1] = -1
    configuration: Literal["valid"] = "valid"
    application: Literal["scaffold_only"] = "scaffold_only"
    character: Literal["not_implemented"] = "not_implemented"
    voice: Literal["not_implemented"] = "not_implemented"
    model_pin: Literal["configured_unverified", "pending"]
    services: dict[ServiceName, ServiceHealth]
    odysseus: Literal["disabled"] = "disabled"
    blockers: tuple[str, ...]


def readiness(config: DeploymentConfig) -> ReadinessReport:
    """Report validated configuration only, without network probes or runtime startup."""
    return ReadinessReport(
        model_pin="configured_unverified" if config.model_pin_configured else "pending",
        services={
            name: ServiceHealth(mode=service.mode) for name, service in config.services.items()
        },
        blockers=(
            "Actual ChatGPT archive is required for phase 0 reconnaissance.",
            "Canonical ABI, memory storage, and realtime runtime await later phases.",
            "Actual model pin and local model measurements await phase 3.",
        ),
    )
