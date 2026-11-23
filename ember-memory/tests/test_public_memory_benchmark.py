"""Offline checks for AMB provenance, native grants, and label-free retrieval."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from struct import pack
from typing import Literal
from unittest.mock import Mock
from uuid import UUID

import pytest

from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ArchiveSearchResponse,
    MemoryQuery,
    RetrievalCoverage,
    SearchResponse,
)
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.search_contracts import EmbeddingSpec


class TestEncoder:
    """Deterministic index construction only; not a benchmark embedding model."""

    specification = EmbeddingSpec(
        model_revision="test",
        tokenizer_revision="test",
        model_sha256="a" * 64,
        tokenizer_sha256="b" * 64,
    )

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Treat short fixture text as one token."""
        return ((0, len(text)),)

    def token_count(self, text: str) -> int:
        """Keep the short fixture inside one native chunk."""
        return 1 if text else 0

    def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[bytes, ...]:
        """Build a legal unit vector per text."""
        return tuple(pack("<384f", 1.0, *([0.0] * 383)) for _ in texts)


@pytest.fixture(autouse=True)
def _benchmark_import_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "path", [str(Path(__file__).resolve().parents[2]), *sys.path])


def test_archive_scope_and_origin(tmp_path: Path) -> None:
    """Exact chunk provenance and native grants isolate equally matching archive sources."""
    from scripts.benchmark_memory_amb_archive import ArchiveBenchmark, InputDocument

    run = ArchiveBenchmark(tmp_path.resolve(), TestEncoder(), "lexical", 20)
    try:
        run.add(
            "alice",
            [
                InputDocument("local", "Practical otter sightings.", "alice"),
                InputDocument("local", "Separate squirrel sightings.", "alice"),
            ],
            "a" * 64,
        )
        run.add("bob", [InputDocument("foreign", "Other otter sightings.", "bob")], "b" * 64)
        assert len(run.units["alice"][1]) == 2
        assert run.retrieve("otter", "alice").ids == ("local",)
        assert run.retrieve("squirrel", "alice").ids == ("local",)
        assert run.retrieve("sightings", "alice").ids == ("local",)
        assert run.retrieve("otter", "bob").ids == ("foreign",)
        assert run.retrieve("otter", "unregistered").ids == ()
        assert run.retrieve("otter", "alice", 0).coverage == "skipped"
        bob_library = run.units["bob"][0]
        alice = run.engine.character(run.units["alice"][2])
        assert not alice.search_archive(
            ArchiveQuery(text="otter", mode="lexical", source_store_ids=(bob_library,))
        ).results
    finally:
        run.close()


def test_labels_do_not_enter_request() -> None:
    """Changing answers and expected IDs affects scoring only, not query text or budget."""
    from scripts.benchmark_memory_amb import PublicQuery, document_evidence, request_budget

    request = PublicQuery(
        id="case",
        query="Prompt with options",
        user_id="alice",
        gold_ids=["later"],
        meta={"retrieval_query": "otter", "retrieval_limit": 5, "expect": {"mustInclude": "later"}},
    )
    changed = request.model_copy(update={"gold_ids": ["first"]})
    assert request.retrieval_text == changed.retrieval_text == "otter"
    assert request_budget(request, precision=True) == request_budget(changed, precision=True) == 5
    assert document_evidence(request.gold_ids, ("first", "later"))["reciprocal_rank"] == 0.5
    assert document_evidence(changed.gold_ids, ("first", "later"))["reciprocal_rank"] == 1.0


