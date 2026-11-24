"""Benchmark native archive and seeded belief retrieval on fictional histories, never live data."""

import argparse
import json
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from itertools import batched
from pathlib import Path
from random import Random
from statistics import mean
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

from benchmark_phase_eight import summarize
from pydantic import Field, JsonValue

from ember_memory_abi.archive import (
    ContentPart,
    Document,
    ImportCursor,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.common import ContractModel, canonical_object_sha256, timestamp_from_datetime
from ember_memory_abi.evidence import (
    Evidence,
    EvidenceAssertion,
    SourceRef,
    make_evidence_span,
    source_text_sha256,
)
from ember_memory_abi.interfaces import ArchiveQuery, MemoryQuery
from ember_memory_abi.memory import (
    ArchiveChunk,
    CandidateMemory,
    EpisodePayload,
    MemoryContent,
    MemoryDraft,
)
from ember_memory_abi.temporal import SourceTimestamp
from ember_memory_engine import CanonicalEngine, MutationAuthority, SourceGrant
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.index_builder import IndexBuilder
from ember_memory_engine.library import (
    ApprovalRequest,
    KnowledgeSelection,
    LibraryService,
    SeedRequest,
)

VERSION = "0.1.0"
NAME = "ember-synthetic-memory"
CANARY = "Obsidian marimba access phrase is paper thunder."


class Arguments(ContractModel):
    root: Path
    encoder: Path
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    seed: int = 29045
    topics: int = Field(default=24, ge=2, le=128)
    distractors: int = Field(default=256, ge=0, le=2000)
    k: int = Field(default=5, ge=1, le=20)


@dataclass(frozen=True)
class Session:
    id: str
    day: int
    text: str


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    query: str
    required: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()


def fixture(seed: int, topics: int, distractors: int) -> tuple[list[Session], list[Case]]:
    """Generate chronological histories and separate, stable scoring labels."""
    rng = Random(seed)
    colors = ("copper", "jade", "plum", "sand", "navy", "ivory", "charcoal", "scarlet")
    sessions: list[Session] = []
    cases: list[Case] = []
    names = rng.sample(range(100, 1000), topics + 1)
    for i in range(topics):
        name = f"Atelier-{names[i]}"
        alias = f"Workshop-{names[(i + 1) % len(names)]}"
        old, new = rng.sample(colors, 2)
        old_id, new_id, link_id, place_id = (f"{i}-{p}" for p in ("old", "new", "link", "place"))
        sessions.extend(
            (
                Session(
                    old_id, i, f"{name} originally picked {old} for its annual lantern exhibit."
                ),
                Session(
                    new_id,
                    160 + i,
                    f"{name} changed the lantern exhibit color from {old} to {new}.",
                ),
                Session(link_id, 5 + i, f"{alias} is our internal nickname for {name}."),
                Session(
                    place_id, 10 + i, f"The lantern exhibit for {name} uses showroom {i + 301}."
                ),
            )
        )
        cases.extend(
            (
                Case(
                    old_id,
                    "historical",
                    f"What color did {name} first pick for the lantern exhibit?",
                    (old_id,),
                ),
                Case(
                    new_id,
                    "correction",
                    f"What is the latest lantern exhibit color for {name}?",
                    (new_id,),
                    (old_id,),
                ),
                Case(
                    link_id,
                    "multi_hop_alias",
                    f"Which showroom will hold the exhibition for {alias}?",
                    (link_id, place_id),
                ),
                Case(
                    place_id,
                    "direct",
                    f"Which showroom hosts {name}'s lantern exhibit?",
                    (place_id,),
                ),
                Case(f"{i}-absent", "unanswerable", f"Who caters the lantern exhibit for {name}?"),
            )
        )
    for i in range(distractors):
        name = f"Atelier-{rng.choice(names[:-1])}"
        color = rng.choice(colors)
        topic = rng.choice(
            ("gallery sign", "theatre backdrop", "art club banner", "showroom carpet")
        )
        sessions.append(
            Session(
                f"noise-{i}",
                rng.randrange(320),
                f"{name} approved a {color} {topic} for catalog item {i + 4000}.",
            )
        )
    rng.shuffle(cases)
    return sorted(sessions, key=lambda item: item.day), cases


def record(library: UUID, session: Session) -> Document:
    stamp = timestamp_from_datetime(datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=session.day))
    return Document(
        source=SourceRef(
            source_store_id=library, record_kind="document", record_id=uuid4(), record_revision=1
        ),
        recorded_at=stamp,
        created_time=SourceTimestamp(value=stamp, basis="synthetic_fixture", precision="day"),
        parts=(
            ContentPart(
                part_id=uuid4(),
                kind="text",
                status="available",
                text=session.text,
                content_sha256=source_text_sha256(session.text),
            ),
        ),
    )


