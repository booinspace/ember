"""Opt-in synthetic measurements through the sole gateway; no private prompts or outputs."""

import argparse
import asyncio
import json
import math
import platform
import subprocess
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Protocol
from uuid import uuid4

import httpx
from pydantic import Field

from ..runtime.config import DeploymentConfig, RuntimeModel, load_config
from .client import HttpGatewayClient
from .contracts import (
    GatewayError,
    GatewayHealth,
    GatewayRequest,
    GenerationUsage,
    ModelClient,
    PromptMessage,
)


class BenchmarkClient(ModelClient, Protocol):
    """A gateway client with content-free admission diagnostics, independent of its runner."""

    async def health(self) -> GatewayHealth: ...


class Sample(RuntimeModel):
    """Content-free client timing; absent provider/audio measurements remain absent."""

    outcome: str
    elapsed_seconds: float = Field(ge=0)
    first_visible_seconds: float | None = None
    usage: GenerationUsage | None = None
    cancellation_to_release_seconds: float | None = None
    next_p0_first_visible_seconds: float | None = None
    first_pcm_seconds: float | None = None
    interruption_to_sink_clear_seconds: float | None = None


def summarize(values: list[float]) -> dict[str, float | int] | None:
    """Use nearest-rank percentiles and retain sample counts for small-run interpretation."""
    if not values:
        return None
    ordered = sorted(values)
    return {
        "samples": len(values),
        "p50": median(values),
        "p95": ordered[math.ceil(len(values) * 0.95) - 1],
        "p99_exploratory": ordered[math.ceil(len(values) * 0.99) - 1],
    }


def synthetic_request(config: DeploymentConfig, words: int, output_tokens: int) -> GatewayRequest:
    """Make a bounded fixed public fixture, never accepting conversation input."""
    return GatewayRequest(
        version="0.3.0",
        request_id=uuid4(),
        context_id=uuid4(),
        model=config.model_tag,
        max_output_tokens=output_tokens,
        deadline_at=(datetime.now(UTC) + timedelta(seconds=175)).isoformat().replace("+00:00", "Z"),
        messages=(
            PromptMessage(role="system", content="Answer briefly. Do not use tools or reasoning."),
            PromptMessage(
                role="user",
                content="Synthetic benchmark data: "
                + "blue river stone tree. " * words
                + " Explain how rain reaches a river in several short sentences.",
            ),
        ),
    )


async def wait_released(client: BenchmarkClient, timeout: float = 180) -> None:
    """Require ready health and an empty active slot; uncertainty is never cleared here."""
    async with asyncio.timeout(timeout):
        while True:
            health = await client.health()
            if health.status == "quiescence_required" or health.journal_status != "available":
                raise GatewayError("benchmark_release_unknown")
            if health.status == "ready" and health.active_request_id is None:
                return
            await asyncio.sleep(0.02)


async def measure(
    client: BenchmarkClient,
    request: GatewayRequest,
    *,
    cancel: bool = False,
    voice: DeploymentConfig | None = None,
) -> Sample:
    """Measure visible streaming or consumer detachment followed by observed gateway drain."""
    first: float | None = None
    usage: GenerationUsage | None = None
    terminal = False
    started = perf_counter()
    cancelled_at: float | None = None
    sink_first: float | None = None
    sink_clear: float | None = None

    async def text() -> AsyncGenerator[str, None]:
        nonlocal first, usage, terminal, cancelled_at
        async with aclosing(client.stream(request)) as events:
            async for event in events:
                if event.kind == "text" and event.text:
                    if first is None:
                        first = perf_counter() - started
                    yield event.text
                    if cancel and voice is None:
                        cancelled_at = perf_counter()
                        return
                elif event.kind == "end":
                    terminal = True
                    usage = event.usage

    try:
        if voice is None:
            async with aclosing(text()) as parts:
                async for _ in parts:
                    pass
        else:
            from .benchmark_voice import play

            sink_first, cancelled_at, sink_clear = await play(
                text(), voice, started=started, interrupt=cancel
            )
        if cancel and cancelled_at is not None:
            await wait_released(client)
            released = perf_counter() - cancelled_at
            probe = request.model_copy(update={"request_id": uuid4(), "max_output_tokens": 16})
            next_turn = await measure(client, probe)
            return Sample(
                outcome="consumer_detached_and_drained",
                elapsed_seconds=perf_counter() - started,
                first_visible_seconds=first,
                cancellation_to_release_seconds=released,
                next_p0_first_visible_seconds=next_turn.first_visible_seconds,
                first_pcm_seconds=sink_first,
                interruption_to_sink_clear_seconds=sink_clear,
            )
        return Sample(
            outcome=(
                "voice_no_pcm"
                if voice is not None and sink_first is None
                else "complete"
                if terminal
                else "incomplete_stream"
            ),
            elapsed_seconds=perf_counter() - started,
            first_visible_seconds=first,
            usage=usage,
            first_pcm_seconds=sink_first,
        )
    except (GatewayError, TimeoutError) as error:
        return Sample(
            outcome=error.code if isinstance(error, GatewayError) else "timeout",
            elapsed_seconds=perf_counter() - started,
            first_visible_seconds=first,
        )