@pytest.mark.parametrize("ranking", ["default", "precision_v1"])
def test_archive_ranking_receipt_and_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ranking: Literal["default", "precision_v1"]
) -> None:
    """Recorded ranking matches the native request; defaults omit request options."""
    from scripts import benchmark_memory_amb as amb
    from scripts.benchmark_memory_amb import PublicQuery
    from scripts.benchmark_memory_amb_archive import InputDocument

    output = tmp_path / "result.json"
    argv = [
        "benchmark_memory_amb",
        "--amb",
        str(tmp_path),
        "--encoder",
        str(tmp_path),
        "--output",
        str(output),
        "--dataset",
        "locomo",
        "--mode",
        "hybrid",
    ]
    if ranking != "default":
        argv.extend(("--archive-ranking", ranking))
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(amb, "_git", Mock(return_value=amb.AMB_COMMIT.encode()))
    monkeypatch.setattr(amb, "BgeEncoder", Mock(return_value=TestEncoder()))
    monkeypatch.setattr(amb, "_baselines", Mock(return_value=[]))
    monkeypatch.setattr(
        amb,
        "_inputs",
        Mock(
            return_value=(
                [InputDocument("local", "Practical otter sightings.", "alice")],
                [PublicQuery(id="one", query="otter", user_id="alice", gold_ids=[], meta={})],
                None,
            )
        ),
    )
    requests: list[ArchiveQuery] = []

    def capture(_store: SqliteMemoryStore, query: ArchiveQuery) -> ArchiveSearchResponse:
        requests.append(query)
        return ArchiveSearchResponse(
            results=(), coverage=RetrievalCoverage(status="complete", index_status="available")
        )

    monkeypatch.setattr(SqliteMemoryStore, "search_archive", capture)
    assert amb.main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["archive_ranking"] == ranking
    (request,) = requests
    assert request.mode == "hybrid"
    if ranking == "default":
        assert "options" not in request.model_fields_set
        assert request.options.extensions == {}
    else:
        assert request.options.extensions == {"ember.archive_ranking": "precision_v1"}


