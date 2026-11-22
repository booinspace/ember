"""Measure voice waiting on synthetic OpenCode-shaped requests through a running gateway."""

import argparse
import asyncio
import json
from contextlib import aclosing
from pathlib import Path
from time import perf_counter
from typing import TextIO
from uuid import uuid4

from ember_agent.inference.benchmark import hardware, measure, synthetic_request
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import GatewayError, GenerationUsage, PromptMessage
from ember_agent.runtime.config import DeploymentConfig, load_config


async def run(
    config: DeploymentConfig, output: Path, samples: int, *, voice: bool, observe_active: bool
) -> None:
    """Collect matched warm trials without starting, cancelling, or restarting a runner."""
    if config.inference.backend != "strata" or not config.inference.coding_api:
        raise ValueError("Use a running Strata deployment with coding enabled.")
    credentials = [
        (config.runtime_root / name).read_text(encoding="utf-8").strip()
        for name in ("gateway.token", "gateway-agent.token")
    ]
    with output.open("x", encoding="utf-8", newline="\n") as report:
        await trials(
            config, report, credentials, samples, voice=voice, observe_active=observe_active
        )


async def trials(
    config: DeploymentConfig,
    report: TextIO,
    credentials: list[str],
    samples: int,
    *,
    voice: bool,
    observe_active: bool,
) -> None:
    """Use the existing P5 gateway ingress and emit one content-free row per trial."""
    endpoint = config.services.gateway.endpoint
    async with (
        aclosing(HttpGatewayClient(endpoint, credentials[0])) as foreground,
        aclosing(HttpGatewayClient(endpoint, credentials[1])) as coding,
    ):
        # The benchmark uses the same installed PCM sink as the ordinary gateway suite.
        voice_config = config if voice else None
        profile = await foreground.health()
        if (
            profile.model != config.model_tag
            or profile.digest.removeprefix("sha256:") != config.model_digest.removeprefix("sha256:")
            or profile.context_tokens != config.context_tokens
        ):
            raise GatewayError("benchmark_live_profile_mismatch")

        def write(row: object) -> None:
            print(json.dumps(row), file=report, flush=True)

        async def idle() -> None:
            try:
                async with asyncio.timeout(20):
                    while True:
                        health = await foreground.health()
                        if health.status not in {"ready", "busy"}:
                            raise GatewayError("benchmark_gateway_unavailable")
                        if (
                            health.status == "ready"
                            and health.active_request_id is None
                            and health.queued_p1 == 0
                        ):
                            return
                        await asyncio.sleep(0.2)
            except TimeoutError as error:
                raise GatewayError("benchmark_requires_idle_gateway") from error

        async def contend(case: str) -> None:
            await idle()
            first_text = asyncio.Event()
            coding_end = 0.0
            coding_usage: GenerationUsage | None = None
            words = "blue river stone tree. " * 2200 if case == "before_first_text" else ""
            request = synthetic_request(config, 0, 512).model_copy(
                update={
                    "workload": "P5",
                    "messages": (
                        PromptMessage(
                            role="user",
                            content=f"Synthetic coding benchmark {uuid4().hex}. {words}"
                            "Emit one line per integer from one through five hundred. "
                            "Each line should contain the integer followed by its square.",
                        ),
                    ),
                }
            )

            async def consume() -> None:
                nonlocal coding_end, coding_usage
                async with aclosing(coding.stream(request)) as events:
                    async for event in events:
                        if event.kind == "text" and event.text:
                            first_text.set()
                        if event.kind == "end":
                            coding_end = perf_counter()
                            coding_usage = event.usage

            task = asyncio.create_task(consume())
            try:
                async with asyncio.timeout(20):
                    while True:
                        if task.done():
                            await task
                            raise GatewayError("benchmark_coding_ended_before_probe")
                        health = await foreground.health()
                        if health.active_request_id not in {None, request.request_id}:
                            task.cancel()
                            raise GatewayError("benchmark_other_request_active")
                        if health.active_request_id == request.request_id and (
                            case == "before_first_text" or first_text.is_set()
                        ):
                            break
                        await asyncio.sleep(0.025)
                had_text = first_text.is_set()
                sent = perf_counter()
                result = await measure(
                    foreground, synthetic_request(config, 0, 24), voice=voice_config
                )
                await task
                write(
                    {
                        "case": case,
                        "coding_had_text_at_probe_start": had_text,
                        "coding_remaining_seconds": max(0.0, coding_end - sent),
                        "coding_usage": coding_usage.model_dump() if coding_usage else None,
                        "voice": result.model_dump(),
                    }
                )
            except (GatewayError, TimeoutError) as error:
                task.cancel()
                write({"case": case, "error": getattr(error, "code", "timeout")})
                raise
            finally:
                # Closing the subscriber leaves the gateway to finish the owned work.
                await asyncio.gather(task, return_exceptions=True)

        write(
            {
                "hardware": await asyncio.to_thread(hardware),
                "model": config.model_tag,
                "runner_digest": profile.digest,
                "context_tokens": config.context_tokens,
                "measurement": "paced_pcm" if voice else "gateway_text",
            }
        )
        if observe_active:
            for _ in range(samples):
                try:
                    async with asyncio.timeout(20):
                        while True:
                            health = await foreground.health()
                            if health.status == "busy" and health.active_request_id is not None:
                                break
                            if health.status not in {"busy", "ready"}:
                                raise GatewayError("benchmark_gateway_unavailable")
                            await asyncio.sleep(0.2)
                except TimeoutError:
                    write({"case": "observed_busy", "error": "no_active_request"})
                    return
                # P0 waits only behind active P5. Other active workloads reject it as busy.
                result = await measure(
                    foreground, synthetic_request(config, 0, 24), voice=voice_config
                )
                write({"case": "observed_busy", "voice": result.model_dump()})
                if result.outcome == "busy":
                    break
            return
        await idle()
        reservation = uuid4()
        try:
            await foreground.session(reservation, connected=True)
            warmup = await measure(foreground, synthetic_request(config, 0, 24), voice=voice_config)
            write({"case": "warmup", "voice": warmup.model_dump()})
            if warmup.outcome != "complete":
                raise GatewayError("benchmark_warmup_failed")
            for _ in range(samples):
                await idle()
                baseline = await measure(
                    foreground, synthetic_request(config, 0, 24), voice=voice_config
                )
                write({"case": "idle", "voice": baseline.model_dump()})
                await contend("before_first_text")
                await contend("after_first_text")
        finally:
            await foreground.session(reservation, connected=False)


def main() -> None:
    """Accept a local configuration and an unused aggregate report path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=5, choices=range(1, 21))
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--observe-active", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config, require_model_pin=True)
    asyncio.run(
        run(
            config,
            args.output,
            args.samples,
            voice=not args.text_only,
            observe_active=args.observe_active,
        )
    )


if __name__ == "__main__":
    main()
