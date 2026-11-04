"""Create an exclusive private setup receipt for the qualified installed Qwen GGUF runner."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
from pathlib import Path

from vllm_profile import QUALIFIED_ENVIRONMENT, SetupProfile, installed_software

MODEL_SHA256 = "a4adabb80876639edf19db8f9f8197492fa5c26e82ee1fdb6f7dd2136d7c70c1"
METADATA_SHA256 = {
    "config.json": "8df0808889e4a0988ba32200eae7b1faf692b810a1a56e7cb49f826c80ef9af0",
    "tokenizer_config.json": "b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27",
    "tokenizer.json": "0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3",
    "chat_template.jinja": "c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041",
    "generation_config.json": "e70c136c1b78ddc1fb0905bac8e733a4dc448d4f852a5dd75143fffc70be550e",
}
_VERSIONS = {
    "vllm": "0.31.0",
    "torch": "2.13.0+cu130",
    "gguf": "0.19.0",
    "vllm-gguf-plugin": "0.0.5+e454affe",
}


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _software() -> dict[str, str]:
    software = installed_software()
    if not software["python"].startswith("3.12."):
        raise ValueError("The qualified vLLM environment requires Python 3.12.")
    for name, expected in _VERSIONS.items():
        if importlib.metadata.version(name) != expected:
            raise ValueError("Install the qualified vLLM, Torch, GGUF and plugin versions first.")
    return software


def _plugin_files() -> list[Path]:
    distribution = importlib.metadata.distribution("vllm-gguf-plugin")
    files = distribution.files
    if files is None:
        raise ValueError("Install the qualified GGUF plugin wheel first.")
    assets: list[Path] = []
    for entry in files:
        if entry.parts[0] != "vllm_gguf_plugin" or entry.suffix not in {".py", ".so"}:
            continue
        if ".." in entry.parts:
            raise ValueError("Installed GGUF plugin metadata contains an unsafe asset path.")
        assets.append(Path(str(distribution.locate_file(entry))).absolute())
    if not any(path.suffix == ".py" for path in assets) or not any(
        path.suffix == ".so" for path in assets
    ):
        raise ValueError("The qualified GGUF plugin requires Python and compiled CUDA assets.")
    return sorted(set(assets))


def prepare_profile(
    model: Path,
    metadata: Path,
    output: Path,
    *,
    cuda_home: Path,
    cc: Path,
    cxx: Path,
    context_tokens: int = 131072,
) -> str:
    """Hash existing local assets and write one receipt without copying weights or credentials."""
    if output.exists() or output.is_symlink():
        raise FileExistsError("The setup receipt already exists; choose a new output path.")
    if not 2048 <= context_tokens <= 131072:
        raise ValueError("The qualified context must be between 2048 and 131072 tokens.")
    model = model.absolute()  # Preserve the operator's content-addressed symlink name.
    metadata = metadata.resolve(strict=True)
    cuda_home = cuda_home.resolve(strict=True)
    cc, cxx = cc.absolute(), cxx.absolute()
    if not metadata.is_dir() or not cuda_home.is_dir():
        raise ValueError("Existing metadata and CUDA toolkit directories are required.")
    if {entry.name for entry in metadata.iterdir()} != set(METADATA_SHA256):
        raise ValueError(
            "The qualified metadata directory must contain exactly five approved files."
        )
    if model.name != "sha256-" + MODEL_SHA256 + ".gguf":
        raise ValueError("Use the existing qualified content-addressed GGUF path.")
    software = _software()
    files = {str(model): _sha256(model)}
    if files[str(model)] != MODEL_SHA256:
        raise ValueError("The GGUF bytes do not match the qualified model artifact.")
    for name, expected in METADATA_SHA256.items():
        path = metadata / name
        actual = _sha256(path)
        if actual != expected:
            raise ValueError("Metadata bytes do not match the qualified companion revision.")
        files[str(path)] = actual
    repo = Path(__file__).resolve().parents[1]
    assets = [
        *_plugin_files(),
        *(
            repo / "scripts" / name
            for name in (
                "run_vllm.py",
                "vllm_profile.py",
                "vllm_gateway_guard.py",
            )
        ),
        *(
            repo / "ember-agent/src/ember_agent/inference" / name
            for name in (
                "vllm.py",
                "upstream.py",
                "gateway.py",
                "contracts.py",
            )
        ),
        cc,
        cxx,
    ]
    for path in assets:
        files[str(path)] = _sha256(path)
    argv = [
        "serve",
        str(model),
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--served-model-name",
        "ember-vllm-qwen-iq3s",
        "--tokenizer",
        str(metadata),
        "--hf-config-path",
        str(metadata),
        "--generation-config",
        str(metadata),
        "--chat-template",
        str(metadata / "chat_template.jinja"),
        "--model-class-overrides",
        json.dumps(
            {
                "Qwen3_5ForConditionalGeneration": (
                    "vllm.model_executor.models.qwen3_5:Qwen3_5ForCausalLM"
                ),
            }
        ),
        "--quantization",
        "gguf",
        "--load-format",
        "gguf",
        "--dtype",
        "bfloat16",
        "--max-model-len",
        str(context_tokens),
        "--max-num-seqs",
        "1",
        "--max-num-batched-tokens",
        "512",
        "--gpu-memory-utilization",
        "0.90",
        "--enable-prefix-caching",
        "--enable-chunked-prefill",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "qwen3_coder",
        "--reasoning-parser",
        "qwen3",
        "--enable-per-request-metrics",
        "--disable-uvicorn-access-log",
        "--disable-fastapi-docs",
        "--middleware",
        "vllm_gateway_guard.GatewayOnlyMiddleware",
    ]
    profile = SetupProfile(
        argv=argv,
        files=files,
        software=software,
        environment={
            **QUALIFIED_ENVIRONMENT,
            "CUDA_HOME": str(cuda_home),
            "CC": str(cc),
            "CXX": str(cxx),
        },
    )
    content = (profile.model_dump_json(indent=2) + "\n").encode()
    if len(content) > 128 * 1024:
        raise ValueError("The setup receipt exceeds the launcher's size bound.")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(content)
    canonical = json.dumps(
        profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def main() -> None:
    """Create a receipt in the already installed GPU environment; perform no setup or launch."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context-tokens", type=int, default=131072)
    parser.add_argument("--cuda-home", type=Path, required=True)
    parser.add_argument("--cc", type=Path, default=Path(shutil.which("gcc-13") or "gcc-13"))
    parser.add_argument("--cxx", type=Path, default=Path(shutil.which("g++-13") or "g++-13"))
    args = parser.parse_args()
    print(
        prepare_profile(
            args.model,
            args.metadata,
            args.output,
            cuda_home=args.cuda_home,
            cc=args.cc,
            cxx=args.cxx,
            context_tokens=args.context_tokens,
        )
    )


if __name__ == "__main__":
    main()