def gpu_snapshot() -> list[dict[str, float]] | None:
    """Read total dedicated GPU memory; polling can miss peaks and WDDM shared spill."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        return [
            {"used_mib": float(parts[0]), "free_mib": float(parts[1])}
            for line in result.stdout.splitlines()
            if len(parts := line.split(",")) == 2
        ]
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def hardware() -> dict[str, str | None]:
    """Record installed runtime/hardware identity without model content or environment secrets."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        gpu = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        gpu = None
    return {"platform": platform.platform(), "python": platform.python_version(), "gpu": gpu}


async def run(
    config: DeploymentConfig,
    output: Path,
    *,
    samples: int,
    words: int,
    output_tokens: int,
    allow_reserved: bool,
    voice: bool,
    queued_workload: str | None = None,
) -> None:
    """Reserve one benchmark lane and write raw sanitized samples, including failed trials."""
    token = (config.runtime_root / "gateway.token").read_text(encoding="utf-8").strip()
    client = HttpGatewayClient(config.services.gateway.endpoint, token)
    reservation = uuid4()
    records: list[Sample] = []
    memory: list[list[dict[str, float]]] = []
    stopped = asyncio.Event()

    async def gpu() -> None:
        while not stopped.is_set():
            value = await asyncio.to_thread(gpu_snapshot)
            if value is not None:
                memory.append(value)
            try:
                async with asyncio.timeout(0.25):
                    await stopped.wait()
            except TimeoutError:
                pass

    health = await client.health()
    if health.status != "ready" or health.active_request_id is not None:
        await client.aclose()
        raise GatewayError("benchmark_requires_idle_gateway")
    if health.realtime_sessions and not allow_reserved:
        await client.aclose()
        raise GatewayError("benchmark_requires_exclusive_session")
    manifest = await asyncio.to_thread(hardware)
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=3) as metadata:
            response = await metadata.get(config.services.ollama.endpoint + "/api/version")
            response.raise_for_status()
            manifest["ollama_version_response"] = response.text[:256]
    except httpx.HTTPError:
        manifest["ollama_version_response"] = None
    sampler = asyncio.create_task(gpu(), name="benchmark-gpu-sampler")
    warmup: Sample | None = None
    background: HttpGatewayClient | None = None
    queued: asyncio.Task[None] | None = None
    queued_health: GatewayHealth | None = None
    try:
        await client.session(reservation, connected=True)
        if queued_workload is not None:
            credential = (
                "gateway-agent.token" if queued_workload == "P2" else "gateway-background.token"
            )
            background = HttpGatewayClient(
                config.services.gateway.endpoint,
                (config.runtime_root / credential).read_text(encoding="utf-8").strip(),
            )
            step = GatewayRequest.model_validate_json(
                synthetic_request(config, 0, 16)
                .model_copy(update={"workload": queued_workload, "output": "json"})
                .model_dump_json()
            )

            async def consume() -> None:
                assert background is not None
                async with aclosing(background.stream(step)) as events:
                    async for _ in events:
                        pass

            queued = asyncio.create_task(consume(), name="benchmark-queued-background")
            async with asyncio.timeout(5):
                while True:
                    if queued.done():
                        await queued
                        raise GatewayError("benchmark_background_did_not_queue")
                    queued_health = await client.health()
                    counts = {
                        "P2": queued_health.queued_p2,
                        "P3": queued_health.queued_p3,
                        "P4": queued_health.queued_p4,
                    }
                    if counts[queued_workload]:
                        break
                    await asyncio.sleep(0.02)
        warmup = await measure(
            client, synthetic_request(config, words, output_tokens), voice=config if voice else None
        )
        if warmup.outcome != "complete":
            raise GatewayError("benchmark_warmup_failed")
        for _ in range(samples):
            records.append(
                await measure(
                    client,
                    synthetic_request(config, words, output_tokens),
                    voice=config if voice else None,
                )
            )
            if queued is not None and queued.done():
                raise GatewayError("benchmark_background_queue_expired")
        records.append(
            await measure(
                client,
                synthetic_request(config, words, output_tokens),
                cancel=True,
                voice=config if voice else None,
            )
        )
    finally:
        stopped.set()
        await sampler
        try:
            if queued is not None:
                queued.cancel()
                await asyncio.gather(queued, return_exceptions=True)
                # Observe server-side detach before releasing our reservation.
                async with asyncio.timeout(5):
                    while True:
                        state = await client.health()
                        if (state.queued_p2, state.queued_p3, state.queued_p4) == (
                            health.queued_p2,
                            health.queued_p3,
                            health.queued_p4,
                        ):
                            break
                        await asyncio.sleep(0.02)
            if background is not None:
                await background.aclose()
            await client.session(reservation, connected=False)
        finally:
            await client.aclose()
            report = {
                "version": 1,
                "environment": manifest,
                "model": config.model_tag,
                "digest": config.model_digest,
                "context_tokens": config.context_tokens,
                "output_tokens": output_tokens,
                "fixture_repetitions": words,
                "queued_workload": queued_workload,
                "queued_health": queued_health.model_dump(mode="json") if queued_health else None,
                "health": health.model_dump(mode="json"),
                "voice_measurement": "paced_sdk_pcm_sink_no_network_or_device" if voice else None,
                "physical_audio_stop_seconds": None,
                "gpu_sampling_interval_seconds": 0.25,
                "gpu_samples": memory,
                "warmup_excluded": warmup.model_dump(mode="json") if warmup else None,
                "samples": [item.model_dump(mode="json") for item in records],
                "warm_first_visible_seconds": summarize(
                    [
                        item.first_visible_seconds
                        for item in records
                        if item.outcome == "complete" and item.first_visible_seconds is not None
                    ]
                ),
                "warm_turn_seconds": summarize(
                    [item.elapsed_seconds for item in records if item.outcome == "complete"]
                ),
                "decode_tokens_per_second": summarize(
                    [
                        item.usage.tokens_per_second
                        for item in records
                        if item.outcome == "complete"
                        and item.usage is not None
                        and item.usage.tokens_per_second is not None
                    ]
                ),
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents clobbering previous results or operator files.
            with output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, indent=2, allow_nan=False)
    if any(item.outcome not in {"complete", "consumer_detached_and_drained"} for item in records):
        raise GatewayError("benchmark_contains_failed_trials")


def main() -> None:
    """Run only against an already installed and explicitly selected gateway configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", default=30, type=int)
    parser.add_argument("--fixture-repetitions", default=32, type=int)
    parser.add_argument("--output-tokens", default=128, type=int)
    parser.add_argument("--allow-reserved", action="store_true")
    parser.add_argument("--voice", action="store_true")
    parser.add_argument("--queued-workload", choices=("P2", "P3", "P4"))
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000 or not 0 <= args.fixture_repetitions <= 2048:
        parser.error("Use 1–1000 samples and 0–2048 fixture repetitions.")
    if not 16 <= args.output_tokens <= 4096:
        parser.error("Use 16–4096 output tokens.")
    if args.output.exists():
        parser.error("Output already exists; choose a new report path.")
    config = load_config(args.config, require_model_pin=True)
    asyncio.run(
        run(
            config,
            args.output,
            samples=args.samples,
            words=args.fixture_repetitions,
            output_tokens=args.output_tokens,
            allow_reserved=args.allow_reserved,
            voice=args.voice,
            queued_workload=args.queued_workload,
        )
    )


if __name__ == "__main__":
    main()
