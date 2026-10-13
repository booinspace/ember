"""Synthetic paired Phase 8 contention evidence; never reads or mutates a private memory root."""

import argparse
import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import UUID, uuid4

from benchmark_phase_seven import corpus
from pydantic import Field

from ember_agent.character.temporal import CalendarResolver
from ember_agent.memory.deep_contracts import DeepRecallRequest
from ember_agent.memory.deep_recall import DeepRecallService
from ember_memory_abi.common import ContractModel
from ember_memory_abi.interfaces import ContextRequest, MemoryQuery, RetrievalContext
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.index_builder import IndexBuilder
from ember_memory_engine.read_budget import ReadBudget


class Arguments(ContractModel):
    root: Path
    encoder: Path
    output: Path
    samples: int = Field(default=1000, ge=1000, le=10000)
    records: int = Field(default=1000, ge=1000, le=100000)


class Measurement(ContractModel):
    samples: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    over_150_ms: int
    degraded: int


def summarize(values: list[float], degraded: int) -> Measurement:
    values.sort()
    count = len(values)
    return Measurement(
        samples=count,
        p50_ms=median(values),
        p95_ms=values[(count * 95 + 99) // 100 - 1],
        p99_ms=values[(count * 99 + 99) // 100 - 1],
        over_150_ms=sum(value > 150 for value in values),
        degraded=degraded,
    )


def regression(baseline: Measurement, condition: Measurement) -> dict[str, object]:
    bounds = {
        "p50_ms": min(5, baseline.p50_ms * 0.05),
        "p95_ms": min(10, baseline.p95_ms * 0.10),
        "p99_ms": min(20, baseline.p99_ms * 0.15),
    }
    deltas = {
        "p50_ms": condition.p50_ms - baseline.p50_ms,
        "p95_ms": condition.p95_ms - baseline.p95_ms,
        "p99_ms": condition.p99_ms - baseline.p99_ms,
    }
    rates = {
        "timeout_delta_percentage_points": 100
        * (condition.over_150_ms / condition.samples - baseline.over_150_ms / baseline.samples),
        "degraded_delta_percentage_points": 100
        * (condition.degraded / condition.samples - baseline.degraded / baseline.samples),
    }
    return {
        "limits_ms": bounds,
        "deltas_ms": deltas,
        **rates,
        "passed": all(deltas[key] <= bounds[key] for key in bounds)
        and all(value <= 1 for value in rates.values()),
    }


async def measure(
    operation: Callable[[str], Awaitable[RetrievalContext]], samples: int
) -> Measurement:
    queries = (
        "NAS DNS Tailscale resolver",
        "router VLAN firewall",
        "ambient music sequencer",
        "VR headset tracking",
    )
    values: list[float] = []
    degraded = 0
    for ordinal in range(samples):
        started = perf_counter()
        context = await operation(queries[ordinal % len(queries)])
        values.append((perf_counter() - started) * 1000)
        degraded += context.coverage.status != "complete"
    return summarize(values, degraded)


async def paired(engine: CanonicalEngine, identity: UUID, args: Arguments) -> dict[str, object]:
    store = engine.character(identity)
    foreground = InProcessAsyncMemoryClient(store)

    def factory(deadline: float) -> InProcessAsyncMemoryClient:
        budget = ReadBudget(deadline=deadline)
        return InProcessAsyncMemoryClient(
            store, deep_sources=DeepSources(engine, budget), read_budget=budget
        )

    async def ordinary(text: str) -> RetrievalContext:
        return await foreground.retrieve_context(
            ContextRequest(
                memory_query=MemoryQuery(text=text, limit=6),
                include_profile=True,
                memory_token_budget=768,
            )
        )

    service: DeepRecallService | None = None
    task: asyncio.Task[None] | None = None
    finished = asyncio.Event()
    deep_runs = 0
    deep_outcomes: dict[str, int] = {}
    deep_elapsed: list[float] = []

    async def investigations() -> None:
        nonlocal deep_runs
        assert service is not None
        while not finished.is_set():
            started = perf_counter()
            result = await service.investigate(
                DeepRecallRequest(
                    request_id=uuid4(),
                    character_id=identity,
                    context_id=uuid4(),
                    query="NAS DNS Tailscale resolver",
                    reference_at="2026-10-09T12:00:00.000000Z",
                )
            )
            deep_elapsed.append((perf_counter() - started) * 1000)
            deep_runs += 1
            deep_outcomes[result.outcome] = deep_outcomes.get(result.outcome, 0) + 1
            await asyncio.sleep(0.05 if result.outcome == "deferred" else 0)

    try:
        cold = await measure(ordinary, 4)
        for _ in range(3):
            for query in (
                "NAS DNS Tailscale resolver",
                "router VLAN firewall",
                "ambient music sequencer",
                "VR headset tracking",
            ):
                await ordinary(query)
        baseline = await measure(ordinary, args.samples)
        service = DeepRecallService(identity, factory, validation=foreground)
        idle = await measure(ordinary, args.samples)
        task = asyncio.create_task(investigations(), name="ember-synthetic-deep-benchmark")
        active = await measure(ordinary, args.samples)
        finished.set()
        await task
        parser = CalendarResolver()
        timings: list[float] = []
        for _ in range(args.samples):
            started = perf_counter()
            parser.resolve("NAS problems from summer 2026", datetime(2026, 10, 9, tzinfo=UTC))
            timings.append((perf_counter() - started) * 1000)
        return {
            "fixture_records": args.records,
            "encoder": "CPU BGE small, one thread",
            "generation": "experimental 0.6.0",
            "cold_queries": cold.model_dump(),
            "ordinary_deep_disabled": baseline.model_dump(),
            "ordinary_deep_enabled_idle": idle.model_dump(),
            "ordinary_deep_active": active.model_dump(),
            "idle_regression": regression(baseline, idle),
            "active_regression": regression(baseline, active),
            "calendar_parsing": summarize(timings, 0).model_dump(),
            "deep_runs": deep_runs,
            "deep_outcomes": deep_outcomes,
            "foreground_policy": "deep defers to admitted foreground native reads",
            "deep_elapsed": summarize(deep_elapsed, 0).model_dump() if deep_elapsed else None,
            "deep_encoder_policy": "completed query cache only; no uncached encodes",
            "not_measured": [
                "simultaneous index refresh",
                "large archive",
                "temporal nonempty SQL and historical validation",
                "actual peak working memory",
                "curated support precision and recall",
                "Qwen interpretation/final-answer TTFT",
                "actual voice delivery",
            ],
        }
    finally:
        finished.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if service is not None:
            await service.aclose()
        await foreground.aclose()


def main() -> int:
    """Use a new synthetic root and installed CPU assets; publish aggregate-only output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--encoder", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=1000)
    parser.add_argument("--records", type=int, default=1000)
    args = Arguments.model_validate(vars(parser.parse_args()))
    if args.root.exists() or args.output.exists():
        raise ValueError("Benchmark requires a new synthetic root and report.")
    encoder = BgeEncoder(args.encoder, threads=1)
    with CanonicalEngine(args.root, encoder=encoder) as engine:
        identity = corpus(engine, args.records)
        IndexBuilder(engine, encoder).rebuild(identity)
        report = asyncio.run(paired(engine, identity, args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
