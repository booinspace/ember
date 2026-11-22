"""Verify one installed Strata profile and bind its owned launch to the gateway."""

import asyncio
import json
import os
import re
from hashlib import file_digest, sha256
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import Field, ValidationError

from .config import ConfigurationError, DeploymentConfig, RuntimeModel, StrataSettings
from .processes import OwnedProcess

STRATA_KEY_ENV = "EMBER_STRATA_API_KEY"
STRATA_INSTANCE_ENV = "EMBER_STRATA_INSTANCE_ID"
_STRATA_PROFILE_ENV = "EMBER_STRATA_LAUNCH_PROFILE"
_INSTANCE_PATTERN = r"[0-9a-f]{32}"
_MAX_JSON = 1024 * 1024
_SHARD = re.compile(r"(.+)-(\d{5})-of-(\d{5})\.gguf", re.IGNORECASE)

# ponytail: Initial qualification is for local text GGUF profiles with MTP or no drafter.
# Extend only with explicit artifact validation for new file arguments and model layouts.
_FILE_FLAGS = {"--native", "--native-dense-gguf", "--ple-gguf", "--embd-gguf", "--expert-profile"}
_DIR_FLAGS = {"--pack", "--mtp"}
_VALUE_FLAGS = {
    "--expert-cache",
    "--prefill",
    "--spec",
    "--spec-min-p",
    "--max-context",
    "--kv",
    "--resident-budget-gib",
    "--vram-reserve-mib",
    "--ple-io",
    "--kv-resident",
    "--rope-scaling",
    "--rope-scale",
    "--mtp-q4",
    "--mtp-hnorm",
    "--batch",
    "--slots",
    "--batch-mtp",
}
_SWITCH_FLAGS = {"--resident-experts", "--mmap-experts"}


class _RunnerProfile(RuntimeModel):
    """Supported native Strata JSON subset; other features must be explicitly qualified."""

    exe: str
    cwd: str
    tokenizer: str
    model_name: str
    args: list[str] = Field(min_length=2, max_length=128)
    ember_artifacts: dict[str, str] = Field(min_length=1, max_length=2048)
    host: str = "127.0.0.1"
    port: int | None = None
    backend: str = "cuda"
    cuda: int | None = None
    gpu: int | None = None
    lib_dirs: list[str] = Field(default_factory=list, max_length=16)
    drafting: str
    mtp: bool | None = None
    open_browser: bool = False
    lazy_load: bool = False
    idle_unload_s: float = 0.0


def _profile_binding(config: DeploymentConfig, settings: StrataSettings) -> str:
    # Re-read deployment TOML in the child, but reject profile changes since parent verification.
    identity = (
        settings.model_dump_json(),
        str(config.runtime_root),
        config.services.gateway.endpoint,
        config.model_tag,
        config.model_digest,
        config.context_tokens,
        config.preemption_enabled,
    )
    return sha256(json.dumps(identity).encode()).hexdigest()


def strata_gateway_environment(
    config: DeploymentConfig, settings: StrataSettings, token: str, instance: str
) -> dict[str, str]:
    """Pass a verified runner instance and its private bearer only to the owned gateway."""
    return {
        STRATA_KEY_ENV: token,
        STRATA_INSTANCE_ENV: instance,
        _STRATA_PROFILE_ENV: _profile_binding(config, settings),
    }


def managed_strata_token(config: DeploymentConfig) -> str:
    """Require managed launch provenance for this gateway deployment; never attach to Strata."""
    settings = config.inference.strata
    token = os.environ.get(STRATA_KEY_ENV, "")
    if (
        config.inference.backend != "strata"
        or settings is None
        or config.services.gateway.mode != "managed"
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
        or not re.fullmatch(_INSTANCE_PATTERN, os.environ.get(STRATA_INSTANCE_ENV, ""))
        or os.environ.get(_STRATA_PROFILE_ENV) != _profile_binding(config, settings)
    ):
        raise ConfigurationError("Strata requires an owned launch via ember start.")
    return token


