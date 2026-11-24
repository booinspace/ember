"""Run public AMB retrieval offline. No LLM, judge, or production store.

Use pinned AMB checkout and PrecisionMemBench fixtures from docs/memory-benchmark.md.
Default archive lane uses local CPU BGE assets. PrecisionMemBench seeded-belief
and explicit-scope lanes use native structured identity lookup without an encoder.
Run from the Ember checkout.
"""

import argparse
import gzip
import json
import os
import platform
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from statistics import mean, median
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Literal, Protocol, cast

from pydantic import BaseModel, Field, TypeAdapter
from scripts.benchmark_memory_amb_archive import (
    ArchiveBenchmark,
    InputDocument,
    Retrieval,
    SearchMode,
)
from scripts.benchmark_memory_amb_beliefs import BeliefBenchmark, BeliefLane, load_seed

from ember_memory_abi.common import canonical_object_sha256
from ember_memory_engine import CanonicalEngine, identity_recall
from ember_memory_engine.embedding import BgeEncoder, file_sha256

type Lane = Literal["archive"] | BeliefLane
AMB_COMMIT = "01bd83f3fa5245d6be3986cfab81fdd828f26acd"
PMB_COMMIT = "b95d6abb471c0d591c440172283dd74e7af000df"
PMB_SCORER_FILE = "src/memory_bench/dataset/precisionmembench.py"
PMB_SCORER_SHA256 = "66eddb9548ec00d639379b28ebd59a3fb17518ef262bf883407a68304419c165"
DEFAULT_RESULT_LIMIT = 20
PMB_FILES = {
    "beliefs.seed.json": "35659b3b9a581b644fc6746e1b4e902c3e7b56c0973b546bf7b0816a69e3de98",
    "retrieval.cases.json": "09e99f0852280538abe34b03570f65583fdb10df3c554b7150f0f665d5db709c",
}
SPLITS = {
    "precisionmembench": "single-turn",
    "locomo": "locomo10",
    "longmemeval": "s",
    "personamem": "32k",
    "beam": "100k",
    "lifebench": "en",
}
# BEAM supplies conversation-level IDs (the isolation key), not evidence relevance.
# Published LifeBench export has no document labels. Neither gets an ID effectiveness score.
EVIDENCE_SPLITS = frozenset({"locomo", "longmemeval", "personamem"})
BEAM_SPLITS = ("100k", "500k", "1m", "10m")


class Params(BaseModel):
    """Validated local inputs and fixed retrieval policy."""

    amb: Path
    ember_root: Path
    encoder: Path | None
    output: Path
    fixtures: Path | None
    dataset: str
    split: str | None
    lane: Lane = "archive"
    mode: SearchMode = "lexical"
    limit: int = Field(ge=1, le=40)
    query_limit: int | None = Field(ge=1)


class PublicQuery(BaseModel):
    """Read only public normalized fields; do not pass scoring metadata to retrieval."""

    id: str
    query: str
    user_id: str | None
    gold_ids: list[str]
    meta: dict[str, object]

    @property
    def retrieval_text(self) -> str:
        """Use AMB's RAG retrieval prompt when supplied (e.g. no MCQ options)."""
        text = self.meta.get("retrieval_query")
        return text if isinstance(text, str) and text else self.query


class AmbDocumentFactory(Protocol):
    def __call__(self, *, id: str, content: str, source_ids: list[str]) -> InputDocument: ...


class AmbQuery(Protocol):
    id: str
    query: str
    user_id: str | None
    gold_ids: list[str]
    meta: dict[str, object]


class AmbPrecision(Protocol):
    def load_documents(self, split: str) -> list[InputDocument]: ...
    def load_queries(self, split: str) -> list[AmbQuery]: ...
    def score_retrieval(
        self, query: AmbQuery, retrieved: list[InputDocument]
    ) -> tuple[bool, str]: ...
    def summary_metrics(self, results: list[dict[str, object]]) -> dict[str, object]: ...


