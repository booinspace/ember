"""Curated synthetic NAS evidence probe with a real installed CPU encoder; no model generation."""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from benchmark_phase_seven import corpus

from ember_agent.character.temporal import CalendarResolver
from ember_agent.memory.deep_contracts import DeepRecallRequest
from ember_agent.memory.deep_recall import DeepRecallService
from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_abi.memory import Entity, EntityRef, EpisodePayload
from ember_memory_abi.selection import RecallSelection
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.read_budget import ReadBudget

REFERENCE = "2026-10-13T12:00:00.000000Z"
QUERY = "NAS networking memories last year"


def examples(engine: CanonicalEngine, character: UUID) -> set[UUID]:
    subject = ensure_subject(engine, character)
    nas = EntityRef(store_id=character, entity_id=uuid4())
    engine.put_entity(
        character,
        Entity(reference=nas, revision=1, name="Synthetic NAS", aliases=("NAS", "homelab")),
    )
    author = MemoryAuthoringService(
        engine, subject, authority=MutationAuthority(mode="confirmed_user")
    )
    expected: set[UUID] = set()
    for number, event in enumerate(
        (
            "NAS networking project: Tailscale MagicDNS resolved remote names.",
            "NAS networking project: AdGuard Home handled local DNS requests.",
            "NAS networking project: OPNsense forwarded split DNS to the resolver.",
            "NAS networking project: TrueNAS used a static homelab address.",
            "NAS networking project: UniFi VLAN isolation needed an explicit firewall rule.",
            "NAS networking project: a resolver change repaired intermittent hostname failures.",
            "NAS networking independent office incident: another router lost a DNS route.",
            "NAS networking independent travel incident: a hotel blocked remote access.",
        )
    ):
        receipt = author.create(
            NewMemoryInput(
                idempotency_key="curated:" + str(number),
                text=event,
                payload=EpisodePayload(event=event, participants=(subject, nas)),
                temporal=TemporalBounds(
                    valid_from="2025-07-01T00:00:00.000000Z",
                    valid_until="2025-08-01T00:00:00.000000Z",
                    observed_at="2025-07-15T12:00:00.000000Z",
                    precision="month",
                    basis="synthetic_supported_interval",
                ),
            )
        )
        expected.add(receipt.affected[0].memory_id)
    return expected


async def probe(engine: CanonicalEngine, character: UUID, expected: set[UUID]) -> dict[str, object]:
    from datetime import datetime

    store = engine.character(character)
    resolution = CalendarResolver().resolve(QUERY, datetime.fromisoformat(REFERENCE))
    # Cold deep cannot initiate a CPU encode. Warm the exact ordinary query explicitly.
    store.search(MemoryQuery(text=resolution.query.text, temporal=resolution.query.temporal))
    foreground = InProcessAsyncMemoryClient(store)

    def factory(deadline: float) -> InProcessAsyncMemoryClient:
        budget = ReadBudget(deadline=deadline)
        return InProcessAsyncMemoryClient(
            store, deep_sources=DeepSources(engine, budget), read_budget=budget
        )

    service = DeepRecallService(character, factory, validation=foreground)
    request = DeepRecallRequest(
        request_id=uuid4(),
        character_id=character,
        context_id=uuid4(),
        query=QUERY,
        reference_at=REFERENCE,
    )
    try:
        started = perf_counter()
        result = await service.investigate(request)
        elapsed = (perf_counter() - started) * 1000
        selected = {item.memory.memory_id for item in result.memories}
        assert all(item.ranking for item in result.memories)
        precision = len(selected & expected) / len(selected) if selected else 0
        recall = len(selected & expected) / len(expected)
        assert all(len(cluster.members) == 1 for cluster in result.clusters), result.clusters
        supported = {
            item.memory.memory_id
            for item in result.memories
            if any(
                assertion.evidence == observation.source.evidence
                for assertion in item.memory.content.evidence
                for observation in result.observations
            )
        }
        assert await service.inspect(request.request_id, request.context_id) is not None
        wrong = await service.investigate(
            request.model_copy(
                update={"request_id": uuid4(), "character_id": engine.create_character().store_id}
            )
        )
        assert not wrong.memories and not wrong.observations
        engine.character(
            character, authority=MutationAuthority(mode="confirmed_user")
        ).configure_recall(RecallSelection(local_enabled=False, import_mode="none"))
        assert await service.inspect(request.request_id, request.context_id) is None
        denied = await service.investigate(request.model_copy(update={"request_id": uuid4()}))
        assert not denied.memories and not denied.observations
        return {
            "fixture": "synthetic dated NAS projects with two independently labelled alternatives",
            "encoder": "installed CPU BGE small, one thread",
            "query_cache": "one explicitly warmed ordinary topical query",
            "expected_relevant_memories": len(expected),
            "selected": len(selected),
            "precision": precision,
            "support_recall": recall,
            "exact_supported_selected": len(selected & supported),
            "independent_clusters": len(result.clusters),
            "outcome": result.outcome,
            "coverage_reasons": result.reasons,
            "elapsed_ms": elapsed,
            "counters": result.counters.model_dump(),
            "cross_character_and_source_switch": "passed",
            "retrieval_gate_passed": precision >= 0.85 and recall >= 0.80,
            "not_measured": [
                "actual Qwen answer reconstruction",
                "cold query semantic recall",
                "incidents without explicit entity annotations",
                "broad paraphrase query sets",
                "private imported corpus",
                "large corpus or concurrent index refresh",
            ],
        }
    finally:
        await service.aclose()
        await foreground.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, directory, output = Path(args.root), Path(args.encoder), Path(args.output)
    if root.exists() or output.exists():
        raise ValueError("Probe requires a new synthetic root and report.")
    encoder = BgeEncoder(directory, threads=1)
    with CanonicalEngine(root, encoder=encoder) as engine:
        character = corpus(engine, 1000)
        expected = examples(engine, character)
        engine.rebuild_index(character, encoder)
        report = asyncio.run(probe(engine, character, expected))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0 if report["retrieval_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