def _absolute(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or "\0" in value or value.startswith(("//", "\\\\")):
        raise ConfigurationError("Strata profile requires absolute local paths.")
    return path


def _read_small(path: Path) -> bytes:
    if not path.is_file():
        raise ConfigurationError("Required local Strata file is missing.")
    with path.open("rb") as stream:
        value = stream.read(_MAX_JSON + 1)
    if len(value) > _MAX_JSON:
        raise ConfigurationError("Strata startup JSON exceeds its size limit.")
    return value


def _directory_error(error: OSError) -> None:
    raise error


def _unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ConfigurationError("Strata runner JSON contains duplicate keys.")
        result[name] = value
    return result


def _engine_paths(profile: _RunnerProfile, context: int) -> tuple[set[Path], set[Path]]:
    files: set[Path] = set()
    directories: set[Path] = set()
    options: dict[str, str] = {}
    arguments = iter(profile.args)
    for flag in arguments:
        if flag in options or flag not in _FILE_FLAGS | _DIR_FLAGS | _VALUE_FLAGS | _SWITCH_FLAGS:
            raise ConfigurationError("Unsupported or repeated Strata runner engine flag.")
        value = "" if flag in _SWITCH_FLAGS else next(arguments, "")
        if flag not in _SWITCH_FLAGS and (not value or value.startswith("--") or "\0" in value):
            raise ConfigurationError("Strata engine flags require separate explicit values.")
        if flag in _FILE_FLAGS:
            path = _absolute(value)
            files.add(path)
            if flag != "--expert-profile":
                if path.suffix.lower() != ".gguf":
                    raise ConfigurationError("Strata model inputs must be installed GGUF files.")
                match = _SHARD.fullmatch(path.name)
                if match:
                    prefix, index, count = match.groups()
                    if not 1 <= int(index) <= int(count) <= 32:
                        raise ConfigurationError("Invalid Strata GGUF shard numbering.")
                    files.update(
                        path.with_name(f"{prefix}-{number:05d}-of-{count}{path.suffix}")
                        for number in range(1, int(count) + 1)
                    )
        if flag in _DIR_FLAGS:
            directories.add(_absolute(value))
        options[flag] = value
    if not {"--native", "--pack"} <= options.keys() or options.get("--max-context") != str(context):
        raise ConfigurationError("Strata requires explicit local GGUF, pack and matching context.")
    if profile.drafting == "mtp":
        if "--mtp" not in options or profile.mtp is False:
            raise ConfigurationError("Strata MTP requires an explicitly pinned local pack.")
    elif profile.drafting != "off" or "--mtp" in options:
        raise ConfigurationError("Strata drafting must be off or use an explicit local MTP pack.")
    return files, directories


def prepare_strata_config(
    config: DeploymentConfig, settings: StrataSettings, snapshot: Path
) -> None:
    """Verify pinned config and artifacts before writing an isolated launch copy."""
    try:
        python = _absolute(settings.python_executable)
        script = _absolute(settings.server_script)
        config_path = _absolute(settings.runner_config)
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ConfigurationError("Installed Strata Python is unavailable.")
        value = _read_small(config_path)
        if sha256(value).hexdigest() != settings.runner_config_sha256:
            raise ConfigurationError("Strata runner config SHA-256 does not match.")
        if config.model_digest.removeprefix("sha256:") != settings.runner_config_sha256:
            raise ConfigurationError("Strata model pin must match its runner config SHA-256.")
        raw: object = json.loads(value.decode("utf-8-sig"), object_pairs_hook=_unique_members)
        profile = _RunnerProfile.model_validate(raw)
        if (
            profile.host != "127.0.0.1"
            or profile.port not in {None, urlsplit(settings.endpoint).port}
            or profile.model_name != config.model_tag
            or profile.open_browser
            or profile.lazy_load
            or profile.idle_unload_s != 0
        ):
            raise ConfigurationError(
                "Strata profile must load its pinned model eagerly and locally."
            )
        if not _absolute(profile.cwd).is_dir() or any(
            not _absolute(directory).is_dir() for directory in profile.lib_dirs
        ):
            raise ConfigurationError("Strata installed working or library directory is missing.")
        files, directories = _engine_paths(profile, config.context_tokens)
        model_files = {
            path.resolve(strict=True) for path in files if path.suffix.lower() == ".gguf"
        }
        exe = _absolute(profile.exe)
        files.update((script, exe))
        tokenizer = _absolute(profile.tokenizer)
        directories.add(tokenizer)
        files.update(tokenizer / name for name in ("vocab.json", "merges.txt", "token_type.json"))
        pins: dict[Path, str] = {}
        for name, digest in profile.ember_artifacts.items():
            path = _absolute(name).resolve(strict=True)
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or path in pins or not path.is_file():
                raise ConfigurationError("Invalid Strata artifact SHA-256 manifest.")
            pins[path] = digest
        for directory in directories:
            if not directory.is_dir():
                raise ConfigurationError("A pinned Strata model directory is missing.")
            for root, subdirs, names in directory.walk(on_error=_directory_error):
                if any((root / name).is_junction() for name in subdirs):
                    raise ConfigurationError(
                        "Junctions are not supported inside Strata model directories."
                    )
                for name in names:
                    path = root / name
                    if not path.is_file() or path.resolve(strict=True) not in pins:
                        raise ConfigurationError(
                            "Strata model directory contains an unpinned file."
                        )
        if not {file.resolve(strict=True) for file in files} <= pins.keys():
            raise ConfigurationError("Strata runner references an unpinned artifact.")
        for artifact, digest in pins.items():
            if not settings.verify_model_checksums and artifact in model_files:
                continue
            with artifact.open("rb") as stream:
                if file_digest(stream, "sha256").hexdigest() != digest:
                    raise ConfigurationError("Strata artifact SHA-256 does not match.")
        with snapshot.open("xb") as stream:
            stream.write(value)
    except (OSError, ValidationError, ValueError) as error:
        if isinstance(error, ConfigurationError):
            raise
        raise ConfigurationError(
            "Invalid or unavailable installed Strata profile files."
        ) from error


class _ReadyRecord(RuntimeModel):
    schema_version: int
    instance_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    pid: int = Field(gt=0)
    api_url: str
    bound_host: str
    bound_port: int
    model_id: str
    model_state: str
    api_state: str
    created_at: float


class _Model(RuntimeModel):
    model_config = {"strict": True, "extra": "ignore"}

    id: str
    max_model_len: int
    status: dict[str, str]


class _Models(RuntimeModel):
    model_config = {"strict": True, "extra": "ignore"}

    data: list[_Model]


class _Telemetry(RuntimeModel):
    model_config = {"strict": True, "extra": "ignore"}

    instance: dict[str, object]


async def _metadata(client: httpx.AsyncClient, route: str, token: str) -> bytes:
    headers = {"Authorization": "Bearer " + token, "Accept-Encoding": "identity"}
    async with client.stream("GET", route, headers=headers) as response:
        response.raise_for_status()
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ConfigurationError("Strata returned unsupported metadata encoding.")
        result = bytearray()
        async for chunk in response.aiter_raw():
            if len(result) + len(chunk) > _MAX_JSON:
                raise ConfigurationError("Strata startup metadata exceeds its size limit.")
            result.extend(chunk)
        return bytes(result)


async def await_strata_ready(
    config: DeploymentConfig, settings: StrataSettings, child: OwnedProcess, path: Path, token: str
) -> str:
    """Prove owned readiness with a fresh native record and authenticated metadata."""
    endpoint = settings.endpoint.rstrip("/")
    try:
        async with asyncio.timeout(settings.startup_timeout_seconds):
            while True:
                child.check()
                if await asyncio.to_thread(path.is_file):
                    try:
                        record = _ReadyRecord.model_validate_json(
                            await asyncio.to_thread(_read_small, path)
                        )
                    except ValidationError:
                        # Native ready-file publishing uses O_EXCL then writes JSON.
                        await asyncio.sleep(0.1)
                        continue
                    break
                await asyncio.sleep(0.1)
            if (
                record.api_url != endpoint
                or record.bound_host != "127.0.0.1"
                or record.bound_port != urlsplit(endpoint).port
                or record.model_id != config.model_tag
                or record.model_state != "ready"
                or record.api_state != "ready"
            ):
                raise ConfigurationError("Owned Strata ready record does not match deployment.")
            async with httpx.AsyncClient(base_url=endpoint, timeout=3, trust_env=False) as client:
                async with client.stream("GET", "/v1/models") as response:
                    if response.status_code != 401:
                        raise ConfigurationError("Strata model endpoint does not require a bearer.")
                models = _Models.model_validate_json(await _metadata(client, "/v1/models", token))
                if not any(
                    model.id == config.model_tag
                    and model.max_model_len == config.context_tokens
                    and model.status.get("value") == "loaded"
                    for model in models.data
                ):
                    raise ConfigurationError(
                        "Owned Strata model or context does not match deployment."
                    )
                telemetry = _Telemetry.model_validate_json(
                    await _metadata(client, "/v1/telemetry", token)
                )
                if telemetry.instance.get("id") != record.instance_id:
                    raise ConfigurationError("Strata endpoint is not the owned runner instance.")
            child.check()
            return record.instance_id
    except (TimeoutError, httpx.HTTPError, ValidationError) as error:
        raise ConfigurationError("Owned Strata did not become authenticated-ready.") from error