@dataclass(frozen=True)
class PrecisionScorer:
    """Pinned AMB scorer with exact ID provenance; no text-based ID inference."""

    dataset: AmbPrecision
    document: AmbDocumentFactory
    queries: dict[str, AmbQuery]

    def score(self, query_id: str, result: Retrieval) -> dict[str, object]:
        """Apply unchanged upstream assertions and preserve their public per-case evidence."""
        query = self.queries[query_id]
        documents = [self.document(id=item, content="", source_ids=[item]) for item in result.ids]
        correct, _ = self.dataset.score_retrieval(query, documents)
        fields = (
            "pass_type",
            "active_pass",
            "retrieval_precision",
            "retrieval_recall",
            "pinned_coverage",
            "failures",
            "resolution",
            "relevant_beliefs",
            "pinned_beliefs",
            "retrieved_questions",
        )
        return {"correct": correct, "meta": {key: query.meta.get(key) for key in fields}}


def _git(root: Path, *command: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *command],
        check=True,
        stdout=subprocess.PIPE,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""},
    ).stdout


def _read(root: Path, name: str) -> bytes:
    raw = _git(root, "show", f"{AMB_COMMIT}:{name}")
    return gzip.decompress(raw) if name.endswith(".gz") else raw


def _precision(amb: Path, fixtures: Path) -> PrecisionScorer:
    for name, digest in PMB_FILES.items():
        if file_sha256(fixtures / name) != digest:
            raise ValueError(f"PrecisionMemBench fixture checksum failed: {name}")
    # Git may check this text file out with CRLF on Windows.
    scorer_bytes = (amb / PMB_SCORER_FILE).read_bytes().replace(b"\r\n", b"\n")
    if sha256(scorer_bytes).hexdigest() != PMB_SCORER_SHA256:
        raise ValueError("PrecisionMemBench scorer checksum failed.")
    # Run the pinned source without importing AMB's hosted memory provider registry.
    # AMB's scorer has an optional cap switch; forbid it to preserve unmodified scoring.
    if os.environ.get("AMB_PMB_RETURN_CAP"):
        raise ValueError("Unset AMB_PMB_RETURN_CAP before an Ember benchmark.")
    os.environ["PRECISIONMEMBENCH_DATA_PATH"] = str(fixtures.absolute())
    sys.path.insert(0, str(amb / "src"))
    module = import_module("memory_bench.dataset.precisionmembench")
    models = import_module("memory_bench.models")
    dataset = cast(AmbPrecision, module.PrecisionMemBenchDataset())
    return PrecisionScorer(
        dataset,
        cast(AmbDocumentFactory, models.Document),
        {q.id: q for q in dataset.load_queries("single-turn")},
    )


def _inputs(
    params: Params, dataset: str, split: str
) -> tuple[list[InputDocument], list[PublicQuery], PrecisionScorer | None]:
    if dataset == "precisionmembench":
        if params.fixtures is None:
            raise ValueError("PrecisionMemBench requires --fixtures with both pinned JSON files.")
        scorer = _precision(params.amb, params.fixtures)
        docs = [
            InputDocument(d.id, d.content, d.user_id, d.timestamp)
            for d in scorer.dataset.load_documents(split)
        ]
        queries = [
            PublicQuery(id=q.id, query=q.query, user_id=q.user_id, gold_ids=q.gold_ids, meta=q.meta)
            for q in scorer.queries.values()
        ]
        return docs, queries, scorer
    prefix = f"data/{dataset}/{split}"
    docs = TypeAdapter(list[InputDocument]).validate_json(
        _read(params.amb, f"{prefix}/documents.json.gz")
    )
    queries = TypeAdapter(list[PublicQuery]).validate_json(
        _read(params.amb, f"{prefix}/queries.json.gz")
    )
    return docs, queries, None


def document_evidence(gold_ids: list[str], ids: tuple[str, ...]) -> dict[str, object]:
    """Score ranked document IDs for a query with nonempty published labels."""
    gold = set(gold_ids)
    first = next((rank for rank, item in enumerate(ids, 1) if item in gold), None)
    return {
        "gold_ids": sorted(gold),
        "hit": int(first is not None),
        "recall": len(gold.intersection(ids)) / len(gold),
        "reciprocal_rank": 0.0 if first is None else 1.0 / first,
    }


def request_budget(query: AmbQuery, precision: bool) -> int | None:
    """Extract only the public caller budget, never scorer expectations."""
    if not precision:
        return None
    value = query.meta.get("retrieval_limit")
    if type(value) is not int or value < 0:
        raise ValueError("Missing or invalid public PrecisionMemBench request budget.")
    return value


def request_scope(query: AmbQuery) -> tuple[str, ...]:
    """Read explicit caller scope (never inferred from case labels or text)."""
    return TypeAdapter(tuple[str, ...]).validate_python(query.meta.get("scope"))


