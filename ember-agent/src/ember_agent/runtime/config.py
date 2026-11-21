"""Offline validated deployment contracts; never starts services or generation."""

import json
import re
import tomllib
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from .mcp_contracts import MCPServerSettings

type ManagementMode = Literal["attach", "managed", "disabled"]
type ServiceName = Literal["ollama", "vllm", "livekit", "tts", "gateway"]
type SpeechSpeed = Annotated[float, Field(ge=0.75, le=1.5, allow_inf_nan=False)]
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


class VLLMSettings(RuntimeModel):
    """An explicitly installed external runner; setup and GPU ownership remain external."""

    endpoint: str
    version: Literal["0.31.0"] = "0.31.0"
    artifact_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    profile_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    api_key_environment: Annotated[str, Field(pattern=r"^(?:[A-Za-z_][A-Za-z0-9_]{0,127})?$")] = ""

    @model_validator(mode="after")
    def _local_origin(self) -> Self:
        _validate_endpoint(ServiceSettings(endpoint=self.endpoint, mode="attach"), "vllm")
        return self


class InferenceSettings(RuntimeModel):
    """Select exactly one upstream behind the unchanged gateway and admission policy."""

    backend: Literal["ollama", "vllm"] = "ollama"
    vllm: VLLMSettings | None = None

    @model_validator(mode="after")
    def _selected_runner(self) -> Self:
        if self.backend == "vllm" and self.vllm is None:
            raise ValueError("vLLM requires an explicit installed runner profile.")
        return self


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


class TTSServiceSettings(ServiceSettings):
    """An installed local speech command; arguments are passed directly without a shell."""

    executable: str = ""
    arguments: tuple[str, ...] = ()
    working_directory: Path | None = None
    startup_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 20.0


class _TTSServiceSettings(ServiceSettings):
    executable: str = ""
    arguments: list[str] = Field(default_factory=list, max_length=32)
    working_directory: str = ""
    startup_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 20.0


class DeployedServices(RuntimeModel):
    """Named runtime services; port availability alone never establishes ownership."""

    ollama: ServiceSettings
    livekit: LiveKitSettings
    tts: TTSServiceSettings
    gateway: ServiceSettings

    def items(self) -> tuple[tuple[ServiceName, ServiceSettings], ...]:
        """Iterate the fixed service set with typed identities and settings."""
        return (
            ("ollama", self.ollama),
            ("livekit", self.livekit),
            ("tts", self.tts),
            ("gateway", self.gateway),
        )


class BrowserSettings(RuntimeModel):
    """App-owned controls; optional explicit private IPv4 LAN access retains operator auth."""

    enabled: bool = False
    endpoint: str = "http://127.0.0.1:8787"
    lan_address: str | None = None
    public_origin: str = ""
    trusted_proxy: str = ""
    assets: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "frontend/dist"
    )


class _BrowserSettings(RuntimeModel):
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:8787"
    assets: str = ""
    lan_address: str = ""
    public_origin: str = ""
    trusted_proxy: str = ""


class AudioSettings(RuntimeModel):
    """CPU speech is optional and loads only explicit installed model artifacts."""

    enabled: bool = False
    stt_model_directory: Path | None = None
    phonon_endpoint: Literal["", "http://127.0.0.1:8010"] = ""
    phonon_executable: Path | None = None
    phonon_cache_directory: Path | None = None
    stt_threads: Annotated[int, Field(ge=1, le=4)] = 2
    language: Annotated[str, Field(min_length=2, max_length=8)] = "en"
    tts_model: Annotated[str, Field(min_length=1, max_length=128)] = "tts-1"
    tts_voice: Annotated[str, Field(min_length=1, max_length=128)] = "default"
    tts_speed: SpeechSpeed = 1.0


class InterpretationSettings(RuntimeModel):
    """Independent opt-in; installing deep recall never enables serial foreground interpretation."""

    enabled: bool = False
    timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 8.0


