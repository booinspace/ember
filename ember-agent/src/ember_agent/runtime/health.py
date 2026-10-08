"""Truthful preparation readiness; the doctor performs no runtime probes."""

from typing import Literal

from .config import DeploymentConfig, ManagementMode, RuntimeModel, ServiceName


class ServiceHealth(RuntimeModel):
    """Configured ownership is separate from health, which is not probed here."""

    mode: ManagementMode
    health: Literal["not_probed"] = "not_probed"


class ReadinessReport(RuntimeModel):
    """Serialized preparation readiness; no field claims a functioning runtime."""

    phase: Literal["3"] = "3"
    configuration: Literal["valid"] = "valid"
    application: Literal["phase3_slice_only"] = "phase3_slice_only"
    character: Literal["available_unprobed"] = "available_unprobed"
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
            "Official ZIP import is supported; saved-memory inclusion and "
            "reviewed claim adoption remain open.",
            "Phase 3 supports managed LiveKit text startup; "
            "browser/audio integration remains phase 8.",
            (
                "Configured model pin needs fresh gateway health verification."
                if config.model_pin_configured
                else "Model pin configuration and phase 3 measurements are pending."
            ),
        ),
    )