def _run_beliefs(params: Params, split: str, lane: BeliefLane) -> dict[str, object]:
    if params.fixtures is None:
        raise ValueError("PrecisionMemBench requires --fixtures with both pinned JSON files.")
    scorer = _precision(params.amb, params.fixtures)
    seeds = load_seed(params.fixtures / "beliefs.seed.json")
    queries = tuple(scorer.queries.values())[: params.query_limit]
    retrievals: list[Retrieval] = []
    with (
        TemporaryDirectory(prefix=f"ember-amb-{lane}-", dir=params.output.parent) as root,
        CanonicalEngine(Path(root).resolve()) as engine,
    ):
        started = perf_counter()
        run = BeliefBenchmark(engine, seeds, lane, params.limit)
        ingest_ms = (perf_counter() - started) * 1000
        for query in queries:
            budget = request_budget(query, precision=True)
            assert budget is not None
            retrievals.append(
                run.retrieve(
                    query.query,
                    query.user_id,
                    budget,
                    scope=request_scope(query) if lane == "explicit-scope" else (),
                )
            )
    # Nothing read from the scorer's verdicts can affect a canonical request.
    cases = [
        {"query_id": query.id, **asdict(result), **scorer.score(query.id, result)}
        for query, result in zip(queries, retrievals, strict=True)
    ]
    return {
        "amb": AMB_COMMIT,
        "dataset": "precisionmembench",
        "split": split,
        "lane": lane,
        "queries": len(cases),
        "split_queries": len(scorer.queries),
        "ingested_documents": len(seeds),
        "split_documents": len(seeds),
        "source_setup": dict(run.setup),
        "ingest_and_seed_ms": ingest_ms,
        **_retrieval_metrics(retrievals),
        "cases": cases,
        **_precision_metrics(scorer, cases),
    }


def _run(params: Params, dataset: str, split: str, encoder: BgeEncoder) -> dict[str, object]:
    docs, all_queries, scorer = _inputs(params, dataset, split)
    queries = all_queries[: params.query_limit]
    by_unit: dict[str | None, list[PublicQuery]] = defaultdict(list)
    for query in queries:
        by_unit[query.user_id].append(query)
    doc_units: dict[str | None, list[InputDocument]] = defaultdict(list)
    for doc in docs:
        # Smoke limits queries, never documents within selected units. PrecisionMemBench
        # retains all units including the cross-user leak fixture, even for a smoke run.
        if scorer is not None or doc.user_id in by_unit:
            doc_units[doc.user_id].append(doc)
    inventory = {"amb": AMB_COMMIT, "dataset": dataset, "split": split}
    rows: dict[str, dict[str, object]] = {}
    evidence_scores: list[dict[str, object]] = []
    retrievals: list[Retrieval] = []
    ingested = index_documents = index_vectors = 0
    ingest_ms = 0.0

    units = tuple(dict.fromkeys((*doc_units, *by_unit)))
    # Keep PrecisionMemBench's cross-user fixture present during retrieval; larger splits
    # use disposable per-unit roots to bound disk and worker ownership.
    groups = (units,) if scorer is not None else ((user,) for user in units)
    for group in groups:
        with TemporaryDirectory(prefix="ember-amb-", dir=params.output.parent) as root:
            run = ArchiveBenchmark(Path(root).resolve(), encoder, params.mode, params.limit)
            try:
                for user in group:
                    if unit_docs := doc_units.get(user):
                        started = perf_counter()
                        run.add(
                            user, unit_docs, canonical_object_sha256({**inventory, "unit": user})
                        )
                        ingest_ms += (perf_counter() - started) * 1000
                        ingested += len(unit_docs)
                index_documents += run.index_documents
                index_vectors += run.index_vectors
                for user in group:
                    for query in by_unit.get(user, []):
                        result = run.retrieve(
                            query.retrieval_text, user, request_budget(query, scorer is not None)
                        )
                        retrievals.append(result)
                        row: dict[str, object] = {"query_id": query.id, **asdict(result)}
                        if scorer is not None:
                            row.update(scorer.score(query.id, result))
                        elif dataset in EVIDENCE_SPLITS and query.gold_ids:
                            scored = document_evidence(query.gold_ids, result.ids)
                            evidence_scores.append(scored)
                            row.update(scored)
                        rows[query.id] = row
            finally:
                run.close()
    ordered = [rows[query.id] for query in queries]
    report: dict[str, object] = {
        **inventory,
        "lane": "archive",
        "queries": len(queries),
        "split_queries": len(all_queries),
        "ingested_documents": ingested,
        "split_documents": len(docs),
        "indexed_projections": index_documents,
        "indexed_vectors": index_vectors,
        "ingest_and_index_ms": ingest_ms,
        **_retrieval_metrics(retrievals),
        "cases": ordered,
    }
    if scorer is not None:
        report.update(_precision_metrics(scorer, ordered))
    elif evidence_scores:
        report.update(
            metric="diagnostic_document_ids",
            evidence_labeled_queries=len(evidence_scores),
            **{
                f"mean_{key}": mean(cast(float, row[key]) for row in evidence_scores)
                for key in ("hit", "recall", "reciprocal_rank")
            },
        )
    else:
        report.update(metric="retrieval_execution_only", score=None)
    return report


