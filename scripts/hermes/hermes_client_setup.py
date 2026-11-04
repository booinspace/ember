"""Provision a dedicated Hermes Desktop profile without importing or installing its SDK."""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, JsonValue

PACKAGE_VERSION = "2026.7363.3271.0"
SDK_REVISION = "b200f5bc60e31588ba2cb60e288029c4a54ba5fa"
TOKEN_ENV = "EMBER_CLIENT_TOKEN"


class SetupReceipt(BaseModel):
    """Record configured capacity and public asset hashes; never include the credential."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["ember-hermes-client-setup-1"] = "ember-hermes-client-setup-1"
    package_version: str = PACKAGE_VERSION
    sdk_revision: str = SDK_REVISION
    home: Path
    desktop_data: Path | None
    configured_context_tokens: int
    files: dict[str, str]
    setup_source_sha256: str


def _origin(value: str, *, model: bool = False) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Service URLs cannot contain control characters.")
    address = urlsplit(value)
    if (
        address.scheme not in ({"http"} if model else {"http", "https"})
        or not address.hostname
        or address.username is not None
        or address.password is not None
        or address.query
        or address.fragment
        or (address.path != "/hermes/v1" if model else address.path not in {"", "/"})
    ):
        raise ValueError("Use the explicit Ember loopback ingress and a search service origin.")
    if model and address.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("The model ingress must use an explicit loopback address.")
    if address.port is not None and not 1 <= address.port <= 65535:
        raise ValueError("Invalid service port.")
    return value.rstrip("/")


def _private_directory(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700)
        return
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    identity = subprocess.run(
        [str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    rows = list(csv.reader(io.StringIO(identity.stdout)))
    if len(rows) != 1 or len(rows[0]) != 2 or re.fullmatch(r"S-\d+(?:-\d+)+", rows[0][1]) is None:
        raise ValueError("Cannot identify the Windows profile owner.")
    subprocess.run(
        [
            str(system / "icacls.exe"),
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"*{rows[0][1]}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ],
        check=True,
        capture_output=True,
    )


def _write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)


def _quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _launcher(home: Path, desktop_data: Path) -> bytes:
    return (
        f"""$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$package = @(Get-AppxPackage -Name 'NousResearchInc.HermesAgent')
