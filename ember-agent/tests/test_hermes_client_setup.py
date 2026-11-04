"""Dedicated Desktop setup must preserve existing profiles and protect its copied token."""

import hashlib
import importlib
import json
import os
import stat
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def client_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    scripts = Path(__file__).resolve().parents[2] / "scripts" / "hermes"
    monkeypatch.syspath_prepend(str(scripts))
    module = importlib.import_module("hermes_client_setup")
    source = tmp_path / "sources"
    source.mkdir()
    (source / "hermes_client_plugin.py").write_text(
        "raise AssertionError('SDK import forbidden')\n"
    )
    (source / "hermes_client_transport.py").write_text("# synthetic transport bytes\n")
    (source / "hermes_client_setup.py").write_text("# setup source receipt fixture\n")
    monkeypatch.setattr(module, "__file__", str(source / "hermes_client_setup.py"))
    token = tmp_path / "existing-client.token"
    token.write_text("synthetic-private-token-" + "a" * 32)
    return SimpleNamespace(
        module=module,
        home=tmp_path / "ember-home",
        desktop_data=tmp_path / "ember-desktop",
        token_file=token,
        source=source,
    )


def prepare(environment: SimpleNamespace, **updates: object) -> str:
    options = {
        "base_url": "http://127.0.0.1:8787/hermes/v1",
        "context_tokens": 131072,
        "search_endpoint": "http://192.0.2.5:8095",
        **updates,
    }
    return environment.module.prepare_profile(
        environment.home, environment.desktop_data, environment.token_file, **options
    )


