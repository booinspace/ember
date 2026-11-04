"""Print a verified runner identity using metadata only; never generate or change setup."""

import argparse
import asyncio
from pathlib import Path

from ember_agent.inference.contracts import GatewayError
from ember_agent.inference.vllm import VLLMUpstream
from ember_agent.runtime.config import load_config


async def inspect(path: Path) -> str:
    """Reuse the adapter's bounded metadata transport and its complete pin validation."""
    config = load_config(path, require_model_pin=True)
    settings = config.inference.vllm
    if config.inference.backend != "vllm" or settings is None:
        raise GatewayError("upstream_configuration_mismatch")
    upstream = VLLMUpstream(config)
    try:
        return await upstream.inspect_identity()
    finally:
        await upstream.aclose()


def main() -> None:
    """An explicit operator command; the printed digest does not edit any deployment file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(asyncio.run(inspect(args.config)))


if __name__ == "__main__":
    main()
