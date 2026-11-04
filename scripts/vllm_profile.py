"""Verified setup receipt shared by the explicit vLLM launcher and its route guard."""

import hashlib
import importlib.metadata
import json
import platform
import sysconfig
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

QUALIFIED_ENVIRONMENT = {
    "VLLM_USE_RUST_FRONTEND": "0",
    "VLLM_PLUGINS": "gguf",
    "VLLM_GGUF_USE_CUDA": "1",
    "VLLM_SERVER_DEV_MODE": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "CUDA_VISIBLE_DEVICES": "0",
    "OMP_NUM_THREADS": "1",
}


class SetupProfile(BaseModel):
    """Static service inputs only; credentials and runtime observations never enter identity."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: Literal["ember-vllm-setup-v1"] = "ember-vllm-setup-v1"
    argv: Annotated[list[str], Field(min_length=1, max_length=128)]
    files: Annotated[dict[str, str], Field(min_length=1, max_length=256)]
    software: Annotated[dict[str, str], Field(min_length=1, max_length=512)]
    environment: Annotated[dict[str, str], Field(max_length=16)] = Field(
        default_factory=lambda: dict(QUALIFIED_ENVIRONMENT)
    )


def installed_software() -> dict[str, str]:
    """Capture installed roots; imports may add bundled vendor metadata to dynamic sys.path."""
    software = {"python": platform.python_version()}
    roots = sorted(
        {str(Path(sysconfig.get_path(name)).resolve()) for name in ("purelib", "platlib")}
    )
    for distribution in importlib.metadata.distributions(path=roots):
        name = distribution.metadata["Name"]
        if not name or name in software:
            raise ValueError("Installed software metadata is missing or duplicated.")
        software[name] = distribution.version
    return software


def load_profile(path: Path) -> tuple[SetupProfile, str]:
    """Verify actual immutable input bytes before attesting their static setup digest."""
    if path.stat().st_size > 128 * 1024:
        raise ValueError("vLLM setup receipt exceeds its bound.")
    profile = SetupProfile.model_validate_json(path.read_bytes())
    if any(profile.environment.get(key) != value for key, value in QUALIFIED_ENVIRONMENT.items()):
        raise ValueError(
            "Use the qualified Python frontend, plugin, kernels and offline environment."
        )
    if set(profile.environment) - set(QUALIFIED_ENVIRONMENT) - {"CUDA_HOME", "CC", "CXX"}:
        raise ValueError("Unsupported vLLM execution environment.")
    if installed_software() != profile.software:
        raise ValueError("vLLM setup software changed; explicit requalification is required.")
    for index, argument in enumerate(profile.argv):
        if argument not in {"--tokenizer", "--hf-config-path", "--generation-config"}:
            continue
        if index + 1 == len(profile.argv):
            raise ValueError("vLLM metadata directory argument is missing.")
        directory = Path(profile.argv[index + 1])
        expected = {Path(name).name for name in profile.files if Path(name).parent == directory}
        if (
            not directory.is_absolute()
            or not directory.is_dir()
            or not expected
            or {entry.name for entry in directory.iterdir()} != expected
        ):
            raise ValueError(
                "vLLM metadata inventory changed; explicit requalification is required."
            )
    for name, expected in profile.files.items():
        source = Path(name)
        if not source.is_absolute() or len(expected) != 64:
            raise ValueError("vLLM setup file identity is invalid.")
        with source.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("vLLM setup input changed; explicit requalification is required.")
    canonical = json.dumps(
        profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return profile, hashlib.sha256(canonical).hexdigest()
