"""The suite retains failed reports and never overwrites an earlier hardware run."""

import json
import runpy
import sys
from pathlib import Path

import pytest

from ember_agent.inference import benchmark
from ember_agent.runtime import config


@pytest.mark.parametrize("fail", [False, True])
def test_suite_cases_preserve_failure_reports_and_reject_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    output = tmp_path / "reports"
    calls = []

    async def measured(settings: object, path: Path, **kwargs: object) -> None:
        calls.append((path.stem, kwargs))
        path.write_text(json.dumps({"outcome": "failed" if fail else "complete"}))
        if fail:
            raise RuntimeError("Synthetic backend failure")

    monkeypatch.setattr(benchmark, "run", measured)
    monkeypatch.setattr(config, "load_config", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "suite",
            "--config",
            "synthetic.toml",
            "--output-directory",
            str(output),
            "--samples",
            "2",
            "--voice-samples",
            "3",
        ],
    )
    script = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_gateway_suite.py"
    if fail:
        with pytest.raises(RuntimeError, match="Synthetic backend failure"):
            runpy.run_path(str(script), run_name="__main__")
        assert len(calls) == 1
        assert json.loads((output / "p0-short.json").read_text()) == {"outcome": "failed"}
    else:
        runpy.run_path(str(script), run_name="__main__")
        assert [label for label, _ in calls] == [
            "p0-short",
            "p0-medium",
            "p0-long",
            "p2-queued",
            "p3-queued",
            "p4-queued",
            "voice-short",
            "voice-p2-queued",
            "voice-p3-queued",
            "voice-p4-queued",
        ]
        assert [options["samples"] for _, options in calls] == [2, 2, 2, 2, 2, 2, 3, 3, 3, 3]
        assert [options["queued_workload"] for _, options in calls] == [
            None,
            None,
            None,
            "P2",
            "P3",
            "P4",
            None,
            "P2",
            "P3",
            "P4",
        ]
        assert all(options["output_tokens"] == 64 for _, options in calls)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(script), run_name="__main__")
    assert error.value.code == 2
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
