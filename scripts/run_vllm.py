"""Explicit external runner launch; never invoked by Ember startup or inference callers."""

import argparse
import os
import sys
from pathlib import Path

from vllm_profile import load_profile

_ALLOWED_FLAGS = {
    "--host",
    "--port",
    "--middleware",
    "--served-model-name",
    "--tokenizer",
    "--hf-config-path",
    "--load-format",
    "--quantization",
    "--dtype",
    "--seed",
    "--model-class-overrides",
    "--max-model-len",
    "--max-num-seqs",
    "--max-num-batched-tokens",
    "--gpu-memory-utilization",
    "--kv-cache-memory-bytes",
    "--enable-prefix-caching",
    "--enable-chunked-prefill",
    "--enforce-eager",
    "--enable-auto-tool-choice",
    "--tool-call-parser",
    "--reasoning-parser",
    "--generation-config",
    "--chat-template",
    "--enable-per-request-metrics",
    "--disable-uvicorn-access-log",
    "--disable-fastapi-docs",
    "--attention-config",
}


def main() -> None:
    """Verify setup inputs then replace this process with the single pinned vLLM server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    profile, digest = load_profile(args.profile)
    argv = profile.argv
    if (
        argv[0] != "serve"
        or len(argv) < 2
        or argv[1] not in profile.files
        or Path(argv[1]).name != "sha256-" + profile.files.get(argv[1], "") + ".gguf"
        or any(value.startswith("-") and value not in _ALLOWED_FLAGS for value in argv)
        or argv.count("--middleware") != 1
        or argv[argv.index("--middleware") + 1 : argv.index("--middleware") + 2]
        != ["vllm_gateway_guard.GatewayOnlyMiddleware"]
        or any(
            value.startswith(("--api-key", "--host=", "--middleware=", "--config"))
            for value in argv
        )
        or argv.count("--host") != 1
        or argv[argv.index("--host") + 1 : argv.index("--host") + 2] != ["127.0.0.1"]
    ):
        raise ValueError("Use the qualified loopback server arguments and mandatory route guard.")
    token = os.environ.get("EMBER_VLLM_TOKEN", "")
    if not 32 <= len(token) <= 4096 or not token.isascii() or not token.isalnum():
        raise ValueError("A dedicated random alphanumeric vLLM credential is required.")
    environment = {
        **{
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("VLLM_", "HF_", "TRANSFORMERS_", "TOKENIZERS_", "CUDA_"))
        },
        **profile.environment,
        "EMBER_VLLM_PROFILE": str(args.profile.resolve()),
        "VLLM_API_KEY": token,
        "VLLM_SERVER_DEV_MODE": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parent),
    }
    executable = Path(sys.executable).with_name("vllm")
    print("Verified static vLLM profile: " + digest, flush=True)
    os.execve(executable, [str(executable), *argv], environment)


if __name__ == "__main__":
    main()
