"""Aggregate-only deep recall measurements on an explicitly supplied private snapshot.

Take SQLite backups into an ignored isolated root first. This script never submits
model generation, approves claims, seeds memories, or writes source text to reports.
It opens the snapshot through normal engine ownership; do not use a running live root.
"""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from typing import Annotated
from uuid import UUID, uuid4

from benchmark_phase_eight import regression, summarize
from pydantic import Field, TypeAdapter, ValidationError

from ember_agent.memory.deep_contracts import DeepRecallRequest
from ember_agent.memory.deep_recall import DeepRecallService
from ember_memory_abi.common import ContractModel, timestamp_from_datetime
from ember_memory_abi.interfaces import ContextRequest, MemoryQuery
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.read_budget import ReadBudget


class Arguments(ContractModel):
    """Explicit local inputs; query contents remain outside the resulting report."""

    snapshot_root: Path
    character: UUID
    encoder: Path
    queries: Path
    output: Path
    samples: int = Field(default=3, ge=1, le=100)
    ordinary_samples: int = Field(default=1000, ge=0, le=10000)


_QUERY_LIST: TypeAdapter[list[str]] = TypeAdapter(
    Annotated[
        list[Annotated[str, Field(min_length=1, max_length=512)]],
        Field(min_length=1, max_length=32),
    ]
)


async def measure(
    engine: CanonicalEngine, args: Arguments, queries: list[str]
) -> dict[str, object]:
    """Measure cold/distinct investigations and warm fast recall with conservative contention."""
    store = engine.character(args.character)
    foreground = InProcessAsyncMemoryClient(store)

    def factory(deadline: float) -> InProcessAsyncMemoryClient:
        budget = ReadBudget(deadline=deadline)
        return InProcessAsyncMemoryClient(
            store, deep_sources=DeepSources(engine, budget), read_budget=budget
        )

    async def ordinary(text: str) -> tuple[float, bool]:
        start = perf_counter()
        value = await foreground.retrieve_context(
            ContextRequest(memory_query=MemoryQuery(text=text, limit=6), memory_token_budget=768)
        )
        return (perf_counter() - start) * 1000, value.coverage.status != "complete"

    service: DeepRecallService | None = None
    stop = asyncio.Event()
    task: asyncio.Task[None] | None = None
    contention: dict[str, int] = {}

    async def background() -> None:
        assert service is not None
        while not stop.is_set():
            result = await service.investigate(
                DeepRecallRequest(
                    request_id=uuid4(),
                    character_id=args.character,
                    context_id=uuid4(),
                    query=queries[0],
                    reference_at=timestamp_from_datetime(engine.clock.now()),
                )
            )
            contention[result.outcome] = contention.get(result.outcome, 0) + 1
            await asyncio.sleep(0.05)

    async def fast() -> dict[str, object]:
        times: list[float] = []
        degraded = 0
        for ordinal in range(args.ordinary_samples):
            elapsed, partial = await ordinary(queries[ordinal % min(4, len(queries))])
            times.append(elapsed)
            degraded += partial
        return summarize(times, degraded).model_dump()

    try:
        service = DeepRecallService(args.character, factory, validation=foreground)
        cases: list[dict[str, object]] = []
        for sample in range(args.samples):
            for ordinal, query in enumerate(queries):
                request = DeepRecallRequest(
                    request_id=uuid4(),
                    character_id=args.character,
                    context_id=uuid4(),
                    query=query,
                    reference_at=timestamp_from_datetime(engine.clock.now()),
                )
                result = await service.investigate(request)
                inspected = await service.inspect(request.request_id, request.context_id)
                cases.append(
                    {
                        "case": ordinal,
                        "sample": sample,
                        "outcome": result.outcome,
                        "memories": len(result.memories),
                        "observations": len(result.observations),
                        "prompt_memories": len(result.prompt_memories),
                        "reasons": result.reasons,
                        "inspection_valid": inspected is not None,
                        "counters": result.counters.model_dump(exclude={"extensions"}),
                    }
                )
        await service.aclose()
        service = None
        paired: dict[str, object] = {}
        if args.ordinary_samples:
            for query in queries[:4]:
                await ordinary(query)
            baseline = await fast()
            service = DeepRecallService(args.character, factory, validation=foreground)
            idle = await fast()
            task = asyncio.create_task(background(), name="ember-private-snapshot-deep-benchmark")
            active = await fast()
            stop.set()
            await task
            from benchmark_phase_eight import Measurement

            paired = {
                "disabled": baseline,
                "idle": idle,
                "active": active,
                "idle_regression": regression(
                    Measurement.model_validate(baseline), Measurement.model_validate(idle)
                ),
                "active_regression": regression(
                    Measurement.model_validate(baseline), Measurement.model_validate(active)
                ),
                "deep_outcomes_during_foreground": contention,
            }
        records = store.database.read(
            lambda c: int(c.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        )
        return {
            "memory_records": records,
            "deep_cases": cases,
            "ordinary": paired,
            "limits": {
                "deadline_ms": 5000,
                "sql_statements": 4096,
                "rows": 2048,
                "bytes": 16 * 1024 * 1024,
            },
            "privacy": "Aggregate counts only; query text, findings, identifiers "
            "and source text excluded.",
            "limitations": [
                "Not a source-backed relevance/recall quality evaluation.",
                "Cold deep queries use lexical retrieval when a query embedding is uncached.",
                "Foreground contention uses conservative deferral; this does not certify "
                "concurrent deep semantic execution.",
                "No Qwen, speech, refresh, RSS or multi-character measurements.",
            ],
        }
    finally:
        stop.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if service is not None:
            await service.aclose()
        await foreground.aclose()


def main() -> int:
    """Never overwrite an existing report or operate a live root owned by another runtime."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", required=True, type=Path)
    parser.add_argument("--character", required=True, type=UUID)
    parser.add_argument("--encoder", required=True, type=Path)
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--ordinary-samples", type=int, default=1000)
    args = Arguments.model_validate(vars(parser.parse_args()))
    if args.output.exists() or not (args.snapshot_root / "registry.db").is_file():
        raise ValueError("Use an existing isolated snapshot and a new report path.")
    if args.queries.stat().st_size > 32768:
        raise ValueError("The private query file exceeds its supported bound.")
    try:
        queries = _QUERY_LIST.validate_json(args.queries.read_bytes())
    except ValidationError:
        raise ValueError(
            "Use a JSON array of at most 32 bounded, nonempty query strings."
        ) from None
    encoder = BgeEncoder(args.encoder, threads=1)
    with CanonicalEngine(args.snapshot_root, encoder=encoder) as engine:
        report = asyncio.run(measure(engine, args, queries))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"report_written": True, "cases": len(queries) * args.samples}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
