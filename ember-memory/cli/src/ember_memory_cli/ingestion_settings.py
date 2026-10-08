"""Read-only maintenance view of deployment v1; no dependency on the agent product."""

import tomllib
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field

from ember_memory_abi.common import CanonicalId, ContractModel
from ember_memory_engine.processing_contracts import ProcessingError

type SourceKind = Literal["summary", "archive", "all"]


class IngestionArguments(ContractModel):
    """Explicit workflow choices, narrowed before opening canonical state."""

    source: Path | None
    config: Path | None
    root: Path | None
    store: CanonicalId | None
    job: CanonicalId | None
    character: CanonicalId | None
    source_namespace: str | None
    format: Literal["auto", "text", "json", "chatgpt-zip"]
    text_field: str
    source_kind: SourceKind
    key: str | None
    model: str | None
    digest: str | None
    gateway: str | None
    token_file: Path | None
    encoder: Path | None
    window_attempts: Annotated[int, Field(ge=1, le=3)]
    max_windows: Annotated[int, Field(ge=1)] | None
    skip_index: bool


class IngestionSettings(ContractModel):
    """Resolved local paths/model pins; no services are started."""

    root: Path
    character: CanonicalId | None
    model: str | None
    digest: str | None
    gateway: str
    token_file: Path | None
    encoder: Path | None


class _Subset(ContractModel):
    model_config = {"strict": True, "frozen": True, "extra": "ignore"}


class _Paths(_Subset):
    data_root: Annotated[str, Field(min_length=1)]
    runtime_root: Annotated[str, Field(min_length=1)]


class _Model(_Subset):
    tag: str = ""
    expected_digest: str = ""


class _Gateway(_Subset):
    endpoint: str


class _Search(_Subset):
    embedding_directory: str = ""


class _Deployment(_Subset):
    config_version: Annotated[int, Field(ge=1, le=1)]
    active_profile: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    paths: _Paths
    model: _Model
    gateway: _Gateway
    search: _Search = _Search()


class _Binding(ContractModel):
    character_id: CanonicalId


def settings_for(args: IngestionArguments) -> IngestionSettings:
    """Consume deployment-v1 maintenance fields with runtime-compatible path resolution."""
    root, character = args.root, args.character
    model, digest = args.model, args.digest
    gateway, token, encoder = args.gateway, args.token_file, args.encoder
    if args.config is not None:
        path = args.config.resolve()
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ProcessingError("ingest_config_too_large")
        parsed: object = tomllib.loads(raw.decode("utf-8"))
        config = _Deployment.model_validate(parsed)
        base = path.parent
        configured_root = (base / config.paths.data_root).resolve()
        runtime = (base / config.paths.runtime_root).resolve()
        if (
            configured_root == runtime
            or configured_root in runtime.parents
            or runtime in configured_root.parents
        ):
            raise ProcessingError("ingest_config_roots_overlap")
        if args.root is not None and args.root.resolve() != configured_root:
            raise ProcessingError("ingest_config_root_mismatch")
        root = configured_root
        model, digest = (
            model or config.model.tag or None,
            digest or config.model.expected_digest or None,
        )
        gateway, token = (
            gateway or config.gateway.endpoint,
            token or runtime / "gateway-background.token",
        )
        if encoder is None and config.search.embedding_directory:
            encoder = (base / config.search.embedding_directory).resolve()
        binding = runtime / (config.active_profile + "-character.json")
        if character is None and binding.exists():
            with binding.open("rb") as stream:
                payload = stream.read(4097)
            if len(payload) > 4096:
                raise ProcessingError("ingest_binding_invalid")
            character = _Binding.model_validate_json(payload).character_id
    gateway = gateway or "http://127.0.0.1:11435"
    origin = urlsplit(gateway)
    if (
        origin.scheme != "http"
        or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
        or origin.port is None
        or origin.port <= 0
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or origin.path not in {"", "/"}
    ):
        raise ProcessingError("ingest_gateway_origin_invalid")
    return IngestionSettings(
        root=(root or Path(".local/data")).resolve(),
        character=character,
        model=model,
        digest=digest.removeprefix("sha256:") if digest else None,
        gateway=gateway,
        token_file=token,
        encoder=encoder,
    )
