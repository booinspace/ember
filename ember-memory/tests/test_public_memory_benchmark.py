"""Offline checks for AMB provenance, native grants, and label-free retrieval."""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from struct import pack
from typing import Literal
from unittest.mock import Mock

import pytest

from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ArchiveSearchResponse,
    RetrievalCoverage,
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
