r"""Sweep synthetic archive scale in disposable stores, with local CPU only.

From repo root (create .local/ first):
  uv run --locked --offline python scripts/benchmark_memory_archive_scale.py \
      --output .local/archive-scale.json --profile
Omit --encoder for a tied-vector smoke, not a qualification run.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sqlite3
from collections import Counter
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from importlib.metadata import version
from inspect import getfile
from itertools import batched, product
from pathlib import Path
from random import Random
from statistics import median
from struct import pack
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from benchmark_memory_archive_profile import Stage, Trace, current, profile
from pydantic import Field, ValidationError

from ember_memory_abi.archive import ImportCursor, NormalizedArchiveBatch, ProcessingSpecRef
from ember_memory_abi.common import ContractModel, canonical_object_sha256
from ember_memory_abi.errors import DeadlineExceededError
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.interfaces import ArchiveQuery
from ember_memory_abi.memory import ArchiveChunk
from ember_memory_engine import CanonicalEngine, SourceGrant
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.embedding import BgeEncoder, file_sha256
from ember_memory_engine.errors import EngineBusyError
from ember_memory_engine.search_contracts import EmbeddingSpec, Encoder

if TYPE_CHECKING:
    from benchmark_memory_synthetic import Session

_TOPICS = 8
_LIMIT = 5
_DEADLINE_OR_ADMISSION = frozenset(
    {
        DeadlineExceededError.code,
        EngineBusyError.code,
        "lexical_soft_deadline_miss",
        "semantic_deadline_or_admission_miss",
    }
)
type ExactRef = tuple[UUID, str, UUID, int]


class Arguments(ContractModel):
    """Validate the sweep axes before creating scratch stores."""

    output: Path
    encoder: Path | None = None
    libraries: list[Annotated[int, Field(ge=1, le=128)]] = Field(default=[1, 4], min_length=1)
    history_records: list[Annotated[int, Field(ge=4 * _TOPICS)]] = Field(
        default=[128, 512], min_length=1
    )
    grant_percent: list[Annotated[int, Field(ge=1, le=100)]] = Field(
        default=[10, 100], min_length=1
    )
    queries: int = Field(default=12, ge=1, le=5 * _TOPICS)
    append_records: int = Field(default=4, ge=1, le=128)
    seed: int = 29045
    profile: bool = False


class _SmokeEncoder:
    # ponytail: tied vectors exercise native KNN; use --encoder for actual similarity and cost.
    specification = EmbeddingSpec(
        model="ember.archive-scale-smoke",
        model_revision="unit-vector-v1",
        tokenizer_revision="whitespace-v1",
        model_sha256="0" * 64,
        tokenizer_sha256="0" * 64,
        query_prefix="",
        encoder_version="ember.archive-scale-smoke@1",
    )
    _vector = pack("<384f", 1.0, *([0.0] * 383))

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple(match.span() for match in re.finditer(r"\S+", text))

    def token_count(self, text: str) -> int:
        return len(self.offsets(text)) + 2

    def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[bytes, ...]:
        return (self._vector,) * len(texts)


@dataclass(frozen=True)
class _Sample:
    ms: float
    coverage: str
    reasons: tuple[str, ...]
    results: int | None
    trace: Trace | None


def _elapsed(start: int) -> float:
    return (perf_counter_ns() - start) / 1e6


def _grant(
    engine: CanonicalEngine, character: UUID, refs: Sequence[SourceRef], allowed: set[ExactRef]
) -> None:
    for batch in batched(refs, 128):
        engine.grant_source(
            character, SourceGrant(references=batch, capabilities=frozenset({"archive_search"}))
        )
        allowed.update(ref.identity_key()[:4] for ref in batch)


def _append(engine: CanonicalEngine, library: UUID, count: int) -> tuple[list[SourceRef], float]:
    from benchmark_memory_synthetic import NAME, VERSION, Session, record

    writer = engine.archive(library)
    sessions = [Session(f"append-{i}", 400 + i, f"Scale append marker {i}.") for i in range(count)]
    records = tuple(record(library, session) for session in sessions)
    digest = canonical_object_sha256([asdict(session) for session in sessions])
    batch = NormalizedArchiveBatch(
        source_store_id=library,
        source_namespace=writer.namespace,
        idempotency_key="scale-append",
        records=records,
        processing_spec=ProcessingSpecRef(
            name=NAME,
            version=VERSION,
            specification_sha256=canonical_object_sha256({"name": NAME, "version": VERSION}),
        ),
        resume_cursor=ImportCursor(
            logical_inventory_sha256=digest,
            component_sha256=digest,
            next_record_index=count,
            adapter=NAME,
            adapter_version=VERSION,
        ),
    )
    start = perf_counter_ns()
    receipt = writer.write_batch(batch)
    ms = _elapsed(start)
    if receipt.records_written != count:
        raise RuntimeError("Archive append did not write all new records.")
    return [item.source for item in records], ms


def _latency(values: list[float]) -> dict[str, float | int]:
    values.sort()
    count = len(values)
    return {
        "samples": count,
        "p50_ms": median(values),
        "p95_ms": values[(95 * count + 99) // 100 - 1],
        "p99_ms": values[(99 * count + 99) // 100 - 1],
    }


def _summary(samples: list[_Sample]) -> dict[str, object]:
    stages: dict[str, list[Stage]] = {}
    for sample in samples:
        if sample.trace is not None:
            for name, stage in sample.trace.stages.items():
                stages.setdefault(name, []).append(stage)
    return {
        **_latency([sample.ms for sample in samples]),
        "over_500_ms": sum(sample.ms > 500 for sample in samples),
        "coverage": dict(Counter(sample.coverage for sample in samples)),
        "coverage_reasons": dict(
            Counter(reason for sample in samples for reason in sample.reasons)
        ),
        "deadline_or_admission_cases": sum(
            not _DEADLINE_OR_ADMISSION.isdisjoint(sample.reasons) for sample in samples
        ),
        "empty_results": sum(sample.results == 0 for sample in samples),
        "returned_results": sum(sample.results or 0 for sample in samples),
        "stages": {
            name: {
                **_latency([stage.ns / 1e6 for stage in values]),
                "calls": sum(stage.calls for stage in values),
                "rows": sum(stage.rows for stage in values),
            }
            for name, values in sorted(stages.items())
        },
    }


def _sample(
    store: SqliteMemoryStore, query: ArchiveQuery, allowed: set[ExactRef], *, diagnostic: bool
) -> _Sample:
    trace = Trace() if diagnostic else None
    token = current.set(trace) if trace is not None else None
    start = perf_counter_ns()
    try:
        response = store.search_archive(query)
        ms = _elapsed(start)
    except (DeadlineExceededError, EngineBusyError) as error:
        return _Sample(_elapsed(start), error.code, (error.code,), None, trace)
    finally:
        if token is not None:
            current.reset(token)
    for hit in response.results:
        refs = (
            tuple(span.evidence.source for span in hit.record.source_spans)
            if isinstance(hit.record, ArchiveChunk)
            else (hit.record.source,)
        )
        if not refs or any(ref.identity_key()[:4] not in allowed for ref in refs):
            raise RuntimeError("Archive hit lacks authorized exact source revisions.")
    return _Sample(
        ms, response.coverage.status, response.coverage.reason_codes, len(response.results), trace
    )


def _search(
    root: Path,
    encoder: Encoder,
    character: UUID,
    requests: list[ArchiveQuery],
    allowed: set[ExactRef],
    *,
    diagnostic: bool = False,
) -> dict[str, object]:
    probes = profile(encoder) if diagnostic else nullcontext()
    with probes, CanonicalEngine(root, encoder=encoder, auto_refresh=False) as engine:
        store = engine.character(character)
        warmup = requests[0].model_copy(update={"text": "Archive warmup probe", "limit": 1})
        _sample(store, warmup, allowed, diagnostic=False)
        phases = {
            phase: [_sample(store, query, allowed, diagnostic=diagnostic) for query in requests]
            for phase in ("distinct", "repeated")
        }
    # Engine close drains native stages that outlived a soft deadline.
    return {phase: _summary(samples) for phase, samples in phases.items()}


def _scenario(
    root: Path,
    args: Arguments,
    encoder: Encoder,
    sessions: list[Session],
    texts: list[str],
    libraries: int,
    percent: int,
) -> dict[str, object]:
    from benchmark_memory_synthetic import load_library

    allowed: set[ExactRef] = set()
    history = len(sessions)
    initial_grants = history * percent // 100
    with CanonicalEngine(root, encoder=encoder, auto_refresh=False) as engine:
        character = engine.create_character().store_id
        start = perf_counter_ns()
        library_ids: list[UUID] = []
        for i in range(libraries):
            library, _, docs = load_library(engine, sessions[i::libraries], f"scale-{i}")
            library_ids.append(library)
            # Grant the same prefix of the corpus regardless of how it is sharded.
            quota = len(range(i, initial_grants, libraries))
            _grant(engine, character, [doc.source for doc in docs[:quota]], allowed)
        import_ms = _elapsed(start)
        if len(allowed) != initial_grants:
            raise RuntimeError("Initial grants differ from requested exact revision count.")
        start = perf_counter_ns()
        builds = [engine.rebuild_index(library, encoder) for library in library_ids]
        build_ms = _elapsed(start)
        sizes = [engine.indexes[library].active()[1].stat().st_size for library in library_ids]

        library = library_ids[0]
        appended, append_ms = _append(engine, library, args.append_records)
        total = history + len(appended)
        queried_grants = total * percent // 100
        _grant(engine, character, appended[: queried_grants - initial_grants], allowed)
        if len(allowed) != queried_grants:
            raise RuntimeError("Committed grants differ from requested exact revision count.")
        start = perf_counter_ns()
        engine.update_index(library, encoder)
        refresh_ms = _elapsed(start)
        index = engine.indexes[library]
        refreshed, path = index.active()
        sequence_delta = refreshed.outbox_sequence - builds[0].outbox_sequence
        if sequence_delta <= 0 or index.source_pending(refreshed):
            raise RuntimeError("Active archive index did not catch up with the source outbox.")
        refreshed_size = path.stat().st_size

    scope = tuple(library_ids)
    requests = [ArchiveQuery(text=text, source_store_ids=scope, limit=_LIMIT) for text in texts]
    return {
        "libraries": libraries,
        "history_records": history,
        "grant_percent": percent,
        "initial_granted_records": initial_grants,
        "initial_grant_fraction": initial_grants / history,
        "queried_records": total,
        "queried_granted_records": len(allowed),
        "queried_grant_fraction": len(allowed) / total,
        "import_and_grant_ms": import_ms,
        "initial_index": {
            "ms": build_ms,
            "projections": sum(item.documents for item in builds),
            "vectors": sum(item.vectors for item in builds),
            "active_bytes": sum(sizes),
        },
        "append_refresh": {
            "records": len(appended),
            "append_ms": append_ms,
            "refresh_ms": refresh_ms,
            "previous_active_bytes": sizes[0],
            "active_bytes": refreshed_size,
            "outbox_sequence_delta": sequence_delta,
            "vector_delta": refreshed.vectors - builds[0].vectors,
        },
        "search": _search(root, encoder, character, requests, allowed),
        "profiled_search": (
            _search(root, encoder, character, requests, allowed, diagnostic=True)
            if args.profile
            else None
        ),
    }


def main() -> None:
    """Write one JSON report; discard each scratch root after its cell finishes."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--output", type=Path, required=True, help="new JSON file in existing dir")
    parser.add_argument("--encoder", type=Path, help="local CPU BGE assets; omit for smoke encoder")
    for option, help_text in (
        ("--libraries", "library counts; initial documents split round-robin"),
        ("--history-records", "initial document totals, at least 32"),
        ("--grant-percent", "exact record grant percentages, rounded down"),
    ):
        parser.add_argument(option, nargs="+", type=int, default=argparse.SUPPRESS, help=help_text)
    for option in ("--queries", "--append-records", "--seed"):
        parser.add_argument(option, type=int, default=argparse.SUPPRESS)
    parser.add_argument("--profile", action="store_true", help="run a separate diagnostic pair")
    try:
        args = Arguments.model_validate(vars(parser.parse_args()))
    except ValidationError as error:
        parser.error(str(error))
    # Search skips libraries without grants, so keep every requested shard in scope.
    if min(args.history_records) * min(args.grant_percent) // 100 < max(args.libraries):
        parser.error("Each cell must grant at least one exact revision per library.")
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("Output must be a new file in an existing directory.")

    # Keep help and validation usable without importing the synthetic scorer's agent stack.
    from benchmark_memory_synthetic import fixture

    encoder = BgeEncoder(args.encoder, threads=1) if args.encoder else _SmokeEncoder()
    # Keep query selection independent of the history-dependent fixture shuffle.
    _, cases = fixture(args.seed, _TOPICS, 0)
    cases = cases[: args.queries]
    texts = [case.query for case in cases]
    engine_source = Path(getfile(CanonicalEngine)).resolve().parent
    script = Path(__file__).resolve()
    scenarios: list[dict[str, object]] = []
    report = {
        "schema": "ember.archive-scale@1",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "sqlite_vec": version("sqlite-vec"),
        "engine_sha256": canonical_object_sha256(
            {
                path.relative_to(engine_source).as_posix(): file_sha256(path)
                for path in engine_source.rglob("*.py")
            }
        ),
        "benchmark_sha256": {
            name: file_sha256(script.with_name(name))
            for name in (
                script.name,
                "benchmark_memory_synthetic.py",
                "benchmark_memory_archive_profile.py",
            )
        },
        "encoder": encoder.specification.model_dump(mode="json"),
        "encoder_threads": 1 if args.encoder else None,
        "parameters": args.model_dump(mode="json", exclude={"output", "encoder"}),
        "archive_mode": "hybrid",
        "result_limit": _LIMIT,
        "query_ids": [case.id for case in cases],
        "notes": [
            "Short fictional documents; not relevance, conversation-graph or capacity evidence.",
            "Tied smoke vectors exercise KNN but do not model BGE cost or similarity.",
            "History is total documents; grant percentages floor to exact whole-record revisions.",
            "Append time is the canonical write only, excluding grant changes.",
            "Refresh copies and replays one library; copy cost scales with active index bytes.",
            "Distinct follows an unrelated warmup attempt, not a cold process or OS cache.",
            "Repeated uses the same requests in order and can reuse cached query vectors.",
            "Search timing is uninstrumented; profiling reopens the same snapshot separately.",
            "Latency includes partial responses and exceptions; p95 and p99 use nearest rank.",
            "Coverage counts response statuses and exception codes, not just successful results.",
            "Deadline/admission cases count engine-reported misses, separately from wall time.",
            "FTS and KNN timings include their SQL eligibility predicates.",
            "Stage samples count queries where the stage ran; rows returned, not rows scanned.",
            "Stage work can overlap or finish after the caller's soft deadline.",
        ],
        "scenarios": scenarios,
    }
    for history in args.history_records:
        sessions, _ = fixture(args.seed, _TOPICS, history - 4 * _TOPICS)
        Random(args.seed).shuffle(sessions)
        for libraries, percent in product(args.libraries, args.grant_percent):
            with TemporaryDirectory(prefix="archive-scale-", dir=args.output.parent) as scratch:
                root = Path(scratch).resolve()
                scenarios.append(
                    _scenario(root, args, encoder, sessions, texts, libraries, percent)
                )
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(f"{len(scenarios)} archive scale cells: {args.output}")


if __name__ == "__main__":
    main()
