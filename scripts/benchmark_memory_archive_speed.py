"""Prepare frozen public/synthetic stores, then time native hybrid archive retrieval.

Run from repo root: python -m scripts.benchmark_memory_archive_speed --help
Reuse one prepared fixture for both revisions. `run --profile` is diagnostic only.
"""

import argparse
import json
import platform
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, JsonValue
from scripts import benchmark_memory_amb as amb  # shared pinned input loader
from scripts.benchmark_memory_amb_archive import InputDocument, ingest
from scripts.benchmark_memory_archive_profile import Trace, current, profile

from ember_memory_abi.common import canonical_object_sha256
from ember_memory_abi.errors import DeadlineExceededError
from ember_memory_abi.interfaces import ArchiveQuery, ArchiveSearchResponse
from ember_memory_abi.memory import ArchiveChunk
from ember_memory_engine import CanonicalEngine, SourceGrant
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.embedding import BgeEncoder, file_sha256
from ember_memory_engine.search_contracts import Encoder


class Case(BaseModel):
    """Only caller fields; never scorer assertions."""

    id: str
    text: str
    character: UUID | None
    limit: int
    source_store_ids: tuple[UUID, ...] = ()


class Fixture(BaseModel):
    """Receipt binding ordered requests to exact, already indexed store bytes."""

    schema_version: Literal[1] = 1
    dataset: str
    input_sha256: str
    store_sha256: str
    encoder_sha256: str
    cases: list[Case]
    grants: dict[UUID, set[tuple[UUID, str, UUID, int]]]


def _tree_sha256(root: Path, pattern: str) -> str:
    return canonical_object_sha256(
        {p.relative_to(root).as_posix(): file_sha256(p) for p in sorted(root.rglob(pattern))}
    )


def _pmb(
    engine: CanonicalEngine, encoder: Encoder, checkout: Path, fixtures: Path
) -> tuple[str, list[Case]]:
    git = amb._git  # pyright: ignore[reportPrivateUsage]
    if git(checkout, "rev-parse", "HEAD").decode().strip() != amb.AMB_COMMIT:
        raise ValueError("Use pinned AMB checkout.")
    if git(checkout, "status", "--porcelain", "--untracked-files=all", "--", "src"):
        raise ValueError("AMB scorer source differs from pinned commit.")
    scorer = amb._precision(checkout, fixtures)  # pyright: ignore[reportPrivateUsage]
    split = "single-turn"
    groups: dict[str | None, list[InputDocument]] = defaultdict(list)
    for doc in scorer.dataset.load_documents(split):
        groups[doc.user_id].append(InputDocument(doc.id, doc.content, doc.user_id, doc.timestamp))
    inventory: dict[str, JsonValue] = {
        "amb": amb.AMB_COMMIT,
        "split": split,
        "fixtures": dict(amb.PMB_FILES),
    }
    units: dict[str | None, tuple[UUID, UUID]] = {}
    for user, docs in groups.items():
        library, _, character = ingest(
            engine,
            docs,
            f"amb-unit-{len(units)}",
            canonical_object_sha256({**inventory, "unit": user}),
        )
        engine.rebuild_index(library, encoder)
        units[user] = library, character
    cases: list[Case] = []
    # Same order, retrieval text and cap as native PMB run; no scorer inside timing.
    by_user: dict[str | None, list[amb.PublicQuery]] = defaultdict(list)
    for query in scorer.queries.values():
        by_user[query.user_id].append(amb.PublicQuery.model_validate(query, from_attributes=True))
    for user in dict.fromkeys((*units, *by_user)):
        unit = units.get(user)
        for query in by_user.get(user, ()):
            budget = amb.request_budget(query, precision=True)
            cases.append(
                Case(
                    id=query.id,
                    text=query.retrieval_text,
                    character=unit[1] if unit else None,
                    limit=min(20, budget) if budget is not None else 20,
                    source_store_ids=(unit[0],) if unit else (),
                )
            )
    return canonical_object_sha256(inventory), cases


def _synthetic(
    engine: CanonicalEngine, encoder: Encoder, *, topics: int = 24, distractors: int = 256
) -> tuple[str, list[Case]]:
    # The existing synthetic CLI imports sibling scripts from its script directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from scripts.benchmark_memory_synthetic import CANARY, Session, fixture, load_library
    finally:
        sys.path.pop(0)
    sessions, queries = fixture(29045, topics, distractors)
    character = engine.create_character().store_id
    for i in range(2):
        library, _, docs = load_library(
            engine, [*sessions[i::2], Session(f"denied-{i}", 0, CANARY)], f"archive-speed-{i}"
        )
        engine.grant_source(
            character,
            SourceGrant(
                references=tuple(doc.source for doc in docs[:-1]),
                capabilities=frozenset({"archive_search"}),
            ),
        )
        engine.rebuild_index(library, encoder)
    engine.rebuild_index(character, encoder)
    cases = [Case(id=q.id, text=q.query, character=character, limit=5) for q in queries]
    cases.append(Case(id="ungranted-canary", text=CANARY, character=character, limit=5))
    return canonical_object_sha256(
        {"sessions": [asdict(s) for s in sessions], "canary": CANARY}
    ), cases


