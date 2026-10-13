"""Synthetic nonempty temporal retrieval/validation measurements using installed CPU assets."""

import argparse
import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID

from benchmark_phase_eight import Arguments, Measurement, summarize
from benchmark_phase_seven import corpus

from ember_agent.character.prompt_budget import qualified_row
from ember_agent.character.temporal import CalendarResolver
from ember_memory_abi.interfaces import ContextRequest, MemoryQuery, TemporalQuery, UpdateRequest
from ember_memory_abi.memory import EpisodePayload, FactPayload, MemoryDraft, StringValue
from ember_memory_abi.related import RelatedRequest
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.embedding import BgeEncoder

AUTHORITY = MutationAuthority(mode="confirmed_user")
REFERENCE = datetime(2026, 10, 13, 12, tzinfo=UTC)


def dated_examples(engine: CanonicalEngine, character: UUID) -> str:
    """Add ended episodes, ended states and a later correction; never change a private root."""
    subject = ensure_subject(engine, character)
    author = MemoryAuthoringService(engine, subject, authority=AUTHORITY)
    past = TemporalBounds(
        valid_from="2026-06-01T00:00:00.000000Z",
        valid_until="2026-09-01T00:00:00.000000Z",
        observed_at="2026-07-15T12:00:00.000000Z",
        precision="month",
        basis="synthetic_explicit_interval",
    )
    correction = None
    for number in range(12):
        text = "Synthetic NAS DNS Tailscale resolver repair " + str(number)
        receipt = author.create(
            NewMemoryInput(
                idempotency_key="dated:" + str(number),
                text=text,
                payload=EpisodePayload(event=text, participants=(subject,)),
                temporal=past,
                importance=0.9,
            )
        )
        correction = receipt.affected[0]
    for number in range(6):
        author.create(
            NewMemoryInput(
                idempotency_key="state:" + str(number),
                text="Synthetic former communication style " + str(number),
                payload=FactPayload(
                    subject=subject,
                    predicate="user.communication_style",
                    definition_version="0.1.0",
                    value=StringValue(value="synthetic former style " + str(number)),
                ),
                temporal=past,
                modality="PREFERENCE",
                importance=0.9,
            )
        )
    from ember_memory_abi.common import timestamp_from_datetime

    cutoff = timestamp_from_datetime(engine.clock.now())
    assert correction is not None
    writer = engine.character(character, authority=AUTHORITY)
    original = writer.get(correction.memory_id)
    writer.update(
        UpdateRequest(
            idempotency_key="metadata-correction",
            memory_id=original.memory_id,
            expected_revision=original.revision,
            draft=MemoryDraft(
                content=original.content,
                temporal=TemporalBounds(
                    observed_at=past.observed_at, basis="synthetic_corrected_unknown_event_date"
                ),
            ),
            reason="Synthetic date correction",
        )
    )
    return cutoff


def timed(operation: Callable[[], object], samples: int) -> Measurement:
    values: list[float] = []
    for _ in range(samples):
        started = perf_counter()
        operation()
        values.append((perf_counter() - started) * 1000)
    return summarize(values, 0)


def parse_metrics(samples: int) -> dict[str, object]:
    """Separate current parser cost, including Unicode, ordinary and clarification cases."""
    parser = CalendarResolver()
    questions = {
        "summer": "memories summer 2026",
        "spoken_day": "NAS on 15th of July 2026",
        "unicode_day": "Straße on 15th of July 2026",
        "calendar_range": "NAS from June 2026 until September 2026",
        "older_year": "memories from 1996",
        "technical_identifier": "RTX 2080",
        "ambiguous_year": "memories from September",
    }
    return {
        name: {
            "timing": timed(
                lambda text=text: parser.resolve(text, REFERENCE), samples
            ).model_dump(),
            "outcome": parser.resolve(text, REFERENCE).status,
        }
        for name, text in questions.items()
    }


async def measurements(
    engine: CanonicalEngine,
    character: UUID,
    args: Arguments,
    cutoff: str,
) -> dict[str, object]:
    client = InProcessAsyncMemoryClient(engine.character(character))
    event = CalendarResolver().resolve("memories summer 2026", REFERENCE).query.temporal
    queries = {
        "ended_episode_structured": MemoryQuery(
            mode="structured", kinds=("episode",), temporal=event, limit=6
        ),
        "ended_episode_topical": MemoryQuery(
            text="NAS DNS Tailscale resolver", kinds=("episode",), temporal=event, limit=6
        ),
        "ended_state_historical": MemoryQuery(
            mode="structured", kinds=("fact",), temporal=event, limit=6
        ),
        "observation_time": MemoryQuery(
            mode="structured",
            kinds=("episode",),
            limit=6,
            temporal=TemporalQuery(
                observed_from="2026-07-01T00:00:00.000000Z",
                observed_until="2026-08-01T00:00:00.000000Z",
            ),
        ),
        "recorded_before_correction": MemoryQuery(
            mode="structured",
            kinds=("episode",),
            limit=6,
            temporal=event.model_copy(update={"recorded_before": cutoff}),
        ),
    }
    output: dict[str, object] = {}
    try:
        for name, query in queries.items():
            request = ContextRequest(
                memory_query=query,
                include_profile=False,
                include_pinned=False,
                memory_token_budget=768,
            )
            context = await client.retrieve_context(request)
            certificate = context.fast_dependencies
            assert context.memories and len(context.memories) <= 6 and certificate is not None
            validation = RelatedRequest(
                intent="validate", dependencies=certificate, row_budget=512, call_budget=8
            )
            assert (await client.lookup_related(validation)).valid is True
            retrieval: list[float] = []
            validating: list[float] = []
            degraded = 0
            for _ in range(args.samples):
                started = perf_counter()
                result = await client.retrieve_context(request)
                retrieval.append((perf_counter() - started) * 1000)
                assert result.memories and len(result.memories) <= 6
                degraded += result.coverage.status != "complete"
                started = perf_counter()
                assert (await client.lookup_related(validation)).valid is True
                validating.append((perf_counter() - started) * 1000)
            output[name] = {
                "retrieval": summarize(retrieval, degraded).model_dump(),
                "historical_validation": summarize(validating, 0).model_dump(),
                "qualified_row_formatting": timed(
                    lambda selected=context.memories: json.dumps(
                        [qualified_row(item.memory) for item in selected]
                    ),
                    args.samples,
                ).model_dump(),
                "selected_memories": len(context.memories),
                "dependency_bytes": len(certificate.model_dump_json().encode()),
                "dependency_references": len(certificate.memories) + len(certificate.sources),
            }
    finally:
        await client.aclose()
    return output


def main() -> int:
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
        character = corpus(engine, args.records)
        cutoff = dated_examples(engine, character)
        engine.rebuild_index(character, encoder)
        report = {
            "fixture_records": args.records,
            "dated_examples": 18,
            "encoder": "CPU BGE small, one thread",
            "warm_samples_per_selector": args.samples,
            "measurements": asyncio.run(measurements(engine, character, args, cutoff)),
            "calendar_resolution": parse_metrics(args.samples),
            "not_measured": [
                "cold distinct-query sets",
                "simultaneous refresh/contention",
                "large archive",
                "actual peak working memory",
                "source-switch regression",
                "Qwen and actual voice TTFT",
            ],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
