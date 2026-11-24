"""Runtime configuration tests, without services, models, or canonical writes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ember_agent.cli import main
from ember_agent.runtime.config import ConfigurationError, load_config
from ember_agent.runtime.health import ReadinessReport, readiness

EXAMPLE = Path(__file__).parents[1] / "config/deployment.example.toml"


def harness_section(executable: Path, workspace: Path) -> str:
    return (
        f'\n[harness]\nprovider="opencode"\ncommand=[{json.dumps(str(executable))}]\n'
        f'revision="2.3.2"\nprofile_sha256="{"a" * 64}"\nworkspace={json.dumps(str(workspace))}\n'
    )


def test_harness_pin_is_explicit_and_never_probed(config_path: Path, tmp_path: Path) -> None:
    baseline = load_config(config_path)
    assert baseline.selected_harness is None and readiness(baseline).harness == "disabled"
    executable = tmp_path / "not-installed"
    workspace = tmp_path / "not-created"
    config_path.write_text(config_path.read_text() + harness_section(executable, workspace))
    config = load_config(config_path)
    harness = config.selected_harness
    assert harness is config.harness and harness is not None
    assert harness.provider == "opencode" and harness.revision == "2.3.2"
    assert harness.command == (str(executable),) and harness.workspace == workspace
    assert harness.profile_sha256 == "a" * 64
    assert not (harness.tools or harness.private_recall or harness.skills)
    assert readiness(config).harness == "configured_unprobed"
    assert not any(
        path.exists() for path in (executable, workspace, config.runtime_root, config.data_root)
    )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('provider="opencode"', ""),
        ('revision="2.3.2"', 'revision=" "'),
        ("a" * 64, "unverified"),
        ("command=", "old_command="),
        ("workspace=", "old_workspace="),
    ],
)
def test_harness_rejects_incomplete_pin(
    config_path: Path, tmp_path: Path, before: str, after: str
) -> None:
    section = harness_section(tmp_path / "opencode", tmp_path / "workspace")
    config_path.write_text(config_path.read_text() + section.replace(before, after))
    with pytest.raises(ConfigurationError, match=r"Invalid \[harness\]"):
        load_config(config_path)


@pytest.mark.parametrize(
    "mode", ["path-command", "relative-workspace", "arguments", "duplicate-grant"]
)
def test_harness_rejects_unpinned_paths_arguments_and_duplicate_grants(
    config_path: Path, tmp_path: Path, mode: str
) -> None:
    executable = tmp_path / "opencode"
    workspace = tmp_path / "workspace"
    section = harness_section(executable, workspace)
    match mode:
        case "path-command":
            section = section.replace(json.dumps(str(executable)), '"opencode"')
        case "relative-workspace":
            section = section.replace(json.dumps(str(workspace)), '"workspace"')
        case "arguments":
            section = section.replace("]\nrevision", ', "run"]\nrevision')
        case "duplicate-grant":
            section += 'tools=["native.inspect_text", "native.inspect_text"]\n'
    config_path.write_text(config_path.read_text() + section)
    with pytest.raises(ConfigurationError, match=r"Invalid \[harness\]"):
        load_config(config_path)


@pytest.mark.parametrize(
    ("section", "diagnostic"),
    [
        ("[hermes]", "Remove \\[hermes\\]"),
        ("[hermes_client]", "Remove \\[hermes_client\\]"),
        ('[harness]\nprovider="hermes"', "explicit OpenCode pin"),
        ('[public_search]\nprovider="hermes"', "searxng_url"),
        ("[inference]\ncoding_memory = true", "Delete inference\\.coding_memory"),
        ("[inference]\ncoding_memory_write = false", "Delete inference\\.coding_memory"),
    ],
)
def test_removed_settings_report_migration_instead_of_adopting(
    config_path: Path, section: str, diagnostic: str
) -> None:
    config_path.write_text(config_path.read_text() + "\n" + section + "\n")
    with pytest.raises(ConfigurationError, match=diagnostic):
        load_config(config_path)


@pytest.mark.parametrize("use_url", [True, False])
def test_public_search_can_run_independently_of_harness(config_path: Path, use_url: bool) -> None:
    command = f'command=[{json.dumps(sys.executable)}, "search.py"]'
    url = 'searxng_url="http://nas.example:8095"'
    config_path.write_text(
        config_path.read_text() + "\n[public_search]\n" + (url if use_url else command)
    )
    config = load_config(config_path)
    assert config.harness is None
    search = config.public_search
    assert search is not None
    assert search.command == (() if use_url else (sys.executable, "search.py"))
    assert search.searxng_url == ("http://nas.example:8095" if use_url else None)
    assert not config.runtime_root.exists()


@pytest.mark.parametrize("provider", ["exa", "parallel"])
def test_hosted_public_search_uses_offline_environment_reference(
    config_path: Path, provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TEST_WEB_API_KEY", raising=False)
    config_path.write_text(
        config_path.read_text()
        + f'\n[public_search]\nprovider="{provider}"\napi_key_env="TEST_WEB_API_KEY"\n'
    )
    config = load_config(config_path)
    assert config.harness is None and config.public_search is not None
    assert config.public_search.provider == provider
    assert config.public_search.api_key_env == "TEST_WEB_API_KEY"
    assert not config.runtime_root.exists()


def test_keyless_exa_mcp_is_opt_in_without_harness_or_credentials(config_path: Path) -> None:
    assert load_config(config_path).public_search is None
    config_path.write_text(config_path.read_text() + '\n[public_search]\nprovider="exa_mcp"\n')
    config = load_config(config_path)
    assert config.harness is None and config.public_search is not None
    assert config.public_search.provider == "exa_mcp"
    assert config.public_search.api_key_env is None and not config.runtime_root.exists()


@pytest.mark.parametrize(
    "fields",
    [
        "",
        'command=["search"]',
        'provider="exa_mcp"\napi_key_env="WEB_KEY"',
        'provider="exa_mcp"\nendpoint="https://example.com/mcp"',
        'provider="exa_mcp"\ntools=["agent_run"]',
        'provider="exa_mcp"\nsearxng_url="https://example.com"',
        f'provider="exa_mcp"\ncommand=[{json.dumps(sys.executable)}]',
        'provider="exa"',
        'provider="parallel"\napi_key_env="secret value"',
        'provider="exa"\napi_key="secret"',
        'provider="exa"\napi_key_env="WEB_KEY"\nendpoint="https://other.example"',
        'provider="parallel"\napi_key_env="WEB_KEY"\nsearxng_url="http://nas.example"',
        f'provider="exa"\napi_key_env="WEB_KEY"\ncommand=[{json.dumps(sys.executable)}]',
        'api_key_env="WEB_KEY"\nsearxng_url="http://nas.example"',
        'searxng_url="http://user:secret@nas.example"',
        'searxng_url="http://nas.example:invalid"',
        'searxng_url="http://nas.example/search"',
        'searxng_url="http://nas.example/?"',
        'searxng_url="file:///etc/hosts"',
        f'searxng_url="http://nas.example"\ncommand=[{json.dumps(sys.executable)}]',
    ],
)
def test_public_search_rejects_ambiguous_or_unsafe_transport(
    config_path: Path, fields: str
) -> None:
    config_path.write_text(config_path.read_text() + "\n[public_search]\n" + fields)
    with pytest.raises(ConfigurationError, match=r"Invalid \[public_search\]") as error:
        load_config(config_path)
    assert "secret" not in str(error.value)


def test_mcp_configuration_is_bounded_explicit_and_does_not_launch_servers(
    config_path: Path,
) -> None:
    original = config_path.read_text()
    server = (
        '\n[[mcp_servers]]\nid="fixture"\ntransport="stdio"\ncommand='
        + json.dumps(sys.executable)
        + '\narguments=["-c", "raise AssertionError(\'must not launch during config parsing\')"]\n'
        + 'grants=[{name="echo", effect="read"}]\n'
    )
    config_path.write_text(original + server)
    config = load_config(config_path)
    assert config.mcp_servers[0].command == sys.executable
    assert config.mcp_servers[0].grants[0].effect == "read"
    assert not config.runtime_root.exists()
    config_path.write_text(original + server + server)
    with pytest.raises(ConfigurationError, match="MCP"):
        load_config(config_path)
    config_path.write_text(
        original
        + "".join(
            f'\n[[mcp_servers]]\nid="fixture{i}"\ntransport="http"\nurl="http://127.0.0.1:1/mcp"\n'
            for i in range(9)
        )
    )
    with pytest.raises(ConfigurationError):
        load_config(config_path)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """Provide an isolated copy of the deployment example."""
    path = tmp_path / "ember-agent/config/deployment.local.toml"
    path.parent.mkdir(parents=True)
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


def test_natural_deep_recall_is_opt_in(config_path: Path) -> None:
    text = config_path.read_text(encoding="utf-8")
    assert "natural_deep_recall = false" in text
    config_path.write_text(text.replace("natural_deep_recall = false", ""), encoding="utf-8")
    assert not load_config(config_path).natural_deep_recall
    config_path.write_text(
        text.replace("natural_deep_recall = false", "natural_deep_recall = true"), encoding="utf-8"
    )
    assert load_config(config_path).natural_deep_recall


def test_deep_archive_relevance_rejects_unknown_profile(config_path: Path) -> None:
    text = config_path.read_text(encoding="utf-8")
    assert load_config(config_path).deep_archive_relevance is None
    config_path.write_text(
        text.replace(
            '# deep_archive_relevance = "corroborated_v1"', 'deep_archive_relevance = "unknown"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="invalid types, values, or unknown keys"):
        load_config(config_path)


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


def test_tts_service_is_neutral_with_legacy_section_compatibility(config_path: Path) -> None:
    original = config_path.read_text()
    current = load_config(config_path)
    assert current.audio.tts_voice == "Juniper" and current.audio.tts_speed == 1.1
    assert "tts" in current.services.model_dump() and "kokoro" not in current.services.model_dump()
    legacy = original.replace("[services.tts]", "[services.kokoro]")
    config_path.write_text(legacy)
    assert load_config(config_path).services.tts == current.services.tts
    config_path.write_text(legacy.replace('tts_model = "tts-1"\n', ""))
    assert load_config(config_path).audio.tts_model == "kokoro"
    # Old Kokoro deployments keep their explicit model/voice and default to normal pace.
    config_path.write_text(
        legacy.replace('tts_model = "tts-1"', 'tts_model = "kokoro"')
        .replace('tts_voice = "Juniper"', 'tts_voice = "af_alloy"')
        .replace("tts_speed = 1.1", "")
    )
    old = load_config(config_path)
    assert (old.audio.tts_model, old.audio.tts_voice, old.audio.tts_speed) == (
        "kokoro",
        "af_alloy",
        1.0,
    )
    config_path.write_text(
        original + '\n[services.kokoro]\nendpoint="http://127.0.0.1:8880"\nmode="attach"\n'
    )
    with pytest.raises(ConfigurationError):
        load_config(config_path)


@pytest.mark.parametrize("value", ["0.5", "1.6", "nan", "inf", "true"])
def test_tts_pace_rejects_invalid_configured_defaults(config_path: Path, value: str) -> None:
    config_path.write_text(
        config_path.read_text().replace("tts_speed = 1.1", "tts_speed = " + value)
    )
    with pytest.raises(ConfigurationError):
        load_config(config_path)


def test_explicit_vllm_profile_is_local_offline_and_keeps_ollama_default(
    config_path: Path,
) -> None:
    original = config_path.read_text()
    assert load_config(config_path).inference.backend == "ollama"
    profile = (
        '\n[inference]\nbackend="vllm"\n[inference.vllm]\n'
        'endpoint="http://127.0.0.1:8000"\nversion="0.31.0"\n'
        'artifact_sha256="'
        + "a" * 64
        + '"\nprofile_sha256="'
        + "b" * 64
        + '"\napi_key_environment="EMBER_VLLM_TOKEN"\n'
    )
    config_path.write_text(original + profile)
    loaded = load_config(config_path)
    assert loaded.inference.backend == "vllm"
    assert loaded.inference.vllm is not None
    assert loaded.inference.vllm.artifact_sha256 == "a" * 64
    report = readiness(loaded)
    assert report.inference_backend == "vllm" and "ollama" not in report.services
    assert report.services["vllm"].health == "not_probed"
    assert not loaded.runtime_root.exists()
    for invalid in [
        profile.replace("127.0.0.1:8000", "example.com:8000"),
        profile.replace("127.0.0.1:8000", "user:secret@127.0.0.1:8000"),
        profile.replace("127.0.0.1:8000", "127.0.0.1:11435"),
        profile.replace('version="0.31.0"', 'version="0.33.0"'),
        profile.replace("EMBER_VLLM_TOKEN", "literal token"),
        '\n[inference]\nbackend="vllm"\n',
    ]:
        config_path.write_text(original + invalid)
        with pytest.raises(ConfigurationError):
            load_config(config_path)
