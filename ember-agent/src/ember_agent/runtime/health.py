"""Truthful preparation readiness; the doctor performs no runtime probes."""

from typing import Literal

from .config import DeploymentConfig, ManagementMode, RuntimeModel, ServiceName


class ServiceHealth(RuntimeModel):
    """Configured ownership is separate from health, which is not probed here."""

    mode: ManagementMode
    health: Literal["not_probed"] = "not_probed"


class ReadinessReport(RuntimeModel):
    """Serialized preparation readiness; no field claims a functioning runtime."""

    phase: Literal["1"] = "1"
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
            "Actual ChatGPT export is required to complete phase 0b acceptance.",
            "ABI/archive 0.1 contracts exist; storage and realtime runtime await later phases.",
            (
                "Configured model pin needs runtime revalidation and phase 3 measurements."
                if config.model_pin_configured
                else "Model pin configuration and phase 3 measurements are pending."
            ),
        ),
    )
