"""Fresh-clone Strata setup does not depend on private deployment files."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ember_agent.runtime.config import load_config
from ember_agent.runtime.strata_runtime import prepare_strata_config


@pytest.mark.parametrize("vision", [False, True])
def test_prepare_strata_on_fresh_checkout(tmp_path: Path, vision: bool) -> None:
    repo = Path(__file__).resolve().parents[2]
    script = tmp_path / "scripts/prepare_strata_profile.py"
    template = tmp_path / "ember-agent/config/deployment.example.toml"
    for path in (script, template):
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / path.relative_to(tmp_path), path)
    server = tmp_path / "server.py"
    engine = tmp_path / "engine.exe"
    model = tmp_path / "test.gguf"
    tokenizer = tmp_path / "pack/tokenizer"
    tokenizer.mkdir(parents=True)
    for path in (
        server,
        engine,
        model,
        *(tokenizer / name for name in ("vocab.json", "merges.txt", "token_type.json")),
    ):
        path.touch()
    vision_fields = {}
    if vision:
        reader = tmp_path / "strata-vision.exe"
        projector = tmp_path / "mmproj-fixture.gguf"
        reader.touch()
        projector.touch()
        vision_fields = {
            "vision": {"exe": str(reader), "mmproj": str(projector), "model": str(model)}
        }
    source = tmp_path / "strata.json"
    source.write_text(
        json.dumps(
            {
                "exe": str(engine),
                "cwd": str(tmp_path),
                "tokenizer": str(tokenizer),
                "model_name": "fixture",
                "mtp": False,
                **vision_fields,
                "args": [
                    "--native",
                    str(model),
                    "--pack",
                    str(tokenizer.parent),
                    "--max-context",
                    "8192",
                ],
            }
        ),
        encoding="utf-8",
    )
    command = [sys.executable, str(script), str(source), sys.executable, str(server)]
    subprocess.run(command, check=True, timeout=20)
    name = "strata-vision" if vision else "strata"
    deployment = tmp_path / f".local/config/deployment.{name}.toml"
    config = load_config(deployment, require_model_pin=True)
    assert not config.inference.coding_vision
    assert config.context_tokens == 131072
    assert config.max_input_tokens == 122880
    assert config.output_reserve_tokens == 8192
    assert config.browser.endpoint == "http://127.0.0.1:8787"
    assert config.inference.strata is not None
    prepare_strata_config(config, config.inference.strata, tmp_path / "verified.json")
    old = deployment.read_bytes()
    assert subprocess.run(command, check=False, timeout=20).returncode != 0
    assert deployment.read_bytes() == old