def load_library(
    engine: CanonicalEngine, sessions: list[Session], name: str
) -> tuple[UUID, dict[UUID, str], list[Document]]:
    library = engine.create_library(name).store_id
    archive = engine.archive(library)
    docs = [record(library, session) for session in sessions]
    ids = {doc.source.record_id: session.id for doc, session in zip(docs, sessions, strict=True)}
    digest = canonical_object_sha256([asdict(session) for session in sessions])
    spec = ProcessingSpecRef(
        name=NAME,
        version=VERSION,
        specification_sha256=canonical_object_sha256({"name": NAME, "version": VERSION}),
    )
    count = 0
    for batch in batched(docs, 128):
        count += len(batch)
        archive.write_batch(
            NormalizedArchiveBatch(
                source_store_id=library,
                source_namespace=archive.namespace,
                idempotency_key=f"fixture-{count}",
                processing_spec=spec,
                records=batch,
                resume_cursor=ImportCursor(
                    logical_inventory_sha256=digest,
                    component_sha256=digest,
                    next_record_index=count,
                    adapter=NAME,
                    adapter_version=VERSION,
                ),
            )
        )
    return library, ids, docs


def seed_beliefs(
    engine: CanonicalEngine, character: UUID, library: UUID, docs: list[Document]
) -> None:
    service = LibraryService(engine, authority=MutationAuthority(mode="confirmed_user"))
    selections: list[KnowledgeSelection] = []
    for doc in docs:
        part = doc.parts[0]
        text = part.text
        assert text is not None
        source = doc.source.model_copy(update={"part_id": part.part_id})
        assertion = EvidenceAssertion(
            evidence=Evidence(
                evidence_id=uuid4(), source=source, span=make_evidence_span(text, 0, len(text))
            ),
            relation="supports",
            modality="FACTUAL_ASSERTION",
            authority="synthetic_confirmed_fixture",
            verdict="supported",
        )
        approval = service.approve(
            library,
            ApprovalRequest(
                idempotency_key=f"approve-{source.record_id}",
                candidate=CandidateMemory(
                    candidate_id=uuid4(),
                    owner_store_id=library,
                    revision=1,
                    recorded_at=doc.recorded_at,
                    draft=MemoryDraft(
                        content=MemoryContent(
                            kind="episode",
                            payload=EpisodePayload(event=text),
                            modality="FACTUAL_ASSERTION",
                            confidence=1.0,
                            importance=0.7,
                            evidence=(assertion,),
                        )
                    ),
                ),
            ),
        )
        selections.append(
            KnowledgeSelection(knowledge_id=approval.knowledge_id, revision=approval.revision)
        )
    for index, batch in enumerate(batched(selections, 128)):
        service.seed(
            character,
            SeedRequest(idempotency_key=f"seed-{index}", library_id=library, selections=batch),
        )


@dataclass(frozen=True)
class Sample:
    id: str
    category: str
    coverage: str
    reason_codes: tuple[str, ...]
    ms: float
    returned: tuple[str, ...]
    recall: float | None
    precision: float | None
    all_required: bool | None
    stale_returned: bool


def query(
    store: SqliteMemoryStore,
    lane: Literal["archive", "belief"],
    case: Case,
    id_map: dict[UUID, str],
    k: int,
) -> Sample:
    started = perf_counter()
    if lane == "archive":
        archive = store.search_archive(ArchiveQuery(text=case.query, limit=k))
        refs = [
            ref
            for item in archive.results
            for ref in (
                [span.evidence.source for span in item.record.source_spans]
                if isinstance(item.record, ArchiveChunk)
                else [item.record.source]
            )
        ]
        coverage = archive.coverage
    else:
        beliefs = store.search(MemoryQuery(text=case.query, limit=k))
        refs = [
            assertion.evidence.source
            for item in beliefs.results
            for assertion in item.memory.content.evidence
        ]
        coverage = beliefs.coverage
    ms = (perf_counter() - started) * 1000
    returned = tuple(dict.fromkeys(id_map.get(ref.record_id, "unknown-source") for ref in refs))
    matches = len(set(case.required).intersection(returned))
    return Sample(
        case.id,
        case.category,
        coverage.status,
        coverage.reason_codes,
        ms,
        returned,
        matches / len(case.required) if case.required else None,
        matches / len(returned) if returned else (0.0 if case.required else None),
        matches == len(case.required) if case.required else None,
        bool(set(returned).intersection(case.stale)),
    )


def metrics(samples: list[Sample]) -> dict[str, object]:
    recall = [sample.recall for sample in samples if sample.recall is not None]
    precision = [
        sample.precision
        for sample in samples
        if sample.precision is not None and sample.recall is not None
    ]
    return {
        "latency": summarize(
            [s.ms for s in samples], sum(s.coverage != "complete" for s in samples)
        ).model_dump(),
        "labeled_cases": len(recall),
        "mean_evidence_recall": mean(recall) if recall else None,
        "mean_evidence_precision": mean(precision) if precision else None,
        "all_required_hits": sum(s.all_required is True for s in samples),
        "stale_returned": sum(s.stale_returned for s in samples),
        "empty_results": sum(not s.returned for s in samples),
        "unanswerable_with_results": sum(
            s.category == "unanswerable" and bool(s.returned) for s in samples
        ),
    }