if ($package.Count -ne 1 -or [string]$package[0].Version -ne '{PACKAGE_VERSION}') {{
    throw 'Install or requalify the pinned Hermes Desktop package before launching this profile.'
}}
$stampPath = Join-Path $package[0].InstallLocation 'app\\resources\\install-stamp.json'
$stamp = Get-Content -LiteralPath $stampPath -Raw | ConvertFrom-Json
if ($stamp.commit -ne '{SDK_REVISION}') {{ throw 'The Hermes Desktop SDK revision changed.' }}
$alias = Join-Path $env:LOCALAPPDATA 'Microsoft\\WindowsApps\\hermes.exe'
if (-not (Test-Path -LiteralPath $alias -PathType Leaf)) {{
    throw 'Enable the installed Hermes app execution alias in Windows Settings.'
}}
$overrides = @{{
    HERMES_HOME = {_quote(home)}
    HERMES_DESKTOP_USER_DATA_DIR = {_quote(desktop_data)}
    HERMES_TUI_TOOLSETS = 'search'
    HERMES_ENABLE_PROJECT_PLUGINS = '0'
    PYTHONDONTWRITEBYTECODE = '1'
    HERMES_DESKTOP_REMOTE_URL = $null
    HERMES_DESKTOP_REMOTE_TOKEN = $null
    HERMES_DESKTOP_DEV_SERVER = $null
    HERMES_DESKTOP_HERMES_ROOT = $null
    HERMES_BUNDLED_PLUGINS = $null
}}
$previous = @{{}}
try {{
    foreach ($name in $overrides.Keys) {{
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $overrides[$name], 'Process')
    }}
    & $alias desktop --cwd {_quote(home)}
    if ($LASTEXITCODE -ne 0) {{ throw 'Hermes Desktop could not launch this profile.' }}
}} finally {{
    foreach ($name in $previous.Keys) {{
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }}
}}
"""
    ).encode("utf-8-sig")


def prepare_profile(
    home: Path,
    desktop_data: Path,
    token_file: Path,
    *,
    base_url: str,
    context_tokens: int,
    search_endpoint: str,
) -> str:
    """Create only new profile roots, returning the credential-free setup receipt hash."""
    home, desktop_data = home.resolve(), desktop_data.resolve()
    if (
        home == desktop_data
        or home.is_relative_to(desktop_data)
        or desktop_data.is_relative_to(home)
    ):
        raise ValueError("Hermes home and Desktop data must be separate directories.")
    if home.exists() or desktop_data.exists():
        raise FileExistsError("Setup requires two new dedicated Hermes directories.")
    if type(context_tokens) is not int or not 64000 <= context_tokens <= 2147483647:
        raise ValueError(
            "Use the actual configured context, at least Hermes' 64,000-token minimum."
        )
    base_url, search_endpoint = _origin(base_url, model=True), _origin(search_endpoint)
    with token_file.open("rb") as source:
        raw = source.read(258)
    if len(raw) > 257:
        raise ValueError("The private Ember client token file exceeds its bound.")
    token = raw.decode("ascii").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token) is None:
        raise ValueError("Use the existing private Ember client token file.")
    sources = Path(__file__).resolve().parent
    files: dict[str, bytes] = {
        "plugins/ember-client/__init__.py": (sources / "hermes_client_plugin.py").read_bytes(),
        "plugins/ember-client/hermes_client_transport.py": (
            sources / "hermes_client_transport.py"
        ).read_bytes(),
        "plugins/ember-client/plugin.yaml": (
            b"name: ember-client\nversion: 0.1.0\nkind: standalone\n"
            b"provides_hooks: [llm_request, pre_tool_call, post_tool_call]\n"
        ),
        "start-hermes.ps1": _launcher(home, desktop_data),
    }
    override: JsonValue = {
        "ember": {
            "context_window": context_tokens,
            "supports_tools": True,
            "supports_vision": False,
            "supports_reasoning": False,
        }
    }
    config: dict[str, JsonValue] = {
        "model": {
            "provider": "ember",
            "default": "ember",
            "base_url": base_url,
            "context_length": context_tokens,
        },
        "providers": {
            "ember": {
                "base_url": base_url,
                "api_mode": "chat_completions",
                "key_env": TOKEN_ENV,
                "session_affinity_header": "X-Ember-Session",
            }
        },
        "model_overrides": {"custom": override, "ember": override},
        "max_tokens": 512,
        "agent": {
            "reasoning_effort": "none",
            "disabled_toolsets": ["desktop_ui", "project", "catalog"],
            "environment_probe": False,
            "tool_use_enforcement": False,
            "execution_guidance": False,
            "task_completion_guidance": False,
            "parallel_tool_call_guidance": False,
        },
        "compression": {"enabled": False},
        "auxiliary": {
            "title_generation": {"enabled": False, "model_upgrade_enabled": False},
            "background_review": {"enabled": False},
        },
        "memory": {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0},
        "model_catalog": {"enabled": False},
        "fallback_providers": [],
        "tools": {"tool_search": {"enabled": "off"}, "connectors": {"enabled": False}},
        "platform_toolsets": {"cli": ["search"]},
        "mcp_servers": {},
        "web": {"search_backend": "searxng", "keyless_fallback": False, "keyless_rescue": False},
        "context": {"engine": "ember-client"},
        "plugins": {"enabled": ["ember-client"]},
    }
    files["config.yaml"] = (json.dumps(config, indent=2) + "\n").encode()
    created: list[Path] = []
    try:
        for directory in (home, desktop_data):
            directory.parent.mkdir(parents=True, exist_ok=True)
            directory.mkdir(mode=0o700)
            created.append(directory)
            _private_directory(directory)
        for name, content in files.items():
            path = home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            _write(path, content)
        _write(home / ".env", f"{TOKEN_ENV}={token}\nSEARXNG_URL={search_endpoint}\n".encode())
        receipt = SetupReceipt(
            home=home,
            desktop_data=desktop_data,
            configured_context_tokens=context_tokens,
            files={name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
            setup_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        ).model_dump_json(indent=2)
        receipt_bytes = (receipt + "\n").encode()
        _write(home / "ember-setup.json", receipt_bytes)
        return hashlib.sha256(receipt_bytes).hexdigest()
    except BaseException as error:
        failures: list[BaseException] = [error]
        for directory in reversed(created):
            try:
                shutil.rmtree(directory)
            except OSError as cleanup_error:
                failures.append(cleanup_error)
        if len(failures) > 1:
            raise BaseExceptionGroup(
                "Cannot finish or clean up the new Hermes profile.", failures
            ) from None
        raise


def main() -> None:
    """Explicit setup only; ordinary application startup never downloads or provisions."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--desktop-data", type=Path)
    parser.add_argument("--named-profile", action="store_true")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--context-tokens", type=int, required=True)
    parser.add_argument("--search-endpoint", required=True)
    args = parser.parse_args()
    if args.named_profile:
        digest = prepare_named_profile(
            args.home,
            args.token_file,
            base_url=args.base_url,
            context_tokens=args.context_tokens,
            search_endpoint=args.search_endpoint,
        )
    else:
        if args.desktop_data is None:
            parser.error("--desktop-data is required for a separate application home")
        digest = prepare_profile(
            args.home,
            args.desktop_data,
            args.token_file,
            base_url=args.base_url,
            context_tokens=args.context_tokens,
            search_endpoint=args.search_endpoint,
        )
    print(json.dumps({"setup_sha256": digest}))