def test_precision_ranking_rejects_lexical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject the opt-in before loading files or encoder for the default lexical mode."""
    from scripts import benchmark_memory_amb as amb

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_memory_amb",
            "--amb",
            str(tmp_path),
            "--encoder",
            str(tmp_path),
            "--output",
            str(tmp_path / "result.json"),
            "--dataset",
            "locomo",
            "--archive-ranking",
            "precision_v1",
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        amb.main()
    assert "--archive-ranking precision_v1 requires --mode hybrid" in capsys.readouterr().err


@pytest.mark.parametrize("lane", ["seeded-belief", "explicit-scope"])
def test_seeded_belief_crosswalk_and_scope(
    tmp_path: Path, lane: Literal["seeded-belief", "explicit-scope"]
) -> None:
    """Identical evidence/alias never combines source IDs or crosses user grants."""
    from scripts.benchmark_memory_amb_beliefs import BeliefBenchmark, load_seed

    from ember_memory_engine import CanonicalEngine

    seed = tmp_path / "beliefs.seed.json"
    text = "Keep Café notes — separately."
    seed.write_text(
        json.dumps(
            [
                {
                    "_id": belief_id,
                    "user_id": user,
                    "type": "preference",
                    "epistemic_status": "active",
                    "canonical_name": "notebook",
                    "aliases": ["moonbeam"],
                    "content": text,
                    "scope": scope,
                    "why_it_matters": "This rationale is not source evidence.",
                }
                for belief_id, user, scope in (
                    ("work", "alice", ["work", "shared"]),
                    ("home", "alice", ["home", "shared"]),
                    ("foreign", "bob", ["work", "foreign-only"]),
                )
            ]
        ),
        encoding="utf-8",
    )
    with CanonicalEngine(tmp_path.resolve() / "store") as engine:
        run = BeliefBenchmark(engine, load_seed(seed), lane, 20)
        unit = run.units["alice"]
        assert set(unit.accepted.values()) == {"work", "home"}
        for native_id in unit.accepted:
            memory = unit.store.get(native_id)
            assert memory.origin is not None and memory.origin.origin_memory_id != native_id
            (source,) = unit.store.get_sources(native_id)
            assert source.quote == text
            assert source.evidence.source.source_store_id == memory.origin.origin_source_store_id
        # Both users have identically named memories; tags can only narrow within one user.
        assert set(run.retrieve("moonbeam", "alice", 20).ids) == {"work", "home"}
        assert set(run.retrieve("moonbeam", "alice", 20, scope=("shared",)).ids) == {"work", "home"}
        expected: set[str] = {"work"} if lane == "explicit-scope" else {"work", "home"}
        assert set(run.retrieve("moonbeam", "alice", 20, scope=("work",)).ids) == expected
        expected = set() if lane == "explicit-scope" else {"work", "home"}
        assert set(run.retrieve("moonbeam", "alice", 20, scope=("foreign-only",)).ids) == expected
        assert run.retrieve("moonbeam", "bob", 20, scope=("work",)).ids == ("foreign",)
        assert len(run.retrieve("moonbeam", "alice", 1).ids) == 1
        assert run.retrieve("moonbeam", "alice", 0).coverage == "skipped"
        assert run.retrieve("", "alice", 20).coverage == "skipped"
        assert run.retrieve("moonbeam", "nobody", 20).coverage == "no_unit"
        assert not unit.store.search_archive(ArchiveQuery(text="Café", mode="lexical")).results


def test_belief_status_preserves_history_without_inventing_dates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolved/superseded stay in source approvals; dates don't turn into validity."""
    from scripts.benchmark_memory_amb_beliefs import BeliefBenchmark, load_seed

    from ember_memory_abi.memory import ApprovedKnowledge, EpisodePayload
    from ember_memory_engine import CanonicalEngine
    from ember_memory_engine.library import ApprovalRequest, LibraryService

    seed = tmp_path / "beliefs.seed.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "_id": belief_id,
                    "user_id": "alice",
                    "type": kind,
                    "epistemic_status": status,
                    "canonical_name": "notebook",
                    "aliases": ["moonbeam"],
                    "content": f"Exact source for {belief_id}.",
                    "created_at": created_at,
                    **state,
                }
                for belief_id, kind, status, created_at, state in (
                    ("older-current", "decision", "active", "2010-01-01T00:00:00Z", {}),
                    ("newer-current", "decision", "active", "2020-01-01T00:00:00Z", {}),
                    ("archived", "decision", "superseded", None, {"superseded_by": "no-target"}),
                    (
                        "closed",
                        "open_question",
                        "active",
                        None,
                        {"resolved_at": "2020-01-01T00:00:00Z"},
                    ),
                    ("open", "open_question", "exploratory", None, {}),
                    ("unsupported", "decision", "unknown", "unknown", {}),
                )
            ]
        ),
        encoding="utf-8",
    )
    approvals: list[ApprovedKnowledge] = []
    native_approve = LibraryService.approve

    def capture(
        service: LibraryService, library: UUID, request: ApprovalRequest
    ) -> ApprovedKnowledge:
        approved = native_approve(service, library, request)
        approvals.append(approved)
        return approved

    monkeypatch.setattr(LibraryService, "approve", capture)
    with CanonicalEngine(tmp_path.resolve() / "store") as engine:
        run = BeliefBenchmark(engine, load_seed(seed), "seeded-belief", 20)
        assert run.setup == {"approved": 5, "seeded": 3, "history_only": 2, "source_only": 1}
        assert set(run.retrieve("moonbeam", "alice", 20).ids) == {"older-current", "newer-current"}
        assert {item.draft.content.modality for item in approvals} == {
            "PAST_STATE",
            "UNCERTAINTY",
            "FACTUAL_ASSERTION",
        }
        for item in approvals:
            temporal = item.draft.temporal
            assert temporal.observed_at is temporal.valid_from is temporal.valid_until is None
            (assertion,) = item.draft.content.evidence
            payload = item.draft.content.payload
            assert isinstance(payload, EpisodePayload)
            assert engine.validate(assertion.evidence) == payload.event