def _retrieval_metrics(retrievals: list[Retrieval]) -> dict[str, object]:
    latencies = sorted(r.latency_ms for r in retrievals if r.coverage not in {"skipped", "no_unit"})
    return {
        "nonempty_queries": sum(bool(r.ids) for r in retrievals),
        "coverage": dict(Counter(r.coverage for r in retrievals)),
        "coverage_reasons": dict(Counter(reason for r in retrievals for reason in r.reasons)),
        "retrieval_latency_ms": {
            "samples": len(latencies),
            "p50": median(latencies) if latencies else None,
            "p95": latencies[(len(latencies) * 95 + 99) // 100 - 1] if latencies else None,
        },
    }


def _precision_metrics(
    scorer: PrecisionScorer, cases: list[dict[str, object]]
) -> dict[str, object]:
    correct = sum(row["correct"] is True for row in cases)
    return {
        "metric": "amb_precisionmembench_case_pass",
        "score": correct / len(cases),
        "correct": correct,
        **scorer.dataset.summary_metrics(cases),
    }


class PublishedRun(BaseModel):
    """Published AMB baseline metadata, never passed to Ember."""

    path: str
    dataset: str
    split: str
    run_name: str
    memory: str
    mode: str
    accuracy: float
    total_queries: int
    correct: int
    active_passes: int | None = None
    active_total: int | None = None
    mean_precision: float | None = None
    mean_recall: float | None = None
    description: str | None = None


def _baselines(
    params: Params, dataset: str, split: str, result: dict[str, object]
) -> list[dict[str, object]]:
    manifest = TypeAdapter(list[PublishedRun]).validate_json(
        _read(params.amb, "results-manifest.json")
    )
    baselines: list[dict[str, object]] = []
    for baseline in manifest:
        if (baseline.dataset, baseline.split) != (dataset, split):
            continue
        row = cast(dict[str, object], baseline.model_dump())
        row["comparison"] = "not_comparable_answer_generation_or_judging"
        if dataset == "precisionmembench" and baseline.mode == "retrieval":
            cases = cast(list[dict[str, object]], result["cases"])
            raw = json.loads(_read(params.amb, baseline.path + ".gz"))
            scores = cast(
                dict[str, bool], {case["query_id"]: case["correct"] for case in raw["results"]}
            )
            if all(case["query_id"] in scores for case in cases):
                count = sum(scores[cast(str, case["query_id"])] for case in cases)
                row.update(
                    comparison="matched_case_ids_same_amb_scorer_different_provider_features",
                    matched_queries=len(cases),
                    matched_baseline_correct=count,
                    matched_baseline_score=count / len(cases),
                    ember_minus_baseline_score=(cast(int, result["correct"]) - count) / len(cases),
                    description=raw.get("description"),
                )
            else:
                row["comparison"] = "not_comparable_case_ids_differ"
        baselines.append(row)
    return baselines


def main() -> int:
    """Write a new per-split result with pinned public inputs and no source/query text."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amb", type=Path, required=True, help=f"AMB checkout at {AMB_COMMIT}")
    parser.add_argument(
        "--ember-root", type=Path, default=Path.cwd(), help="Ember checkout for version receipt"
    )
    parser.add_argument("--encoder", type=Path, help="local pinned CPU BGE assets (archive only)")
    parser.add_argument(
        "--output", type=Path, required=True, help="new JSON path in existing directory"
    )
    parser.add_argument("--dataset", choices=[*SPLITS, "all"], required=True)
    parser.add_argument("--split", choices=BEAM_SPLITS, help="override the default BEAM 100k tier")
    parser.add_argument(
        "--fixtures", type=Path, help=f"local {', '.join(PMB_FILES)} at {PMB_COMMIT}"
    )
    parser.add_argument("--query-limit", type=int, help="deterministic first N queries per split")
    parser.add_argument(
        "--lane",
        choices=["archive", "seeded-belief", "explicit-scope"],
        default="archive",
        help="belief lanes require --dataset precisionmembench",
    )
    parser.add_argument(
        "--mode", choices=["lexical", "hybrid"], help="archive mode (default lexical)"
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_RESULT_LIMIT, help="archive result cap, max 40"
    )
    args = parser.parse_args()
    if args.lane == "archive":
        if args.encoder is None:
            parser.error("archive lane requires --encoder")
    else:
        if args.dataset != "precisionmembench":
            parser.error("belief lanes require --dataset precisionmembench")
        if args.mode is not None or args.encoder is not None:
            parser.error("--mode and --encoder are archive options")
        if args.limit != DEFAULT_RESULT_LIMIT:
            parser.error("belief lanes use the frozen native result cap of 20")
    args.mode = args.mode or "lexical"
    params = Params.model_validate(vars(args))
    if params.split is not None and params.dataset != "beam":
        parser.error("--split is supported only with --dataset beam")
    if _git(params.amb, "rev-parse", "HEAD").decode().strip() != AMB_COMMIT:
        parser.error(f"AMB checkout must be {AMB_COMMIT}")
    if params.dataset in {"precisionmembench", "all"}:
        dirty = _git(params.amb, "status", "--porcelain", "--untracked-files=all", "--", "src")
        if dirty:
            parser.error("AMB src must be unchanged from pinned commit")
    if params.output.exists() or not params.output.parent.is_dir():
        parser.error("--output must be a new file in an existing directory")
    encoder = BgeEncoder(params.encoder) if params.encoder is not None else None
    output: dict[str, object] = {
        "schema": "ember.public-amb@0.1.0",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "ember_commit": _git(params.ember_root, "rev-parse", "HEAD").decode().strip(),
        "launcher_sha256": file_sha256(Path(__file__)),
        "lane": params.lane,
        "result_limit": params.limit,
        "query_limit": params.query_limit,
        "encoder": encoder.specification.model_dump(mode="json") if encoder is not None else None,
        "answer_llm": None,
        "judge_llm": None,
        "precision_fixture_commit": PMB_COMMIT,
    }
    if params.dataset in {"precisionmembench", "all"}:
        output.update(precision_fixture_sha256=PMB_FILES, precision_scorer_sha256=PMB_SCORER_SHA256)
    if params.lane == "archive":
        output.update(
            archive_adapter_sha256=file_sha256(
                Path(__file__).with_name("benchmark_memory_amb_archive.py")
            ),
            mode=params.mode,
            archive_ranking="default",
            retrieval="native_granted_document_archive",
            ranking_unit="native_hits_mapped_to_distinct_amb_document_ids",
            temporal_policy="preserve_document_timestamps_no_query_time_filter",
        )
    else:
        output.update(
            belief_adapter_sha256=file_sha256(
                Path(__file__).with_name("benchmark_memory_amb_beliefs.py")
            ),
            identity_recall_sha256=file_sha256(Path(identity_recall.__file__)),
            mode="structured",
            retrieval="native_approved_seeded_belief_identity",
            ranking_unit="native_accepted_ids_crosswalked_by_exact_source_evidence",
            temporal_policy="current_guidance_only_no_validity_bounds_inferred",
            scope_policy="explicit_caller_tags_within_user"
            if params.lane == "explicit-scope"
            else "user_only",
        )
    splits = (
        SPLITS
        if params.dataset == "all"
        else {params.dataset: params.split or SPLITS[params.dataset]}
    )
    results: list[dict[str, object]] = []
    for dataset, split in splits.items():
        if params.lane == "archive":
            assert encoder is not None
            result = _run(params, dataset, split, encoder)
        else:
            result = _run_beliefs(params, split, params.lane)
        result["published_runs"] = _baselines(params, dataset, split, result)
        results.append(result)
        print(
            f"{dataset}/{split} [{params.lane}]: {result['queries']} queries, {result['metric']}",
            flush=True,
        )
    output["splits"] = results
    with params.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(output, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