def prepare(root: Path, encoder: Encoder, *, pmb: tuple[Path, Path] | None = None) -> None:
    """Build immutable benchmark snapshot, with no service, private data or GPU use."""
    root.mkdir(parents=True, exist_ok=False)
    with CanonicalEngine(root / "store", encoder=encoder) as engine:
        source_sha, cases = _pmb(engine, encoder, *pmb) if pmb else _synthetic(engine, encoder)
        grants: dict[UUID, set[tuple[UUID, str, UUID, int]]] = {}
        for character in {c.character for c in cases if c.character is not None}:
            _, database = engine.maintenance_store(character)
            rows = database.read(
                lambda conn: conn.execute(
                    "SELECT source_store_id,record_kind,record_id,record_revision"
                    " FROM source_grants WHERE capability='archive_search' AND part_id=''"
                ).fetchall()
            )
            grants[character] = {(UUID(r[0]), r[1], UUID(r[2]), r[3]) for r in rows}
    fixture = Fixture(
        dataset="precisionmembench/single-turn" if pmb else "synthetic/multi-library",
        input_sha256=source_sha,
        store_sha256=_tree_sha256(root / "store", "*.db"),
        encoder_sha256=canonical_object_sha256(encoder.specification.model_dump(mode="json")),
        grants=grants,
        cases=cases,
    )
    (root / "fixture.json").write_text(fixture.model_dump_json(indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Sample:
    case: str
    phase: str
    ms: float | None
    status: str
    reasons: tuple[str, ...] = ()
    ids_sha256: str | None = None
    provenance_sha256: str | None = None
    coverage_sha256: str | None = None


def _request(case: Case) -> ArchiveQuery:
    return ArchiveQuery(text=case.text, source_store_ids=case.source_store_ids, limit=case.limit)


def _sample(
    case: Case,
    phase: str,
    ms: float,
    response: ArchiveSearchResponse,
    allowed: set[tuple[UUID, str, UUID, int]],
) -> Sample:
    identities: list[JsonValue] = []
    provenance: list[JsonValue] = []
    for item in response.results:
        record = item.record
        if isinstance(record, ArchiveChunk):
            identities.append(str(record.chunk_id))
            refs = [span.evidence.source for span in record.source_spans]
            sources = [span.model_dump(mode="json") for span in record.source_spans]
        else:
            source = record.source.model_dump(mode="json")
            identities.append(source)
            refs = [record.source]
            sources = [source]
        if any(
            ref.identity_key()[:4] not in allowed
            or (case.source_store_ids and ref.source_store_id not in case.source_store_ids)
            for ref in refs
        ):
            raise ValueError(f"{case.id}: returned unauthorized exact archive source.")
        provenance.append({"branch": item.branch_status, "sources": list(sources)})
    return Sample(
        case.id,
        phase,
        ms,
        response.coverage.status,
        response.coverage.reason_codes,
        canonical_object_sha256(identities),
        canonical_object_sha256(provenance),
        canonical_object_sha256(
            {"coverage": response.coverage.model_dump(mode="json"), "next": response.next_cursor}
        ),
    )


def _latency(ms: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(ms)
    n = len(ordered)
    return {
        "samples": n,
        "p50_ms": median(ordered) if n else None,
        "p95_ms": ordered[(95 * n + 99) // 100 - 1] if n else None,
        "p99_ms": ordered[(99 * n + 99) // 100 - 1] if n else None,
    }


def _summary(samples: list[Sample], traces: list[Trace | None]) -> dict[str, object]:
    groups: dict[str, list[tuple[Sample, Trace | None]]] = defaultdict(list)
    for sample, trace in zip(samples, traces, strict=True):
        groups[sample.phase].append((sample, trace))
    return {
        phase: {
            **_latency([s.ms for s, _ in group if s.ms is not None]),
            "over_500_ms": sum(s.ms is not None and s.ms > 500 for s, _ in group),
            "coverage": dict(Counter(s.status for s, _ in group)),
            "coverage_reasons": dict(Counter(r for s, _ in group for r in s.reasons)),
            "deadline_or_admission_cases": sum(
                s.status == "deadline_exception" or any("deadline" in r for r in s.reasons)
                for s, _ in group
            ),
            "stages": {
                name: {
                    **_latency([v.ns / 1e6 if v else 0.0 for v in values]),
                    "calls": sum(v.calls for v in values if v),
                    "rows": sum(v.rows for v in values if v),
                }
                for name in sorted({key for _, t in group if t for key in t.stages})
                if (values := [t.stages.get(name) if t else None for _, t in group])
            },
            "digests": {
                "ids_sha256": canonical_object_sha256([[s.case, s.ids_sha256] for s, _ in group]),
                "provenance_sha256": canonical_object_sha256(
                    [[s.case, s.provenance_sha256] for s, _ in group]
                ),
                "coverage_sha256": canonical_object_sha256(
                    [[s.case, s.coverage_sha256] for s, _ in group]
                ),
            },
        }
        for phase, group in groups.items()
    }


def _passes(
    engine: CanonicalEngine, fixture: Fixture, *, diagnostic: bool
) -> tuple[list[Sample], list[Trace | None]]:
    clients: dict[UUID, SqliteMemoryStore] = {}
    for case in fixture.cases:
        if case.character is not None and case.character not in clients:
            client = clients[case.character] = engine.character(case.character)
            probe = case.model_copy(update={"text": "Benchmark warmup probe", "limit": 1})
            client.search_archive(_request(probe))

    samples: list[Sample] = []
    traces: list[Trace | None] = []
    seen: set[str] = set()
    for repeated in (False, True):
        for case in fixture.cases:
            trace: Trace | None = None
            phase = "repeat" if repeated else "duplicate" if case.text in seen else "distinct"
            if not case.text.strip() or case.limit == 0:
                sample = Sample(case.id, phase, None, "skipped")
            elif case.character is None:
                sample = Sample(case.id, phase, None, "no_unit")
            else:
                seen.add(case.text)
                client = clients[case.character]
                request = _request(case)
                trace = Trace() if diagnostic else None
                token = current.set(trace) if trace is not None else None
                start = perf_counter_ns()
                try:
                    response = client.search_archive(request)
                except DeadlineExceededError:
                    sample = Sample(
                        case.id, phase, (perf_counter_ns() - start) / 1e6, "deadline_exception"
                    )
                else:
                    ms = (perf_counter_ns() - start) / 1e6
                    sample = _sample(case, phase, ms, response, fixture.grants[case.character])
                finally:
                    if token is not None:
                        current.reset(token)
            samples.append(sample)
            traces.append(trace)
    return samples, traces


def run(root: Path, encoder: Encoder, output: Path, *, diagnostic: bool) -> None:
    """Time two warm-process passes on disposable copy; diagnostic native work may overlap."""
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Output must be a new file in an existing directory.")
    manifest = root / "fixture.json"
    fixture = Fixture.model_validate_json(manifest.read_bytes())
    if fixture.encoder_sha256 != canonical_object_sha256(
        encoder.specification.model_dump(mode="json")
    ):
        raise ValueError("Encoder differs from frozen fixture.")
    with TemporaryDirectory(prefix="archive-speed-", dir=output.parent) as scratch:
        store = Path(scratch) / "store"
        shutil.copytree(root / "store", store)
        if fixture.store_sha256 != _tree_sha256(store, "*.db"):
            raise ValueError("Benchmark store differs from frozen fixture.")
        with profile(encoder) if diagnostic else nullcontext():
            with CanonicalEngine(store, encoder=encoder) as engine:
                samples, traces = _passes(engine, fixture, diagnostic=diagnostic)

    checkout = Path(__file__).resolve().parents[1]
    git = amb._git  # pyright: ignore[reportPrivateUsage]
    report = {
        "schema": "ember.archive-speed@1",
        "dataset": fixture.dataset,
        "fixture_sha256": file_sha256(manifest),
        "store_sha256": fixture.store_sha256,
        "input_sha256": fixture.input_sha256,
        "encoder": encoder.specification.model_dump(mode="json"),
        "profiled": diagnostic,
        "archive_ranking": "default",
        "ember_commit": git(checkout, "rev-parse", "HEAD").decode().strip(),
        "engine_sha256": _tree_sha256(checkout / "ember-memory/packages/memory-engine/src", "*.py"),
        "benchmark_code_sha256": {
            path.name: file_sha256(path)
            for path in Path(__file__).resolve().parent.glob("benchmark_memory_archive_*.py")
        },
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "threads": 1,
        "phases": _summary(samples, traces),
        "cases": [
            {**asdict(s), **({"stages": {k: asdict(v) for k, v in t.stages.items()}} if t else {})}
            for s, t in zip(samples, traces, strict=True)
        ],
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"{fixture.dataset} (profiled={diagnostic}): {output}", flush=True)


def main() -> None:
    """Prepare a new root or time a disposable replay from a frozen benchmark root."""
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare", help="create a new frozen benchmark root")
    bench = commands.add_parser("run", help="time one warm-process distinct/repeat pair")
    for command in (build, bench):
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--encoder", type=Path, required=True)
    build.add_argument("--amb", type=Path, help="pinned AMB checkout; omit for synthetic fixture")
    build.add_argument("--fixtures", type=Path, help="pinned PMB JSON directory")
    bench.add_argument("--output", type=Path, required=True)
    bench.add_argument("--profile", action="store_true", help="install diagnostic-only probes")
    args = cli.parse_args()
    encoder = BgeEncoder(args.encoder, threads=1)
    root = args.root.resolve()
    if args.command == "prepare":
        if bool(args.amb) != bool(args.fixtures):
            cli.error("Pass both --amb and --fixtures for PMB, or neither for synthetic.")
        prepare(root, encoder, pmb=(args.amb, args.fixtures) if args.amb else None)
    else:
        run(root, encoder, args.output, diagnostic=args.profile)


if __name__ == "__main__":
    main()
