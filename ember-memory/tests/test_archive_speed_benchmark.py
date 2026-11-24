"""Run opt-in stage probes against real SQLite and frozen synthetic exact grants."""

import json
import sqlite3
from collections.abc import Sequence
from functools import partial
from pathlib import Path
from struct import pack

import pytest

from ember_memory_engine.search_contracts import EmbeddingSpec


class FixtureEncoder:
    specification = EmbeddingSpec(
        model_revision="test",
        tokenizer_revision="test",
        model_sha256="a" * 64,
        tokenizer_sha256="b" * 64,
    )

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return ((0, len(text)),)

    def token_count(self, text: str) -> int:
        return int(bool(text))

    def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[bytes, ...]:
        return tuple(pack("<384f", 1.0, *([0.0] * 383)) for _ in texts)


def test_archive_speed_replays_identically_with_diagnostic_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts import benchmark_memory_archive_speed as speed

    monkeypatch.setattr(speed, "_synthetic", partial(speed._synthetic, topics=2, distractors=4))
    root = tmp_path / "fixture"
    encoder = FixtureEncoder()
    speed.prepare(root, encoder)
    manifest = speed.Fixture.model_validate_json((root / "fixture.json").read_bytes())
    assert len({ref[0] for grants in manifest.grants.values() for ref in grants}) == 2

    plain, diagnostic = tmp_path / "plain.json", tmp_path / "profile.json"
    connect = sqlite3.connect
    speed.run(root, encoder, plain, diagnostic=False)
    speed.run(root, encoder, diagnostic, diagnostic=True)
    assert sqlite3.connect is connect
    baseline = json.loads(plain.read_bytes())
    profiled = json.loads(diagnostic.read_bytes())
    for phase in ("distinct", "repeat"):
        control, measured = (report["phases"][phase] for report in (baseline, profiled))
        assert control["digests"] == measured["digests"]
        assert control["coverage"] == measured["coverage"] == {"complete": 11}
        assert control["stages"] == {}
        for name in (
            "archive_queue",
            "vector_queue",
            "fts_sql",
            "knn_sql",
            "scope_sql",
            "source_payload_sql",
            "projection_payload_sql",
            "evidence_validation",
        ):
            assert measured["stages"][name]["calls"] > 0
        assert measured["stages"]["projection_payload_sql"]["rows"] > 0
        assert measured["stages"]["source_payload_sql"]["rows"] > 0
    assert profiled["phases"]["distinct"]["stages"]["encoding"]["calls"] == 11
    assert "encoding" not in profiled["phases"]["repeat"]["stages"]