def prepare_named_profile(
    home: Path,
    token_file: Path,
    *,
    base_url: str,
    context_tokens: int,
    search_endpoint: str,
) -> str:
    """Publish a complete standalone profile atomically into the regular Hermes profile list."""
    home = home.resolve()
    if home.parent.name != "profiles" or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", home.name) is None:
        raise ValueError("Use a new named home inside the normal Hermes profiles directory.")
    if home.exists():
        raise FileExistsError("Preserve the existing Hermes profile.")
    # Keep incomplete configuration outside the live profile discovery directory.
    stage = home.parent.parent / ("." + home.name + "-install-" + uuid4().hex)
    scratch = stage.parent / (stage.name + "-desktop")
    owned = False
    try:
        prepare_profile(
            stage,
            scratch,
            token_file,
            base_url=base_url,
            context_tokens=context_tokens,
            search_endpoint=search_endpoint,
        )
        owned = True
        config = json.loads((stage / "config.yaml").read_text(encoding="utf-8"))
        config["gateway"] = {"standalone": True}
        (stage / "config.yaml").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        _write(
            stage / "profile.yaml",
            b'{"display_name":"Ember","description":"Ember chat, character and memory."}\n',
        )
        # Regular Desktop owns its existing data and discovers this profile; no separate window.
        (stage / "start-hermes.ps1").unlink()
        receipt = SetupReceipt.model_validate_json((stage / "ember-setup.json").read_bytes())
        files = {
            name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
            for name in (*receipt.files, "profile.yaml")
            if name != "start-hermes.ps1"
        }
        receipt = receipt.model_copy(update={"home": home, "desktop_data": None, "files": files})
        raw = (receipt.model_dump_json(indent=2) + "\n").encode()
        (stage / "ember-setup.json").write_bytes(raw)
        scratch.rmdir()
        home.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(home)
        return hashlib.sha256(raw).hexdigest()
    finally:
        if owned:
            for unpublished in (stage, scratch):
                if unpublished.exists():
                    shutil.rmtree(unpublished)


if __name__ == "__main__":
    main()
