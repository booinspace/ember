"""Managed Strata configuration, offline pinning, and startup ownership regressions."""

import asyncio
import json
import socket
import sys
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from ember_agent.runtime import livekit_runtime
from ember_agent.runtime.config import ConfigurationError, DeploymentConfig, load_config
from ember_agent.runtime.health import readiness
from ember_agent.runtime.livekit_runtime import RuntimeServices
from ember_agent.runtime.processes import OwnedProcess
from ember_agent.runtime.strata_runtime import managed_strata_token, prepare_strata_config

_EXAMPLE = Path(__file__).parents[1] / "config/deployment.example.toml"


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


_FAKE_SERVER = """
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--engine", choices=["strata"], required=True)
parser.add_argument("--config", type=Path, required=True)
parser.add_argument("--host", required=True)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--offline", action="store_true")
parser.add_argument("--ready-file", type=Path, required=True)
args = parser.parse_args()
assert args.offline and args.host == "127.0.0.1" and os.environ["STRATA_NO_PRESET"] == "1"
profile = json.loads(args.config.read_bytes())
model = profile["model_name"]
identity = "1" * 32


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + os.environ["STRATA_API_KEY"]:
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps({
            "/v1/models": {"data": [{"id": model, "max_model_len": 8192,
                                     "status": {"value": "loaded"}}]},
            "/v1/telemetry": {"instance": {"id": os.getenv("EMBER_TEST_STRATA_ID", identity)}},
        }[self.path]).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


with HTTPServer((args.host, args.port), Handler) as server:
    with args.ready_file.open("x") as record:
        json.dump({"schema_version": 1, "instance_id": identity, "pid": os.getpid(),
                   "api_url": f"http://{args.host}:{args.port}", "bound_host": args.host,
                   "bound_port": args.port, "model_id": model, "model_state": "ready",
                   "api_state": "ready", "created_at": 1.0}, record)
    server.serve_forever()
"""


def _profile(tmp_path: Path, port: int) -> tuple[Path, Path, dict[str, object]]:
    server = tmp_path / "server.py"
    server.write_text(_FAKE_SERVER, encoding="utf-8")
    engine = tmp_path / "strata-engine.exe"
    engine.touch()
    model = tmp_path / "model-00001-of-00002.gguf"
    other = tmp_path / "model-00002-of-00002.gguf"
    model.write_bytes(b"first shard fixture")
    other.write_bytes(b"second shard fixture")
    pack = tmp_path / "pack"
    tokenizer = pack / "tokenizer"
    tokenizer.mkdir(parents=True)
    for name in ("vocab.json", "merges.txt", "token_type.json"):
        (tokenizer / name).touch()
    pins = {
        str(artifact): sha256(artifact.read_bytes()).hexdigest()
        for artifact in (server, engine, model, other, *tokenizer.iterdir())
    }
    profile: dict[str, object] = {
        "exe": str(engine),
        "cwd": str(tmp_path),
        "tokenizer": str(tokenizer),
        "model_name": "fixture",
        "host": "127.0.0.1",
        "port": port,
        "args": ["--native", str(model), "--pack", str(pack), "--max-context", "8192"],
        "drafting": "off",
        "ember_artifacts": pins,
    }
    return server, other, profile


def _config(
    tmp_path: Path,
    server: Path,
    profile: dict[str, object],
    *,
    endpoint: str,
    gateway_port: int | None = None,
) -> Path:
    runner = tmp_path / "profile.json"
    runner.write_bytes(json.dumps(profile).encode())
    digest = sha256(runner.read_bytes()).hexdigest()
    values = {
        "endpoint": endpoint,
        "python_executable": sys.executable,
        "server_script": str(server),
        "runner_config": str(runner),
        "runner_config_sha256": digest,
        "startup_timeout_seconds": 4.0,
    }
    base = (
        _EXAMPLE.read_text(encoding="utf-8")
        .replace('tag = ""', 'tag = "fixture"')
        .replace('expected_digest = ""', f'expected_digest = "sha256:{digest}"')
        .replace('"../../.local/runtime"', '"runtime"')
        .replace('"../../.local/data"', '"data"')
        .replace("11435", str(gateway_port if gateway_port is not None else _port()))
    )
    path = tmp_path / "deployment.toml"
    path.write_text(
        base
        + '\n[inference]\nbackend="strata"\n[inference.strata]\n'
        + "\n".join(f"{name}={json.dumps(value)}" for name, value in values.items()),
        encoding="utf-8",
    )
    return path


def _configured(tmp_path: Path) -> tuple[DeploymentConfig, Path]:
    port = _port()
    server, _, profile = _profile(tmp_path, port)
    path = _config(tmp_path, server, profile, endpoint=f"http://127.0.0.1:{port}")
    return load_config(path), path


def test_doctor_offline_and_local_managed_only(tmp_path: Path) -> None:
    config, path = _configured(tmp_path)
    report = readiness(config)
    assert report.inference_backend == "strata"
    assert report.services["strata"].mode == "managed"
    assert report.services["strata"].health == "not_probed"
    assert "ollama" not in report.services and not config.runtime_root.exists()
    original = path.read_text(encoding="utf-8")
    assert config.inference.strata is not None
    for broken in (
        original.replace(config.inference.strata.endpoint, "http://localhost:8095"),
        original.replace(config.inference.strata.endpoint, config.services.gateway.endpoint),
        original.replace(json.dumps(sys.executable), '"python"'),
        original.replace(config.model_digest, "sha256:" + "0" * 64),
        original.replace('mode = "managed"', 'mode = "attach"'),
    ):
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_config(path)


