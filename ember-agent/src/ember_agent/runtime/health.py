"""Truthful preparation readiness; the doctor performs no runtime probes."""

from typing import Literal

from .config import DeploymentConfig, ManagementMode, RuntimeModel, ServiceName


class ServiceHealth(RuntimeModel):
    """Configured ownership is separate from health, which is not probed here."""

    mode: ManagementMode
    health: Literal["not_probed"] = "not_probed"


class ReadinessReport(RuntimeModel):
    """Serialized preparation readiness; no field claims a functioning runtime."""

    phase: Literal["8"] = "8"
    configuration: Literal["valid"] = "valid"
    application: Literal["experimental_phase8"] = "experimental_phase8"
    character: Literal["available_unprobed"] = "available_unprobed"
    voice: Literal["disabled", "configured_unprobed", "not_configured"]
    browser: Literal["disabled", "configured_unprobed"]
    model_pin: Literal["configured_unverified", "pending"]
    services: dict[ServiceName, ServiceHealth]
    odysseus: Literal["disabled"] = "disabled"
    blockers: tuple[str, ...]


def readiness(config: DeploymentConfig) -> ReadinessReport:
    """Report validated configuration only, without network probes or runtime startup."""
    return ReadinessReport(
        model_pin="configured_unverified" if config.model_pin_configured else "pending",
        voice=(
            "disabled"
            if not config.audio.enabled
            else "configured_unprobed"
            if config.audio.stt_model_directory is not None
            else "not_configured"
        ),
        browser="configured_unprobed" if config.browser.enabled else "disabled",
        services={
            name: ServiceHealth(mode=service.mode) for name, service in config.services.items()
        },
        blockers=(
            "Configuration validation does not probe installed speech artifacts, "
            "browser assets or service health.",
            "Phase 8 implementation is experimental; quality, scale and physical "
            "audio acceptance gates remain open.",
            (
                "Configured model pin needs fresh gateway health verification."
                if config.model_pin_configured
                else "Model pin configuration and phase 3 measurements are pending."
            ),
        ),
    )
