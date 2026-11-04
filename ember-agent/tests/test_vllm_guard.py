"""The optional runner's dev metadata must not expose its administrative endpoints."""

import asyncio
import hashlib
import json
import runpy
from pathlib import Path

import httpx
import pytest
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send


def test_runner_guard_authenticates_metadata_and_denies_admin_even_with_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts/vllm_gateway_guard.py"
    monkeypatch.syspath_prepend(str(script.parent))
    guard = runpy.run_path(str(script))["GatewayOnlyMiddleware"]
    monkeypatch.setattr("sys.argv", ["vllm", "serve"])
    asset = tmp_path / "synthetic-config.json"
    asset.write_text("synthetic")
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "argv": ["serve"],
                "files": {str(asset): hashlib.sha256(asset.read_bytes()).hexdigest()},
                "software": runpy.run_path(str(script.parent / "vllm_profile.py"))[
                    "installed_software"
                ](),
            }
        )
    )
    monkeypatch.setenv("EMBER_VLLM_PROFILE", str(profile))
    for key, value in runpy.run_path(str(script.parent / "vllm_profile.py"))[
        "QUALIFIED_ENVIRONMENT"
    ].items():
        monkeypatch.setenv(key, value)

    async def backend(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse({"reached": True})(scope, receive, send)

    monkeypatch.delenv("EMBER_VLLM_TOKEN", raising=False)
    with pytest.raises(ValueError, match="dedicated random"):
        guard(backend)
    monkeypatch.setenv("EMBER_VLLM_TOKEN", "a" * 64)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=guard(backend)), base_url="http://runner"
        ) as client:
            for path in ["/server_info", "/version", "/v1/models"]:
                assert (await client.get(path)).status_code == 401
            assert (
                await client.get("/server_info", headers=[(b"Authorization", b"Bearer \xff")])
            ).status_code == 401
            client.headers["Authorization"] = "Bearer " + "a" * 64
            for path in ["/server_info", "/version", "/v1/models", "/health"]:
                assert (await client.get(path)).status_code == 200
            receipt = await client.get("/ember/profile")
            assert receipt.status_code == 200 and len(receipt.json()["profile_sha256"]) == 64
            assert (await client.post("/v1/chat/completions", json={})).status_code == 200
            for path in ["/collective_rpc", "/sleep", "/wake_up", "/reset_prefix_cache"]:
                assert (await client.post(path)).status_code == 404
            assert (await client.options("/server_info")).status_code == 404
            assert (
                await client.get(
                    "/server_info",
                    headers=[
                        ("Authorization", "Bearer " + "a" * 64),
                        ("Authorization", "Bearer " + "a" * 64),
                    ],
                )
            ).status_code == 401

    asyncio.run(run())

    asset.write_text("changed")
    with pytest.raises(ValueError, match="setup input changed"):
        guard(backend)


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "changed",
        "duplicate_host",
        "inline_key",
        "no_guard",
        "abbrev_host",
        "abbrev_middleware",
        "underscore_key",
    ],
)
def test_verified_launcher_rejects_changed_inputs_and_unguarded_server_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str
) -> None:
    import sys

    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    content = b"synthetic model bytes"
    asset = tmp_path / ("sha256-" + hashlib.sha256(content).hexdigest() + ".gguf")
    asset.write_bytes(content)
    argv = [
        "serve",
        str(asset),
        "--host",
        "127.0.0.1",
        "--middleware",
        "vllm_gateway_guard.GatewayOnlyMiddleware",
    ]
    if fault == "duplicate_host":
        argv += ["--host", "0.0.0.0"]
    if fault == "inline_key":
        argv += ["--api-key=secret"]
    if fault == "abbrev_host":
        argv += ["--hos", "0.0.0.0"]
    if fault == "abbrev_middleware":
        argv += ["--middlewar", "extra.Middleware"]
    if fault == "underscore_key":
        argv += ["--api_k", "inline-secret"]
    if fault == "no_guard":
        argv[-1] = "unprotected.Middleware"
    profile = tmp_path / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "argv": argv,
                "files": {str(asset): hashlib.sha256(asset.read_bytes()).hexdigest()},
                "software": runpy.run_path(str(scripts / "vllm_profile.py"))[
                    "installed_software"
                ](),
            }
        )
    )
    if fault == "changed":
        asset.write_bytes(b"changed")
    monkeypatch.setenv("EMBER_VLLM_TOKEN", "a" * 64)
    monkeypatch.setattr(sys, "argv", ["run_vllm", "--profile", str(profile)])
    monkeypatch.setenv("VLLM_USE_RUST_FRONTEND", "1")
    monkeypatch.setenv("VLLM_GGUF_USE_CUDA", "0")
    monkeypatch.setenv("VLLM_UNQUALIFIED_PLUGIN", "unexpected")
    calls = []

    def execute(path: Path, arguments: list[str], environment: dict[str, str]) -> None:
        calls.append((path, arguments, environment))

    monkeypatch.setattr("os.execve", execute)
    if fault == "none":
        runpy.run_path(str(scripts / "run_vllm.py"), run_name="__main__")
        assert len(calls) == 1 and calls[0][1][1:] == argv
        assert calls[0][2]["VLLM_SERVER_DEV_MODE"] == "1"
        assert calls[0][2]["VLLM_USE_RUST_FRONTEND"] == "0"
        assert calls[0][2]["VLLM_GGUF_USE_CUDA"] == "1"
        assert "VLLM_UNQUALIFIED_PLUGIN" not in calls[0][2]
        assert calls[0][2]["EMBER_VLLM_PROFILE"] == str(profile)
    else:
        with pytest.raises(ValueError):
            runpy.run_path(str(scripts / "run_vllm.py"), run_name="__main__")
        assert not calls


@pytest.mark.parametrize("failed", [False, True])
def test_pin_inspection_closes_its_metadata_client_without_editing_configuration(
    monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    from types import SimpleNamespace

    from ember_agent.inference import vllm
    from ember_agent.inference.contracts import GatewayError
    from ember_agent.runtime import config

    settings = SimpleNamespace(
        inference=SimpleNamespace(backend="vllm", vllm=object()), model_digest="a" * 64
    )
    closed = []

    class Inspection:
        def __init__(self, value: object) -> None:
            assert value is settings

        async def inspect_identity(self) -> str:
            if failed:
                raise GatewayError("model_artifact_mismatch")
            return "b" * 64

        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr(vllm, "VLLMUpstream", Inspection)
    monkeypatch.setattr(config, "load_config", lambda *args, **kwargs: settings)
    script = Path(__file__).resolve().parents[2] / "scripts/inspect_vllm_pin.py"
    inspect = runpy.run_path(str(script))["inspect"]
    if failed:
        with pytest.raises(GatewayError, match="model_artifact_mismatch"):
            asyncio.run(inspect(Path("synthetic.toml")))
    else:
        assert asyncio.run(inspect(Path("synthetic.toml"))) == "b" * 64
    assert closed == [True] and settings.model_digest == "a" * 64