def _harness_inputs(command: tuple[str, ...], origin: str) -> None:
    endpoint = urlsplit(origin)
    if (
        endpoint.scheme not in {"http", "https"}
        or not endpoint.hostname
        or endpoint.username is not None
        or endpoint.password is not None
        or endpoint.path not in {"", "/"}
        or endpoint.query
        or endpoint.fragment
        or any(not argument or len(argument) > 4096 for argument in command)
    ):
        raise ValueError("A harness requires an explicit search origin and installed command.")


class HermesSettings(RuntimeModel):
    """Explicit installed isolated worker; its launcher never receives runner credentials."""

    command: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    revision: Literal["71736c9f32597bbca9d89cede5aa136e55946b2f"]
    profile_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    search_endpoint: str
    private_recall: bool = False
    tools: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def installed_origin(self) -> Self:
        _harness_inputs(self.command, self.search_endpoint)
        return self


class HarnessSettings(RuntimeModel):
    """One selected task adapter; SDK compatibility is validated by that adapter."""

    provider: str = Field(default="hermes", pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    command: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    revision: str = Field(min_length=1, max_length=128)
    profile_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    search_endpoint: str
    private_recall: bool = False
    tools: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def installed_inputs(self) -> Self:
        """Retain legacy installation checks without importing a provider SDK."""
        _harness_inputs(self.command, self.search_endpoint)
        if len(set(self.tools)) != len(self.tools) or any(len(tool) > 128 for tool in self.tools):
            raise ValueError("Harness tool grants must be unique bounded identities.")
        return self


class PublicSearchSettings(RuntimeModel):
    """Explicit installed search-only command, independent of any Desktop client."""

    provider: str = Field(default="command", pattern=r"^[a-z][a-z0-9_-]{0,31}$")

    command: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def installed_command(self) -> Self:
        if not Path(self.command[0]).is_absolute() or any(
            not part or len(part) > 4096 for part in self.command
        ):
            raise ValueError("Public search requires an absolute installed command.")
        return self


class HermesClientSettings(RuntimeModel):
    """Optional paired native client; recalled replies remain in its separate local history."""

    private_recall: bool = False
    search_command: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    input_ceiling: Annotated[int, Field(ge=4096, le=24576)] = 16384

    @model_validator(mode="after")
    def installed_command(self) -> Self:
        if any(not part or len(part) > 4096 for part in self.search_command):
            raise ValueError("The native search adapter requires explicit installed arguments.")
        if self.search_command and not Path(self.search_command[0]).is_absolute():
            raise ValueError("The native search adapter executable must be absolute.")
        return self


class MemoryMCPSettings(RuntimeModel):
    """Opt-in read-only listener; host and credential location are not configurable."""

    port: Annotated[int, Field(ge=1024, le=65535)] = 8790


class DeploymentConfig(RuntimeModel):
    """Resolved deployment configuration, distinct from verified service/model health."""

    active_profile: str
    system_prompt_file: Path | None = None
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
    mcp_servers: tuple[MCPServerSettings, ...] = ()
    memory_mcp: MemoryMCPSettings | None = None
    inference: InferenceSettings = InferenceSettings()
    hermes: HermesSettings | None = None
    hermes_client: HermesClientSettings | None = None
    harness: HarnessSettings | None = None
    public_search: PublicSearchSettings | None = None

    @property
    def selected_harness(self) -> HarnessSettings | None:
        """Normalize the legacy declaration at composition, keeping callers provider-neutral."""
        if self.harness is not None:
            return self.harness
        if self.hermes is not None:
            return HarnessSettings.model_validate_json(self.hermes.model_dump_json())
        return None

    @property
    def model_pin_configured(self) -> bool:
        """Indicate configuration presence; actual runner identity remains unverified."""
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
    tts: _TTSServiceSettings = Field(validation_alias=AliasChoices("tts", "kokoro"))


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
    tts_model: Annotated[str, Field(min_length=1, max_length=128)] = "tts-1"
    tts_voice: Annotated[str, Field(min_length=1, max_length=128)]
    tts_speed: SpeechSpeed = 1.0
    enabled: bool = False
    stt_model_directory: str = ""
    phonon_endpoint: Literal["", "http://127.0.0.1:8010"] = ""
    phonon_executable: str = ""
    phonon_cache_directory: str = ""
    stt_threads: Annotated[int, Field(ge=1, le=4)] = 2
    language: Annotated[str, Field(min_length=2, max_length=8)] = "en"


class _SearchSettings(RuntimeModel):
    embedding_directory: str = ""
    timezone: str = "Europe/London"


class _PromptSettings(RuntimeModel):
    system_file: Annotated[str, Field(min_length=1)] = "prompts/system.md"


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
    prompts: _PromptSettings = _PromptSettings()
    memory_mcp: MemoryMCPSettings | None = None
    inference: InferenceSettings = InferenceSettings()
    hermes: dict[str, JsonValue] | None = None
    hermes_client: dict[str, JsonValue] | None = None
    harness: dict[str, JsonValue] | None = None
    public_search: dict[str, JsonValue] | None = None
    mcp_servers: list[dict[str, JsonValue]] = Field(
        default_factory=list[dict[str, JsonValue]], max_length=8
    )


def _read_settings(path: Path) -> _DeploymentSettings:
    try:
        with path.open("rb") as stream:
            parsed: object = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigurationError("Cannot read a valid deployment TOML file.") from error
    try:
        settings = _DeploymentSettings.model_validate(parsed)
        raw_services: object = parsed["services"]
        if (
            isinstance(raw_services, dict)
            and "kokoro" in raw_services
            and "tts_model" not in settings.audio.model_fields_set
        ):
            # Preserve the historical request model only at the legacy TOML boundary.
            settings = settings.model_copy(
                update={"audio": settings.audio.model_copy(update={"tts_model": "kokoro"})}
            )
        return settings
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
        raise ConfigurationError("Actual model tag and digest are still required.")
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
        tts=TTSServiceSettings(
            endpoint=settings.services.tts.endpoint,
            mode=settings.services.tts.mode,
            executable=(
                str((base / settings.services.tts.executable).resolve())
                if "/" in settings.services.tts.executable
                or "\\" in settings.services.tts.executable
                else settings.services.tts.executable
            ),
            arguments=tuple(settings.services.tts.arguments),
            working_directory=(base / settings.services.tts.working_directory).resolve()
            if settings.services.tts.working_directory
            else None,
            startup_timeout_seconds=settings.services.tts.startup_timeout_seconds,
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
    if services.ollama.mode != "attach" and not (
        settings.inference.backend == "vllm" and services.ollama.mode == "disabled"
    ):
        raise ConfigurationError("Ollama must remain independently managed in this deployment.")
    if settings.gateway.preemption_enabled:
        raise ConfigurationError(
            "Preemption must remain disabled until measured phase 13 acceptance."
        )
    upstream_endpoint = services.ollama.endpoint
    if settings.inference.backend == "vllm":
        assert settings.inference.vllm is not None
        upstream_endpoint = settings.inference.vllm.endpoint
    if urlsplit(services.gateway.endpoint).port == urlsplit(upstream_endpoint).port:
        raise ConfigurationError("Gateway and inference upstream must have distinct endpoints.")
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
    if bool(settings.audio.phonon_executable) != bool(settings.audio.phonon_cache_directory) or (
        settings.audio.phonon_executable and not settings.audio.phonon_endpoint
    ):
        raise ConfigurationError("Managed Phonon requires an endpoint, executable and cache.")
    data_root, runtime_root = _resolve_roots(settings.paths, path.resolve().parent)
    tag, digest = _validate_model(settings.model, require_model_pin)
    _validate_endpoint(
        ServiceSettings(endpoint=settings.browser.endpoint, mode="managed"), "browser"
    )
    lan_address = (
        private_lan_address(settings.browser.lan_address) if settings.browser.lan_address else None
    )
    public_origin = settings.browser.public_origin.rstrip("/")
    proxy = settings.browser.trusted_proxy
    if bool(public_origin) != bool(proxy) or (public_origin and not lan_address):
        raise ConfigurationError(
            "Reverse proxy requires a LAN address, public origin and trusted proxy."
        )
    if public_origin:
        try:
            address = urlsplit(public_origin)
            public_port = address.port
        except ValueError as error:
            raise ConfigurationError("browser.public_origin is invalid.") from error
        if (
            address.scheme != "https"
            or not address.hostname
            or address.username
            or address.password
            or address.path
            or address.query
            or address.fragment
            or public_port == 0
            or address.netloc == urlsplit(settings.browser.endpoint).netloc
        ):
            raise ConfigurationError("browser.public_origin must be a distinct HTTPS origin.")
        private_lan_address(proxy)
        if urlsplit(settings.browser.endpoint).port != 8787:
            raise ConfigurationError("Reverse proxy requires browser port 8787.")
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
    try:
        mcp_servers = tuple(
            MCPServerSettings.model_validate_json(json.dumps(value))
            for value in settings.mcp_servers
        )
        if len({server.id for server in mcp_servers}) != len(mcp_servers):
            raise ConfigurationError("MCP server identities must be unique.")
    except (ValidationError, ValueError) as error:
        raise ConfigurationError("MCP server configuration is invalid.") from error
    try:
        if settings.harness is not None and settings.hermes is not None:
            raise ValueError("Use either harness or the legacy Hermes section.")
        harness = (
            HarnessSettings.model_validate_json(json.dumps(settings.harness))
            if settings.harness is not None
            else None
        )
        public_search = (
            PublicSearchSettings.model_validate_json(json.dumps(settings.public_search))
            if settings.public_search is not None
            else None
        )
        hermes = (
            HermesSettings.model_validate_json(json.dumps(settings.hermes))
            if settings.hermes is not None
            else None
        )
        hermes_client = (
            HermesClientSettings.model_validate_json(json.dumps(settings.hermes_client))
            if settings.hermes_client is not None
            else None
        )
        if (
            public_search
            and hermes_client
            and hermes_client.search_command
            and public_search.command != hermes_client.search_command
        ):
            raise ValueError("Conflicting public-search and legacy client search commands.")
        if hermes_client is not None and not settings.browser.enabled:
            raise ValueError("The Hermes client requires the application control listener.")
    except (ValidationError, ValueError) as error:
        raise ConfigurationError("Hermes configuration is invalid.") from error
    return DeploymentConfig(
        harness=harness,
        public_search=public_search,
        hermes=hermes,
        hermes_client=hermes_client,
        mcp_servers=mcp_servers,
        memory_mcp=settings.memory_mcp,
        inference=settings.inference,
        active_profile=settings.active_profile,
        system_prompt_file=(path.resolve().parent / settings.prompts.system_file).absolute(),
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
            phonon_executable=(path.resolve().parent / settings.audio.phonon_executable).resolve()
            if settings.audio.phonon_executable
            else None,
            phonon_cache_directory=(
                path.resolve().parent / settings.audio.phonon_cache_directory
            ).resolve()
            if settings.audio.phonon_cache_directory
            else None,
            stt_threads=settings.audio.stt_threads,
            language=settings.audio.language,
            tts_model=settings.audio.tts_model,
            tts_voice=settings.audio.tts_voice,
            tts_speed=settings.audio.tts_speed,
        ),
        browser=BrowserSettings(
            enabled=settings.browser.enabled,
            endpoint=settings.browser.endpoint,
            lan_address=lan_address,
            public_origin=public_origin,
            trusted_proxy=proxy,
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
