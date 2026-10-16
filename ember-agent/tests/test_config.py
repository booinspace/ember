"""Runtime configuration tests, without services, models, or canonical writes."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ember_agent.cli import main
from ember_agent.runtime.config import ConfigurationError, load_config
from ember_agent.runtime.health import ReadinessReport, readiness

EXAMPLE = Path(__file__).parents[1] / "config/deployment.example.toml"


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """Provide an isolated copy of the deployment example."""
    path = tmp_path / "config/deployment.local.toml"
    path.parent.mkdir()
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_pending_pin_validates_without_state_or_runtime_readiness(config_path: Path) -> None:
    config = load_config(config_path)
    assert not config.model_pin_configured
    assert not config.data_root.exists()
    report = readiness(config)
    assert report.application == "experimental_phase8"
    assert report.voice == "disabled"
    assert all(service.health == "not_probed" for service in report.services.values())
    with pytest.raises(ConfigurationError, match="tag and digest"):
        load_config(config_path, require_model_pin=True)


def test_paths_resolve_relative_to_config_file(config_path: Path) -> None:
    config = load_config(config_path)
    assert config.data_root == (config_path.parent / "../../.local/data").resolve()
    assert config.runtime_root == (config_path.parent / "../../.local/runtime").resolve()


def test_valid_pin_is_configured_but_unverified(config_path: Path) -> None:
    original = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        original.replace('tag = ""', 'tag = "actual-model:local"').replace(
            'expected_digest = ""',
            'expected_digest = "sha256:' + "a" * 64 + '"',
        ),
        encoding="utf-8",
    )
    config = load_config(config_path, require_model_pin=True)
    assert readiness(config).model_pin == "configured_unverified"


@pytest.mark.parametrize(
    "before,after",
    [
        ("context_tokens = 8192", "context_tokens = 7000"),
        ("max_input_tokens = 7000", "max_input_tokens = true"),
        ('tag = ""', 'tag = "unverified-tag"'),
        ('expected_digest = ""', 'expected_digest = "invalid"'),
        ("http://127.0.0.1:11435", "http://127.0.0.1:11434"),
        ("http://127.0.0.1:11435", "http://localhost:11434/"),
        ("http://127.0.0.1:11435", "https://example.com:443"),
        ("http://127.0.0.1:11435", "http://user:secret@127.0.0.1:11435"),
        ("preemption_enabled = false", "preemption_enabled = true"),
        ("preemption_enabled = false", "preemption_enabled = 0"),
        ('mode = "disabled"', 'mode = "attach"'),
        ('runtime_root = "../../.local/runtime"', 'runtime_root = "../../.local/data/runtime"'),
        ("config_version = 1", "config_version = 2"),
        ("config_version = 1", "config_version = true"),
    ],
)
def test_invalid_configurations_fail_closed(config_path: Path, before: str, after: str) -> None:
    original = config_path.read_text(encoding="utf-8")
    config_path.write_text(original.replace(before, after), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(config_path)


def test_unknown_private_fields_are_rejected_without_log_disclosure(
    config_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        original + '\nPRIVATE_KEY_SENTINEL = "PRIVATE_VALUE_SENTINEL"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as raised:
        load_config(config_path)
    assert raised.value.__cause__ is not None
    assert main(["doctor", "--config", str(config_path)]) == 2
    captured = capsys.readouterr()
    assert "SENTINEL" not in captured.err + captured.out


def test_cli_success_means_config_validity_only_and_start_fails(
    config_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor", "--config", str(config_path), "--json"]) == 0
    report = ReadinessReport.model_validate_json(capsys.readouterr().out)
    assert report.character == "available_unprobed"
    assert main(["start"]) == 2
    assert main(["doctor", "--config", str(config_path), "--require-model-pin"]) == 2


@pytest.mark.end_to_end
def test_agent_entry_point_serializes_truthful_readiness(config_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "ember_agent", "doctor", "--config", str(config_path), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = ReadinessReport.model_validate_json(completed.stdout)
    assert report.application == "experimental_phase8"
    assert report.model_pin == "pending"


@pytest.mark.end_to_end
@pytest.mark.parametrize("command", ["ember", "ember-agent"])
def test_installed_ember_commands_use_the_project_name(command: str) -> None:
    executable = Path(sys.executable).parent / (command + ".exe" if os.name == "nt" else command)
    completed = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("ember ")


def test_start_routes_explicit_browser_launch_to_existing_runtime(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ember_agent.runtime.config import DeploymentConfig

    original = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        original.replace('tag = ""', 'tag = "synthetic"').replace(
            'expected_digest = ""', 'expected_digest = "' + "a" * 64 + '"'
        ),
        encoding="utf-8",
    )
    launches: list[bool] = []

    async def run(config: DeploymentConfig, path: Path, *, open_browser: bool = False) -> None:
        assert config.model_tag == "synthetic" and path == config_path
        launches.append(open_browser)

    monkeypatch.setattr("ember_agent.runtime.livekit_runtime.run_runtime", run)
    assert main(["start", "--config", str(config_path), "--open-browser"]) == 0
    assert main(["start", "--config", str(config_path)]) == 0
    assert launches == [True, False]


def test_disabled_browser_launch_fails_before_starting_services(config_path: Path) -> None:
    import asyncio

    from ember_agent.runtime.livekit_runtime import run_runtime

    config = load_config(config_path)
    with pytest.raises(ConfigurationError, match="browser.enabled"):
        asyncio.run(run_runtime(config, config_path, open_browser=True))
    assert not config.data_root.exists() and not config.runtime_root.exists()


def test_doctor_reports_configured_speech_and_browser_without_runtime_probes(
    config_path: Path,
) -> None:
    text = config_path.read_text(encoding="utf-8")
    text = text.replace("[audio]\nenabled = false", "[audio]\nenabled = true")
    text = text.replace('stt_model_directory = ""', 'stt_model_directory = "missing-model"')
    text = text.replace("[browser]\nenabled = false", "[browser]\nenabled = true")
    config_path.write_text(text, encoding="utf-8")
    config = load_config(config_path)
    report = readiness(config)
    assert report.phase == "8" and report.voice == "configured_unprobed"
    assert report.browser == "configured_unprobed"
    assert all(service.health == "not_probed" for service in report.services.values())
    assert not config.data_root.exists() and not config.runtime_root.exists()