def run(args: Arguments) -> int:
    if not args.encoder.is_dir():
        raise ValueError("Local encoder assets must already exist.")
    args.root.mkdir(parents=True, exist_ok=False)
    sessions, cases = fixture(args.seed, args.topics, args.distractors)
    labels: list[JsonValue] = [
        {
            "id": case.id,
            "category": case.category,
            "query": case.query,
            "required": list(case.required),
            "stale": list(case.stale),
        }
        for case in cases
    ]
    with (args.root / "cases.json").open("x", encoding="utf-8", newline="\n") as output:
        json.dump(labels, output, indent=2)
        output.write("\n")
    encoder = BgeEncoder(args.encoder, threads=1)
    checks: dict[str, bool] = {}
    report: dict[str, object] = {
        "version": VERSION,
        "dataset": NAME,
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "ember_revision": args.revision,
        "encoder": encoder.specification.model_dump(mode="json"),
        "threads": 1,
        "seed": args.seed,
        "topics": args.topics,
        "distractors": args.distractors,
        "k": args.k,
        "archive_ranking": "default",
        "scored_sessions": len(sessions),
        "fixture_sha256": canonical_object_sha256(
            {
                "sessions": [asdict(s) for s in sessions],
                "cases": labels,
            }
        ),
        "unanswerable_note": "Retrieval can return distractors; abstention is not scored.",
        "isolation": checks,
    }
    with CanonicalEngine(args.root / "store", encoder=encoder) as engine:
        character = engine.create_character().store_id
        canary_character = engine.create_character().store_id
        started = perf_counter()
        library, ids, docs = load_library(engine, sessions, NAME)
        report["library_id"] = str(library)
        canary_library, canary_ids, canary_docs = load_library(
            engine, [Session("canary", 0, CANARY)], NAME + "-isolation"
        )
        ids.update(canary_ids)
        for owner, documents in ((character, docs), (canary_character, canary_docs)):
            for batch in batched(documents, 128):
                engine.grant_source(
                    owner,
                    SourceGrant(
                        references=tuple(d.source for d in batch),
                        capabilities=frozenset({"archive_search"}),
                    ),
                )
        report["archive_import_and_grant_seconds"] = perf_counter() - started
        index = IndexBuilder(engine, encoder)
        started = perf_counter()
        for store_id in (library, canary_library, character, canary_character):
            index.rebuild(store_id)
        report["archive_index_seconds"] = perf_counter() - started
        canary = Case("isolation", "isolation", CANARY, ("canary",))
        for lane in ("archive", "belief"):
            if lane == "belief":
                started = perf_counter()
                seed_beliefs(engine, character, library, docs)
                seed_beliefs(engine, canary_character, canary_library, canary_docs)
                report["belief_approve_and_seed_seconds"] = perf_counter() - started
                started = perf_counter()
                for owner in (character, canary_character):
                    index.rebuild(owner)
                report["belief_index_seconds"] = perf_counter() - started
            store = engine.character(character)
            # First pass has distinct query text; second is repeated, not a process restart.
            cold = [query(store, lane, case, ids, args.k) for case in cases]
            warm = [query(store, lane, case, ids, args.k) for case in cases]
            owner_hit = query(engine.character(canary_character), lane, canary, ids, args.k)
            outsider_hit = query(store, lane, canary, ids, args.k)
            checks[lane] = (
                owner_hit.all_required is True
                and "canary" not in outsider_hit.returned
                and all(
                    "canary" not in s.returned and "unknown-source" not in s.returned
                    for s in cold + warm
                )
            )
            report[lane] = {
                "first_pass": metrics(cold),
                "repeated_pass": metrics(warm),
                "by_category": {
                    cat: metrics([s for s in cold if s.category == cat])
                    for cat in sorted({c.category for c in cases})
                },
                "first_pass_cases": [asdict(s) for s in cold],
                "repeated_pass_cases": [asdict(s) for s in warm],
            }
    report["store_bytes"] = sum(
        p.stat().st_size for p in (args.root / "store").rglob("*") if p.is_file()
    )
    with (args.root / "report.json").open("x", encoding="utf-8", newline="\n") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(f"{len(cases)} cases per lane; isolation={checks}; report={args.root / 'report.json'}")
    return 0 if all(checks.values()) else 1


def main() -> int:
    """Require a fresh directory and explicit revision; no network or model generation."""
    parser = argparse.ArgumentParser(description=__doc__)
    for path in ("--root", "--encoder"):
        parser.add_argument(path, type=Path, required=True)
    parser.add_argument("--revision", required=True)
    for option in ("--seed", "--topics", "--distractors", "--k"):
        parser.add_argument(option, type=int, default=argparse.SUPPRESS)
    return run(Arguments.model_validate(vars(parser.parse_args())))


if __name__ == "__main__":
    raise SystemExit(main())
