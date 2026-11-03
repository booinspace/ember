"""Offline validated deployment contracts; never starts services or generation."""

import re
import tomllib
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

type ManagementMode = Literal["attach", "managed", "disabled"]
type ServiceName = Literal["ollama", "livekit", "kokoro", "gateway"]
type PositiveInteger = Annotated[int, Field(gt=0)]


class ConfigurationError(ValueError):
    """Deployment cannot be interpreted safely; messages omit untrusted values."""


class RuntimeModel(BaseModel):
    """Validate runtime contracts strictly without permitting undeclared fields."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ServiceSettings(RuntimeModel):
    """A local origin and explicit service ownership mode."""

    endpoint: str
    mode: ManagementMode


class LiveKitSettings(ServiceSettings):
    """Installed server identity, private credentials and bounded lifecycle settings."""

    executable: str = "livekit-server"
    credentials_file: Path | None = None
    tcp_port: Annotated[int, Field(gt=0, le=65535)] = 7881
    udp_port: Annotated[int, Field(gt=0, le=65535)] = 7882
    startup_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 20.0
    shutdown_timeout_seconds: Annotated[float, Field(gt=0, le=200)] = 30.0


class _LiveKitSettings(ServiceSettings):
    executable: Annotated[str, Field(min_length=1)] = "livekit-server"
    credentials_file: str = ""
    tcp_port: Annotated[int, Field(gt=0, le=65535)] = 7881
    udp_port: Annotated[int, Field(gt=0, le=65535)] = 7882
    startup_timeout_seconds: Annotated[float, Field(gt=0, le=120)] = 20.0
    shutdown_timeout_seconds: Annotated[float, Field(gt=0, le=200)] = 30.0


class KokoroSettings(ServiceSettings):
    """An installed local speech command; arguments are passed directly without a shell."""

    executable: str = ""
    arguments: tuple[str, ...] = ()
    working_directory: Path | None = None
    startup_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 20.0


class _KokoroSettings(ServiceSettings):
    executable: str = ""
    arguments: list[str] = Field(default_factory=list, max_length=32)
    working_directory: str = ""
    startup_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 20.0


class DeployedServices(RuntimeModel):
    """Named runtime services; port availability alone never establishes ownership."""

    ollama: ServiceSettings
    livekit: LiveKitSettings
    kokoro: KokoroSettings
    gateway: ServiceSettings

    def items(self) -> tuple[tuple[ServiceName, ServiceSettings], ...]:
        """Iterate the fixed service set with typed identities and settings."""
        return (
            ("ollama", self.ollama),
            ("livekit", self.livekit),
            ("kokoro", self.kokoro),
            ("gateway", self.gateway),
        )


class BrowserSettings(RuntimeModel):
    """App-owned controls; optional explicit private IPv4 LAN access retains operator auth."""

    enabled: bool = False
    endpoint: str = "http://127.0.0.1:8787"
    lan_address: str | None = None
    assets: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "frontend/dist"
    )


class _BrowserSettings(RuntimeModel):
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:8787"
    assets: str = ""
    lan_address: str = ""


class AudioSettings(RuntimeModel):
    """CPU speech is optional and loads only explicit installed model artifacts."""

    enabled: bool = False
    stt_model_directory: Path | None = None
    phonon_endpoint: Literal["", "http://127.0.0.1:8010"] = ""
    stt_threads: Annotated[int, Field(ge=1, le=4)] = 2
    language: Annotated[str, Field(min_length=2, max_length=8)] = "en"
    tts_voice: Annotated[str, Field(min_length=1)] = "af_heart"


class InterpretationSettings(RuntimeModel):
    """Independent opt-in; installing deep recall never enables serial foreground interpretation."""

    enabled: bool = False
    timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 8.0


class DeploymentConfig(RuntimeModel):
    """Resolved deployment configuration, distinct from verified service/model health."""

    active_profile: str
    data_root: Path
    runtime_root: Path
    model_tag: str
    model_digest: str
    context_tokens: PositiveInteger
    max_input_tokens: PositiveInteger
    output_reserve_tokens: PositiveInteger
    services: DeployedServices
    embedding_directory: Path | None = None
    recall_timezone: str = "Europe/London"
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    interpretation: InterpretationSettings = Field(default_factory=InterpretationSettings)

    @property
    def model_pin_configured(self) -> bool:
        """Indicate configuration presence; actual Ollama identity remains unverified."""
        return bool(self.model_tag and self.model_digest)


class _PathSettings(RuntimeModel):
    data_root: Annotated[str, Field(min_length=1)]
    runtime_root: Annotated[str, Field(min_length=1)]


class _ModelSettings(RuntimeModel):
    tag: str
    expected_digest: str
    context_tokens: PositiveInteger
    max_input_tokens: PositiveInteger
    output_reserve_tokens: PositiveInteger


class _SupportServices(RuntimeModel):
    ollama: ServiceSettings
    livekit: _LiveKitSettings
    kokoro: _KokoroSettings


class _GatewaySettings(RuntimeModel):
    endpoint: str
    mode: ManagementMode
    preemption_enabled: bool


class _ProviderSettings(RuntimeModel):
    mode: ManagementMode


class _Providers(RuntimeModel):
    odysseus: _ProviderSettings


class _AudioSettings(RuntimeModel):
    stt_device: Literal["cpu"]
    tts_voice: Annotated[str, Field(min_length=1)]
    enabled: bool = False
    stt_model_directory: str = ""
    phonon_endpoint: Literal["", "http://127.0.0.1:8010"] = ""
    stt_threads: Annotated[int, Field(ge=1, le=4)] = 2
    language: Annotated[str, Field(min_length=2, max_length=8)] = "en"


class _SearchSettings(RuntimeModel):
    embedding_directory: str = ""
    timezone: str = "Europe/London"


class _DeploymentSettings(RuntimeModel):
    config_version: Annotated[int, Field(ge=1, le=1)]
    active_profile: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    paths: _PathSettings
    model: _ModelSettings
    services: _SupportServices
    gateway: _GatewaySettings
    providers: _Providers
    audio: _AudioSettings
    search: _SearchSettings = _SearchSettings()
    browser: _BrowserSettings = _BrowserSettings()
    interpretation: InterpretationSettings = InterpretationSettings()


def _read_settings(path: Path) -> _DeploymentSettings:
    try:
        with path.open("rb") as stream:
            parsed: object = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError("Cannot read a valid deployment TOML file.") from error
    try:
        return _DeploymentSettings.model_validate(parsed)
    except ValidationError as error:
        # Pydantic error strings include input values, potentially credentials or personal paths.
        raise ConfigurationError(
            "Deployment fields have invalid types, values, or unknown keys."
        ) from error


def _validate_endpoint(settings: ServiceSettings, name: ServiceName | Literal["browser"]) -> None:
    schemes = {"ws", "wss"} if name == "livekit" else {"http"}
    try:
        parsed = urlsplit(settings.endpoint)
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError(f"{name}.endpoint is invalid.") from error
    if (
        parsed.scheme not in schemes
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is None
        or port <= 0
    ):
        raise ConfigurationError(f"{name}.endpoint must be a local origin with an explicit port.")


def _resolve_roots(settings: _PathSettings, base: Path) -> tuple[Path, Path]:
    data_root = (base / settings.data_root).resolve()
    runtime_root = (base / settings.runtime_root).resolve()
    if (
        data_root == runtime_root
        or data_root in runtime_root.parents
        or runtime_root in data_root.parents
    ):
        raise ConfigurationError("data_root and runtime_root must be separate non-nested roots.")
    return data_root, runtime_root


def _validate_model(settings: _ModelSettings, require_model_pin: bool) -> tuple[str, str]:
    tag = settings.tag.strip()
    digest = settings.expected_digest.strip()
    if digest and not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", digest):
        raise ConfigurationError("expected_digest must be a lowercase SHA-256 digest.")
    if bool(tag) != bool(digest):
        raise ConfigurationError(
            "Configure both model tag and expected_digest, or leave both pending."
        )
    if require_model_pin and not tag:
        raise ConfigurationError("Actual Ollama model tag and digest are still required.")
    if settings.max_input_tokens + settings.output_reserve_tokens > settings.context_tokens:
        raise ConfigurationError("Input budget plus output reserve exceeds context_tokens.")
    return tag, digest


def _validate_services(
    settings: _DeploymentSettings, base: Path, runtime_root: Path
) -> DeployedServices:
    services = DeployedServices(
        ollama=settings.services.ollama,
        livekit=LiveKitSettings(
            endpoint=settings.services.livekit.endpoint,
            mode=settings.services.livekit.mode,
            executable=(
                str((base / settings.services.livekit.executable).resolve())
                if "/" in settings.services.livekit.executable
                or "\\" in settings.services.livekit.executable
                else settings.services.livekit.executable
            ),
            credentials_file=(
                (base / settings.services.livekit.credentials_file).resolve()
                if settings.services.livekit.credentials_file
                else runtime_root / "livekit-credentials.json"
            ),
            tcp_port=settings.services.livekit.tcp_port,
            udp_port=settings.services.livekit.udp_port,
            startup_timeout_seconds=settings.services.livekit.startup_timeout_seconds,
            shutdown_timeout_seconds=settings.services.livekit.shutdown_timeout_seconds,
        ),
        kokoro=KokoroSettings(
            endpoint=settings.services.kokoro.endpoint,
            mode=settings.services.kokoro.mode,
            executable=(
                str((base / settings.services.kokoro.executable).resolve())
                if "/" in settings.services.kokoro.executable
                or "\\" in settings.services.kokoro.executable
                else settings.services.kokoro.executable
            ),
            arguments=tuple(settings.services.kokoro.arguments),
            working_directory=(base / settings.services.kokoro.working_directory).resolve()
            if settings.services.kokoro.working_directory
            else None,
            startup_timeout_seconds=settings.services.kokoro.startup_timeout_seconds,
        ),
        gateway=ServiceSettings(endpoint=settings.gateway.endpoint, mode=settings.gateway.mode),
    )
    for name, service in services.items():
        _validate_endpoint(service, name)
    signal_port = urlsplit(services.livekit.endpoint).port
    if services.livekit.mode == "managed" and urlsplit(services.livekit.endpoint).hostname == "::1":
        raise ConfigurationError("Managed LiveKit requires IPv4 loopback; attach for IPv6.")
    if len({signal_port, services.livekit.tcp_port, services.livekit.udp_port}) != 3:
        raise ConfigurationError("LiveKit signalling, TCP and UDP ports must differ.")
    if services.livekit.mode == "managed" and services.livekit.endpoint.startswith("wss:"):
        raise ConfigurationError("Managed local LiveKit uses ws; TLS requires an attached server.")
    if services.ollama.mode != "attach":
        raise ConfigurationError("Ollama must remain independently managed in this deployment.")
    if settings.gateway.preemption_enabled:
        raise ConfigurationError(
            "Preemption must remain disabled until measured phase 13 acceptance."
        )
    if urlsplit(services.gateway.endpoint).port == urlsplit(services.ollama.endpoint).port:
        raise ConfigurationError("Gateway and Ollama must have distinct endpoints.")
    if settings.providers.odysseus.mode != "disabled":
        raise ConfigurationError("Odysseus is not implemented yet; keep its mode disabled.")
    return services


def private_lan_address(value: str) -> str:
    """Accept a literal RFC1918 IPv4 address, never wildcard/public/DNS/loopback hosts."""
    try:
        address = IPv4Address(value)
    except ValueError as error:
        raise ConfigurationError("browser.lan_address requires a private IPv4 address.") from error
    if not any(
        address in IPv4Network(network)
        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ):
        raise ConfigurationError("browser.lan_address must belong to a private LAN.")
    return str(address)


def load_config(path: Path, *, require_model_pin: bool = False) -> DeploymentConfig:
    """Validate deployment and resolve paths relative to its file, without creating state.

    Missing model pins are allowed for phase -1 unless explicitly required. Invalid
    configuration raises ConfigurationError with safe diagnostics and chained causes.
    This blocking maintenance API must not run directly on a realtime event loop.
    """
    settings = _read_settings(path)
    data_root, runtime_root = _resolve_roots(settings.paths, path.resolve().parent)
    tag, digest = _validate_model(settings.model, require_model_pin)
    _validate_endpoint(
        ServiceSettings(endpoint=settings.browser.endpoint, mode="managed"), "browser"
    )
    lan_address = (
        private_lan_address(settings.browser.lan_address) if settings.browser.lan_address else None
    )
    if lan_address and (
        not settings.browser.enabled
        or urlsplit(settings.browser.endpoint).hostname == "::1"
        or settings.services.livekit.mode != "managed"
        or urlsplit(settings.services.livekit.endpoint).hostname == "::1"
    ):
        raise ConfigurationError("LAN access requires an enabled browser and managed IPv4 LiveKit.")
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        ZoneInfo(settings.search.timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigurationError("Configured recall timezone is unavailable; run setup.") from error
    return DeploymentConfig(
        active_profile=settings.active_profile,
        data_root=data_root,
        runtime_root=runtime_root,
        model_tag=tag,
        model_digest=digest,
        context_tokens=settings.model.context_tokens,
        max_input_tokens=settings.model.max_input_tokens,
        output_reserve_tokens=settings.model.output_reserve_tokens,
        services=_validate_services(settings, path.resolve().parent, runtime_root),
        recall_timezone=settings.search.timezone,
        interpretation=settings.interpretation,
        audio=AudioSettings(
            enabled=settings.audio.enabled,
            stt_model_directory=(
                path.resolve().parent / settings.audio.stt_model_directory
            ).resolve()
            if settings.audio.stt_model_directory
            else None,
            phonon_endpoint=settings.audio.phonon_endpoint,
            stt_threads=settings.audio.stt_threads,
            language=settings.audio.language,
            tts_voice=settings.audio.tts_voice,
        ),
        browser=BrowserSettings(
            enabled=settings.browser.enabled,
            endpoint=settings.browser.endpoint,
            lan_address=lan_address,
            assets=(path.resolve().parent / settings.browser.assets).resolve()
            if settings.browser.assets
            else BrowserSettings().assets,
        ),
        embedding_directory=(
            (path.resolve().parent / settings.search.embedding_directory).resolve()
            if settings.search.embedding_directory
            else None
        ),
    )
