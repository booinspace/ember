"""Measure warm owned retrieval with aggregate-only output; no private result content."""

import argparse
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import UUID

from pydantic import Field

from ember_memory_abi.common import (
    CanonicalId,
    ContractModel,
    timestamp_from_datetime,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.interfaces import ArchiveQuery, MemoryQuery
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.index_storage import connect
from ember_memory_engine.indexed_retrieval import fts_expression, projected_memory_scope


class Arguments(ContractModel):
    """Narrow command-line values before any corpus/model access."""

    root: Path
    character: CanonicalId
    encoder: Path
    samples: int = Field(default=40, ge=20, le=1000)
    output: Path | None = None


class Measurement(ContractModel):
    """Wall time includes facade, SQLite and final canonical checks; p95 uses nearest rank."""

    samples: int = Field(ge=20)
    median_ms: float
    p95_ms: float


class BenchmarkReport(ContractModel):
    """Safe aggregate evidence for local capacity assessment, not a general performance claim."""

    version: str = "ember.search-benchmark@0.1.0"
    canonical_startup_ms: float
    source_records: int
    memory_revisions: int
    semantic_profile: str
    measurements: dict[str, Measurement]
    partial_responses: int


def measure(operation: Callable[[], object], samples: int) -> Measurement:
    """Warm five operations, then include every successful sample without outlier trimming."""
    for _ in range(5):
        operation()
    elapsed: list[float] = []
    for _ in range(samples):
        start = perf_counter()
        operation()
        elapsed.append((perf_counter() - start) * 1000)
    elapsed.sort()
    return Measurement(
        samples=samples, median_ms=median(elapsed), p95_ms=elapsed[(samples * 95 + 99) // 100 - 1]
    )


def main() -> int:
    """Use an existing offline-owned character; optionally write a new aggregate report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--character", type=UUID, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--output", type=Path)
    args = Arguments.model_validate(vars(parser.parse_args()))
    encoder = BgeEncoder(args.encoder)
    started = perf_counter()
    with CanonicalEngine(args.root, encoder=encoder) as engine:
        startup = (perf_counter() - started) * 1000
        identity: CanonicalId = args.character
        store = engine.character(identity)
        counts = store.database.read(
            lambda c: (
                c.execute("SELECT COUNT(*) FROM source_records").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0],
            )
        )
        if not all(isinstance(value, int) for value in counts):
            raise ValueError("Invalid canonical counts.")
        partial = 0

        def query(mode: str) -> object:
            nonlocal partial
            request = MemoryQuery.model_validate({"text": "graphics processor", "mode": mode})
            response = store.search(request)
            if response.coverage.status != "complete":
                partial += 1
            return response

        measurements = {
            "structured": measure(
                lambda: store.search(MemoryQuery(mode="structured")), args.samples
            ),
            "lexical": measure(lambda: query("lexical"), args.samples),
            "semantic_including_cached_encoding": measure(lambda: query("semantic"), args.samples),
            "hybrid": measure(lambda: query("hybrid"), args.samples),
            "deep_archive": measure(
                lambda: store.search_archive(ArchiveQuery(text="graphics processor")), args.samples
            ),
        }
        # Maintenance-only stage timings use the same compiled, current scope as
        # retrieval. Keep them separate from facade/fusion/evidence timings.
        generation, path = engine.indexes[identity].active()
        engine.indexes[identity].certified(generation, path)
        if generation.layout != "ember.search-layout@0.4.0" or generation.header_projection is None:
            raise ValueError("Stage measurements require a current certified index layout.")
        current = store.database.read(
            lambda c: c.execute("SELECT COALESCE(MAX(sequence),0) FROM index_outbox").fetchone()[0]
        )
        if current != generation.outbox_sequence:
            raise ValueError("Stage measurements require an up-to-date generation.")
        query_vector = encoder.encode(("graphics processor",), query=True)[0]
        scope, values = projected_memory_scope(store.state, MemoryQuery(text="graphics processor"))
        now = timestamp_to_unix_microseconds(timestamp_from_datetime(engine.clock.now()))
        with closing(connect(path, immutable=True)) as connection:
            connection.execute("PRAGMA cache_size=-196608")
            measurements["fts_scoped_candidates"] = measure(
                lambda: connection.execute(
                    "SELECT d.id FROM document_fts CROSS JOIN documents d "
                    "ON d.id=document_fts.rowid WHERE document_fts MATCH ? AND "
                    + scope
                    + " ORDER BY document_fts.rank LIMIT 40",
                    (fts_expression("graphics processor"), *values),
                ).fetchall(),
                args.samples,
            )
            measurements["semantic_scoped_scan_excluding_encoding"] = measure(
                lambda: connection.execute(
                    "SELECT document_id FROM vec_documents WHERE embedding MATCH ? "
                    "AND k=40 AND active=1 AND valid_from<=? AND valid_until>? ORDER BY distance",
                    (query_vector, now, now),
                ).fetchall(),
                args.samples,
            )
        recent = store.search(MemoryQuery(limit=1))
        if recent.results:
            memory_id = recent.results[0].memory.memory_id
            measurements["id"] = measure(lambda: store.get(memory_id), args.samples)
            measurements["provenance"] = measure(lambda: store.get_sources(memory_id), args.samples)
        report = BenchmarkReport(
            canonical_startup_ms=startup,
            source_records=counts[0],
            memory_revisions=counts[1],
            semantic_profile=encoder.specification.encoder_version,
            measurements=measurements,
            partial_responses=partial,
        )
    payload = report.model_dump_json(indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