@pytest.mark.parametrize("tamper", ["bytes", "manifest", "host", "config"])
def test_strata_preflight_rejects_modified_or_unpinned_inputs(tmp_path: Path, tamper: str) -> None:
    port = _port()
    server, other, profile = _profile(tmp_path, port)
    if tamper == "manifest":
        pins = profile["ember_artifacts"]
        assert isinstance(pins, dict)
        del pins[str(other)]
    if tamper == "host":
        profile["host"] = "0.0.0.0"
    path = _config(tmp_path, server, profile, endpoint=f"http://127.0.0.1:{port}")
    config = load_config(path)
    strata = config.inference.strata
    assert strata is not None
    if tamper == "bytes":
        other.write_bytes(b"modified second shard")
        assert not strata.verify_model_checksums
        unchecked = tmp_path / "default-snapshot.json"
        prepare_strata_config(config, strata, unchecked)
        assert unchecked.is_file()
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "[inference.strata]", "[inference.strata]\nverify_model_checksums = true"
            ),
            encoding="utf-8",
        )
        config = load_config(path)
        strata = config.inference.strata
        assert strata is not None and strata.verify_model_checksums
    if tamper == "config":
        with Path(strata.runner_config).open("ab") as stream:
            stream.write(b"\n")
    snapshot = tmp_path / "snapshot.json"
    with pytest.raises(ConfigurationError):
        prepare_strata_config(config, strata, snapshot)
    assert not snapshot.exists()


def test_strata_startup_preserves_original_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, path = _configured(tmp_path)
    config = config.model_copy(
        update={"browser": config.browser.model_copy(update={"enabled": True})}
    )
    start = AsyncMock(
        side_effect=ConfigurationError("Gateway journal requires operator quiescence.")
    )
    retry = AsyncMock(side_effect=ConfigurationError("Endpoint occupied."))
    monkeypatch.setattr(RuntimeServices, "start", start)
    monkeypatch.setattr(RuntimeServices, "start_gateway", retry)
    with pytest.raises(ConfigurationError, match="Gateway journal requires operator quiescence"):
        asyncio.run(livekit_runtime.run_runtime(config, path))
    retry.assert_not_awaited()


def test_occupied_strata_is_not_probed_or_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        server, _, profile = _profile(tmp_path, port)
        path = _config(tmp_path, server, profile, endpoint=f"http://127.0.0.1:{port}")
        config = load_config(path)
        owner = RuntimeServices(config, path)
        launcher = Mock()
        monkeypatch.setattr(livekit_runtime, "OwnedProcess", launcher)
        with pytest.raises(ConfigurationError, match="occupied"):
            asyncio.run(owner.start_gateway())
        assert not owner.children and not config.runtime_root.exists()
        launcher.assert_not_called()
        assert livekit_runtime.port_open(f"http://127.0.0.1:{port}")


@pytest.mark.parametrize("foreign_identity", [False, True])
def test_owned_strata_launcher_requires_native_instance_and_shares_launch_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_identity: bool
) -> None:
    config, path = _configured(tmp_path)
    config.runtime_root.mkdir()
    owner = RuntimeServices(config, path)
    gateway = Mock(component="gateway")
    calls: dict[str, tuple[list[str], dict[str, str]]] = {}
    stopped: list[str] = []
    gateway.stop.side_effect = lambda: stopped.append("gateway")

    def spawn(
        command: list[str],
        *,
        root: Path,
        component: str,
        instance: UUID,
        environment: dict[str, str],
    ) -> OwnedProcess | Mock:
        calls[component] = (command, environment)
        if component == "gateway":
            return gateway
        child = OwnedProcess(
            command, root=root, component=component, instance=instance, environment=environment
        )
        stop = child.stop

        def close_runner() -> None:
            stopped.append("strata")
            stop()

        child.stop = close_runner
        return child

    monkeypatch.setattr(livekit_runtime, "OwnedProcess", spawn)
    monkeypatch.setattr(owner, "_await_gateway", AsyncMock())
    monkeypatch.delenv("EMBER_STRATA_API_KEY", raising=False)
    monkeypatch.setenv("EMBER_TEST_STRATA_ID", "2" * 32 if foreign_identity else "1" * 32)
    with pytest.raises(ConfigurationError, match="ember start"):
        managed_strata_token(config)

    async def exercise() -> None:
        try:
            if foreign_identity:
                with pytest.raises(ConfigurationError, match="owned runner instance"):
                    await owner.start_gateway()
                assert "gateway" not in calls and stopped == ["strata"] and not owner.children
                return
            await owner.start_gateway()
            strata_command, strata_env = calls["strata"]
            _, gateway_env = calls["gateway"]
            token = strata_env["STRATA_API_KEY"]
            assert "--offline" in strata_command and token not in strata_command
            assert gateway_env["EMBER_STRATA_API_KEY"] == token
            assert gateway_env["EMBER_STRATA_INSTANCE_ID"] == "1" * 32
            for key, value in gateway_env.items():
                monkeypatch.setenv(key, value)
            assert managed_strata_token(config) == token
            with pytest.raises(ConfigurationError, match="ember start"):
                managed_strata_token(config.model_copy(update={"model_tag": "other"}))
            assert [child.component for child in owner.children] == ["strata", "gateway"]
        finally:
            await owner.aclose()
        assert stopped == ["gateway", "strata"]

    asyncio.run(exercise())
    assert not list(config.runtime_root.glob("strata-*" + "/"))
    assert "STRATA_API_KEY" not in (config.runtime_root / "strata-process.json").read_text()