def test_setup_copies_only_profile_assets_and_keeps_secret_out_of_receipt_and_launcher(
    client_setup: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    environment = client_setup
    digest = prepare(environment)
    config = json.loads((environment.home / "config.yaml").read_text())
    assert config["model"]["context_length"] == 131072
    assert config["providers"]["ember"]["key_env"] == "EMBER_CLIENT_TOKEN"
    assert config["agent"]["reasoning_effort"] == "none"
    assert config["context"]["engine"] == "ember-client"
    assert config["plugins"]["enabled"] == ["ember-client"]
    assert not config["compression"]["enabled"] and not config["fallback_providers"]
    assert not config["auxiliary"]["title_generation"]["enabled"]
    assert not config["auxiliary"]["background_review"]["enabled"]
    assert not config["memory"]["memory_enabled"]
    assert config["platform_toolsets"]["cli"] == ["search"] and not config["mcp_servers"]
    token = environment.token_file.read_text()
    assert (environment.home / ".env").read_text().splitlines() == [
        "EMBER_CLIENT_TOKEN=" + token,
        "SEARXNG_URL=http://192.0.2.5:8095",
    ]
    plugin = environment.home / "plugins/ember-client"
    assert (plugin / "__init__.py").read_bytes() == (
        environment.source / "hermes_client_plugin.py"
    ).read_bytes()
    receipt_text = (environment.home / "ember-setup.json").read_text()
    receipt = environment.module.SetupReceipt.model_validate_json(receipt_text)
    assert hashlib.sha256(receipt_text.encode()).hexdigest() == digest
    assert ".env" not in receipt.files
    for name, expected in receipt.files.items():
        assert hashlib.sha256((environment.home / name).read_bytes()).hexdigest() == expected
    launcher = (environment.home / "start-hermes.ps1").read_text(encoding="utf-8-sig")
    assert "& $alias desktop --cwd" in launcher
    assert "'Process'" in launcher and "finally" in launcher
    assert "HERMES_TUI_TOOLSETS = 'search'" in launcher
    assert "HERMES_DESKTOP_REMOTE_URL = $null" in launcher
    assert "remote-debugging" not in launcher and "Invoke-CommandInDesktopPackage" not in launcher
    assert token not in receipt_text + launcher + json.dumps(config) + capsys.readouterr().out
    if os.name != "nt":
        assert stat.S_IMODE(environment.home.stat().st_mode) == 0o700
        assert stat.S_IMODE((environment.home / ".env").stat().st_mode) == 0o600


@pytest.mark.parametrize("existing", ["home", "desktop_data"])
def test_setup_never_overwrites_an_existing_profile(
    client_setup: SimpleNamespace, existing: str
) -> None:
    path = getattr(client_setup, existing)
    path.mkdir()
    sentinel = path / "existing-state"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        prepare(client_setup)
    assert sentinel.read_text() == "preserve"
    other = client_setup.desktop_data if existing == "home" else client_setup.home
    assert not other.exists()


@pytest.mark.parametrize(
    "options",
    [
        {"context_tokens": 63999},
        {"context_tokens": True},
        {"base_url": "http://192.0.2.5:8787/hermes/v1"},
        {"base_url": "http://127.0.0.1:8787/v1"},
        {"search_endpoint": "http://user:credential@192.0.2.5:8095"},
        {"search_endpoint": "http://192.0.2.5:8095\nOTHER=value"},
    ],
)
def test_invalid_capacity_or_service_scope_leaves_no_partial_profile(
    client_setup: SimpleNamespace, options: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        prepare(client_setup, **options)
    assert not client_setup.home.exists() and not client_setup.desktop_data.exists()


@pytest.mark.parametrize("value", ["a" * 43 + "\nOTHER=secret", "a" * 43 + " " * 300])
def test_token_cannot_inject_keys_or_hide_a_truncated_suffix(
    client_setup: SimpleNamespace, value: str
) -> None:
    client_setup.token_file.write_text(value)
    with pytest.raises(ValueError):
        prepare(client_setup)
    assert not client_setup.home.exists() and not client_setup.desktop_data.exists()


def test_overlapping_roots_are_rejected(client_setup: SimpleNamespace) -> None:
    client_setup.desktop_data = client_setup.home / "desktop"
    with pytest.raises(ValueError):
        prepare(client_setup)
    assert not client_setup.home.exists()


def test_failure_after_secret_write_removes_only_both_new_roots(
    client_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = client_setup.module
    write = module._write

    def failing_write(path: Path, content: bytes) -> None:
        if path.name == "ember-setup.json":
            raise OSError("Synthetic final-write failure")
        write(path, content)

    monkeypatch.setattr(module, "_write", failing_write)
    original = client_setup.token_file.read_bytes()
    with pytest.raises(OSError, match="Synthetic final-write failure"):
        prepare(client_setup)
    assert not client_setup.home.exists() and not client_setup.desktop_data.exists()
    assert client_setup.token_file.read_bytes() == original


def test_windows_acl_is_private_before_profile_content_is_written(
    client_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    module: ModuleType = client_setup.module
    calls: list[list[str]] = []

    def run(arguments: list[str], **options: object) -> SimpleNamespace:
        calls.append(arguments)
        return SimpleNamespace(stdout='"synthetic-user","S-1-5-21-100-200-300-400"\n')

    monkeypatch.setattr(
        module, "os", SimpleNamespace(name="nt", environ={"SystemRoot": "C:/Windows"})
    )
    monkeypatch.setattr(module.subprocess, "run", run)
    module._private_directory(client_setup.home)
    assert calls[0][1:] == ["/user", "/fo", "csv", "/nh"]
    assert calls[1][1:] == [
        str(client_setup.home),
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-21-100-200-300-400:(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",
    ]


def test_named_profile_is_published_standalone_without_touching_regular_desktop_data(
    client_setup: SimpleNamespace,
) -> None:
    env = client_setup
    home = env.home.parent / "normal-hermes" / "profiles" / "ember"
    desktop = env.home.parent / "existing-desktop"
    desktop.mkdir()
    (desktop / "preserve").write_text("existing chat")
    digest = env.module.prepare_named_profile(
        home,
        env.token_file,
        base_url="http://127.0.0.1:8787/hermes/v1",
        context_tokens=131072,
        search_endpoint="http://192.0.2.5:8095",
    )
    assert json.loads((home / "config.yaml").read_text())["gateway"] == {"standalone": True}
    assert json.loads((home / "profile.yaml").read_text())["display_name"] == "Ember"
    assert not (home / "start-hermes.ps1").exists()
    receipt = env.module.SetupReceipt.model_validate_json((home / "ember-setup.json").read_bytes())
    assert receipt.home == home and receipt.desktop_data is None
    assert hashlib.sha256((home / "ember-setup.json").read_bytes()).hexdigest() == digest
    assert list(home.parent.iterdir()) == [home]
    assert (desktop / "preserve").read_text() == "existing chat"


def test_named_profile_never_replaces_an_existing_name(client_setup: SimpleNamespace) -> None:
    env = client_setup
    home = env.home.parent / "profiles" / "ember"
    home.mkdir(parents=True)
    (home / "preserve").write_text("existing profile")
    with pytest.raises(FileExistsError):
        env.module.prepare_named_profile(
            home,
            env.token_file,
            base_url="http://127.0.0.1:8787/hermes/v1",
            context_tokens=131072,
            search_endpoint="http://192.0.2.5:8095",
        )
    assert (home / "preserve").read_text() == "existing profile"


def test_named_profile_preserves_unowned_staging_collision(
    client_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = client_setup
    home = env.home.parent / "profiles" / "ember"
    collision = home.parent.parent / ".ember-install-fixed"
    collision.mkdir(parents=True)
    (collision / "preserve").write_text("not owned by this setup")
    monkeypatch.setattr(env.module, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    with pytest.raises(FileExistsError):
        env.module.prepare_named_profile(
            home,
            env.token_file,
            base_url="http://127.0.0.1:8787/hermes/v1",
            context_tokens=131072,
            search_endpoint="http://192.0.2.5:8095",
        )
    assert (collision / "preserve").read_text() == "not owned by this setup"
    assert not home.exists()


def test_named_profile_failure_does_not_publish_partial_profile(
    client_setup: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = client_setup
    home = env.home.parent / "profiles" / "ember"
    write = env.module._write

    def fail_metadata(path: Path, content: bytes) -> None:
        if path.name == "profile.yaml":
            raise OSError("Synthetic metadata failure")
        write(path, content)

    monkeypatch.setattr(env.module, "_write", fail_metadata)
    with pytest.raises(OSError, match="Synthetic metadata failure"):
        env.module.prepare_named_profile(
            home,
            env.token_file,
            base_url="http://127.0.0.1:8787/hermes/v1",
            context_tokens=131072,
            search_endpoint="http://192.0.2.5:8095",
        )
    assert not home.exists()
    assert not home.parent.exists() or list(home.parent.iterdir()) == []
