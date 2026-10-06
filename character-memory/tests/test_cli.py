"""Memory CLI tests, using synthetic source structure only."""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from character_memory_cli.main import main
from character_memory_importers.report_models import ReconnaissanceReport

FIXTURE = Path(__file__).parents[1] / (
    "packages/memory-importers/tests/fixtures/conversations.synthetic.json"
)


@pytest.fixture
def source_archive(tmp_path: Path) -> Path:
    """Provide an isolated synthetic source ZIP for CLI tests."""
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as writer:
        writer.writestr("conversations.json", FIXTURE.read_bytes())
    return archive


def _arguments(archive: Path, output: Path) -> list[str]:
    return [
        "inspect",
        "chatgpt",
        str(archive),
        "--source-namespace",
        "synthetic",
        "--output",
        str(output),
    ]


def test_cli_writes_report_and_refuses_overwrite_or_source_replacement(
    source_archive: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "reports/inspection.json"
    assert main(_arguments(source_archive, output)) == 0
    original_report = output.read_bytes()
    assert main(_arguments(source_archive, output)) == 2
    assert output.read_bytes() == original_report
    assert main(_arguments(source_archive, source_archive)) == 2
    captured = capsys.readouterr()
    assert "SENTINEL" not in captured.out + captured.err


def test_cli_write_failure_returns_safe_diagnostic(
    source_archive: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "PRIVATE_PATH_SENTINEL"
    blocker.write_text("not a directory", encoding="utf-8")
    assert main(_arguments(source_archive, blocker / "report.json")) == 2
    captured = capsys.readouterr()
    assert "Cannot write the requested report file" in captured.err
    assert "PRIVATE_PATH_SENTINEL" not in captured.err


@pytest.mark.end_to_end
def test_cli_entry_point_emits_valid_content_free_report(
    source_archive: Path, tmp_path: Path
) -> None:
    output = tmp_path / "report.json"
    completed = subprocess.run(
        [sys.executable, "-m", "character_memory_cli", *_arguments(source_archive, output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = ReconnaissanceReport.model_validate_json(output.read_bytes())
    assert report.source.files == 1
    assert "SENTINEL" not in output.read_text(encoding="utf-8")
    assert report.privacy.member_paths == "hashed"
