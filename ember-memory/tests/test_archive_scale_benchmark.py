"""Smoke archive scale reporting against real FTS and sqlite-vec with no model assets."""

import subprocess
import sys
from itertools import product
from pathlib import Path

import pytest
from pydantic import TypeAdapter


@pytest.mark.end_to_end
def test_archive_scale_measures_append_and_search(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts/benchmark_memory_archive_scale.py"
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--output",
            str(output),
            "--libraries",
            "1",
            "2",
            "--history-records",
            "32",
            "35",
            "--grant-percent",
            "25",
            "100",
            "--queries",
            "2",
            "--append-records",
            "2",
            "--profile",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.iterdir()) == [output]

    report = TypeAdapter(dict[str, object]).validate_json(output.read_bytes())
    assert report["schema"] == "ember.archive-scale@1"
    cells = TypeAdapter(list[dict[str, object]]).validate_python(report["scenarios"])
    axes = product((32, 35), (1, 2), (25, 100))
    numeric = TypeAdapter(dict[str, int | float])
    phases = TypeAdapter(dict[str, dict[str, object]])
    stages = TypeAdapter(dict[str, dict[str, int | float]])
    for (history, libraries, percent), cell in zip(axes, cells, strict=True):
        assert (cell["history_records"], cell["libraries"], cell["grant_percent"]) == (
            history,
            libraries,
            percent,
        )
        initial_grants = history * percent // 100
        assert cell["initial_granted_records"] == initial_grants
        assert cell["initial_grant_fraction"] == pytest.approx(initial_grants / history)
        queried = history + 2
        granted = queried * percent // 100
        assert cell["queried_records"] == queried
        assert cell["queried_granted_records"] == granted
        assert cell["queried_grant_fraction"] == pytest.approx(granted / queried)
        assert numeric.validate_python(cell["initial_index"])["vectors"] == history
        append = numeric.validate_python(cell["append_refresh"])
        assert append["records"] == append["vector_delta"] == 2
        assert append["outbox_sequence_delta"] > 0

        primary = phases.validate_python(cell["search"])
        profiled = phases.validate_python(cell["profiled_search"])
        for phase in ("distinct", "repeated"):
            baseline, diagnostic = primary[phase], profiled[phase]
            assert baseline["samples"] == diagnostic["samples"] == 2
            assert baseline["coverage"] == diagnostic["coverage"] == {"complete": 2}
            assert (
                baseline["deadline_or_admission_cases"]
                == diagnostic["deadline_or_admission_cases"]
                == 0
            )
            assert baseline["stages"] == {}
            metrics = stages.validate_python(diagnostic["stages"])
            for name in ("fts_sql", "knn_sql", "scope_sql", "evidence_validation"):
                assert metrics[name]["calls"] > 0
            for name in ("projection_payload_sql", "source_payload_sql"):
                assert metrics[name]["rows"] > 0
            if phase == "distinct":
                assert metrics["encoding"]["calls"] == 2
            else:
                assert "encoding" not in metrics
