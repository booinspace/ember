"""Run matched synthetic gateway cases; each JSON file retains raw timings and failures."""

import argparse
import asyncio
from pathlib import Path

from ember_agent.inference.benchmark import run
from ember_agent.runtime.config import load_config

CASES = (
    ("p0-short", 8, None, False),
    ("p0-medium", 256, None, False),
    ("p0-long", 1024, None, False),
    ("p2-queued", 8, "P2", False),
    ("p3-queued", 8, "P3", False),
    ("p4-queued", 8, "P4", False),
    ("voice-short", 8, None, True),
    ("voice-p2-queued", 8, "P2", True),
    ("voice-p3-queued", 8, "P3", True),
    ("voice-p4-queued", 8, "P4", True),
)


def main() -> None:
    """Use one preinstalled gateway; never launch, unload or directly call a model runner."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--voice-samples", type=int, default=30)
    parser.add_argument("--allow-reserved", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000 or not 1 <= args.voice_samples <= 1000:
        parser.error("Use 1–1000 samples per case.")
    if args.output_directory.exists():
        parser.error("Use a new output directory; previous reports are never overwritten.")
    config = load_config(args.config, require_model_pin=True)
    args.output_directory.mkdir(parents=True)

    async def execute() -> None:
        for label, words, workload, voice in CASES:
            print("Starting " + label, flush=True)
            await run(
                config,
                args.output_directory / (label + ".json"),
                samples=args.voice_samples if voice else args.samples,
                words=words,
                output_tokens=64,
                allow_reserved=args.allow_reserved,
                voice=voice,
                queued_workload=workload,
            )
            print("Completed " + label, flush=True)

    asyncio.run(execute())


if __name__ == "__main__":
    main()