@pytest.mark.parametrize("lane", ["seeded-belief", "explicit-scope"])
def test_belief_manifest_and_scoring_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
) -> None:
    """Changing scorer-only fields changes assertions, not ingestion or request fields."""
    from scripts import benchmark_memory_amb as amb
    from scripts.benchmark_memory_amb_archive import InputDocument
    from scripts.benchmark_memory_amb_beliefs import CURRENT_MODALITIES

    seed = tmp_path / "beliefs.seed.json"
    seed.write_text(
        json.dumps(
            [
                {
                    "_id": "local",
                    "user_id": "alice",
                    "type": "entity",
                    "epistemic_status": "active",
                    "canonical_name": "notebook",
                    "aliases": ["moonbeam"],
                    "content": "An ordinary fact.",
                    "scope": ["work"],
                }
            ]
        ),
        encoding="utf-8",
    )
    queries: dict[str, amb.AmbQuery] = {
        case_id: amb.PublicQuery(
            id=case_id,
            query="moonbeam",
            user_id="alice",
            gold_ids=[gold],
            meta={
                "retrieval_limit": 5,
                "scope": ["work"],
                "expect": {"required": gold},
                "retrieval_query": f"not the caller text {case_id}",
                "category": case_id,
                "description": gold,
                "resolution": case_id,
            },
        )
        for case_id, gold in (("one", "local"), ("two", "wrong"))
    }
    requests: list[MemoryQuery] = []
    native_search = SqliteMemoryStore.search

    def capture(store: SqliteMemoryStore, request: MemoryQuery) -> SearchResponse:
        requests.append(request)
        return native_search(store, request)

    def score(query: amb.AmbQuery, docs: list[InputDocument]) -> tuple[bool, str]:
        assert len(requests) == len(queries)  # Scoring cannot affect a later request.
        assert [doc.id for doc in docs] == ["local"]
        return query.gold_ids == ["local"], "stub verdict"

    def document(*, id: str, content: str, source_ids: list[str]) -> InputDocument:
        assert source_ids == [id]
        return InputDocument(id=id, content=content)

    scorer = amb.PrecisionScorer(
        Mock(score_retrieval=score, summary_metrics=Mock(return_value={})), document, queries
    )
    monkeypatch.setattr(SqliteMemoryStore, "search", capture)
    monkeypatch.setattr(
        amb, "_git", Mock(side_effect=[amb.AMB_COMMIT.encode(), b"", b"ember-test"])
    )
    monkeypatch.setattr(amb, "_precision", Mock(return_value=scorer))
    monkeypatch.setattr(amb, "_baselines", Mock(return_value=[]))
    monkeypatch.setattr(amb, "BgeEncoder", Mock(side_effect=AssertionError("belief lane encoder")))
    output = tmp_path / "result.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_memory_amb",
            "--amb",
            str(tmp_path),
            "--fixtures",
            str(tmp_path),
            "--dataset",
            "precisionmembench",
            "--lane",
            lane,
            "--output",
            str(output),
        ],
    )
    assert amb.main() == 0
    first, second = requests
    assert first == second and first.text == "moonbeam" and first.limit == 5
    assert first.mode == "structured" and first.modalities == CURRENT_MODALITIES
    identity_options = first.options.extensions["ember.identity_recall_v1"]
    if lane == "seeded-belief":
        assert identity_options == {}
    else:
        assert isinstance(identity_options, dict)
        origins = identity_options["origin_store_ids"]
        assert isinstance(origins, list) and len(origins) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["lane"] == lane and report["mode"] == "structured"
    assert report["encoder"] is None and report["result_limit"] == 20
    assert report["precision_fixture_sha256"] == amb.PMB_FILES
    assert report["precision_scorer_sha256"] == amb.PMB_SCORER_SHA256
    (split,) = report["splits"]
    assert split["correct"] == 1 and split["queries"] == 2
    assert [case["ids"] for case in split["cases"]] == [["local"], ["local"]]
