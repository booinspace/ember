"""Explicit receipt creation verifies existing assets without downloading or overwriting."""

import hashlib
import importlib
import importlib.metadata
import json
import os
import runpy
import sysconfig
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def installed_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    module = importlib.import_module("prepare_vllm_profile")
    model_bytes = b"synthetic existing GGUF bytes"
    model_sha = hashlib.sha256(model_bytes).hexdigest()
    blob = tmp_path / "existing-ollama-blob"
    blob.write_bytes(model_bytes)
    model = tmp_path / ("sha256-" + model_sha + ".gguf")
    model.write_bytes(model_bytes)
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    metadata_sha = {}
    for name in module.METADATA_SHA256:
        content = ("synthetic " + name).encode()
        (metadata / name).write_bytes(content)
        metadata_sha[name] = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(module, "MODEL_SHA256", model_sha)
    monkeypatch.setattr(module, "METADATA_SHA256", metadata_sha)
    site = tmp_path / "installed"
    plugin = site / "vllm_gguf_plugin"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("# synthetic plugin\n")
    (plugin / "kernels.so").write_bytes(b"synthetic installed CUDA extension")
    cuda_home = tmp_path / "cuda"
    cuda_home.mkdir()
    cc, cxx = tmp_path / "gcc-13", tmp_path / "g++-13"
    cc.write_bytes(b"synthetic compiler")
    cxx.write_bytes(b"synthetic C++ compiler")
    versions = {**module._VERSIONS, "synthetic-extra": "4.5.6"}
    distributions = [
        SimpleNamespace(metadata={"Name": name}, version=version)
        for name, version in versions.items()
    ]
    wheel = SimpleNamespace(
        files=[
            importlib.metadata.PackagePath("vllm_gguf_plugin/__init__.py"),
            importlib.metadata.PackagePath("vllm_gguf_plugin/kernels.so"),
        ],
        locate_file=lambda entry: site / str(entry),
    )
    monkeypatch.setattr(importlib.metadata, "distributions", lambda **kwargs: distributions)
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: wheel)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: versions[name])
    monkeypatch.setenv("EMBER_VLLM_TOKEN", "private-credential-never-in-receipt")
    return SimpleNamespace(
        module=module,
        scripts=scripts,
        model=model,
        blob=blob,
        metadata=metadata,
        cuda_home=cuda_home,
        cc=cc,
        cxx=cxx,
        output=tmp_path / "profile.json",
        wheel=wheel,
        versions=versions,
        distributions=distributions,
    )


def _prepare(environment: SimpleNamespace, **updates: object) -> str:
    return environment.module.prepare_profile(
        environment.model,
        environment.metadata,
        environment.output,
        cuda_home=environment.cuda_home,
        cc=environment.cc,
        cxx=environment.cxx,
        **updates,
    )


def test_receipt_pins_existing_assets_and_all_software_without_copying(
    installed_profile: SimpleNamespace,
) -> None:
    environment = installed_profile
    original_blob = environment.blob.read_bytes()
    before = set(environment.output.parent.rglob("*"))
    digest = _prepare(environment)
    profile_module = importlib.import_module("vllm_profile")
    profile, verified = profile_module.load_profile(environment.output)
    assert digest == verified
    assert profile.argv[1] == str(environment.model)
    assert environment.model.read_bytes() == original_blob
    assert environment.blob.read_bytes() == original_blob
    assert set(environment.output.parent.rglob("*")) - before == {environment.output}
    assert profile.argv[profile.argv.index("--dtype") + 1] == "bfloat16"
    assert profile.argv[profile.argv.index("--max-model-len") + 1] == "131072"
    assert profile.argv[profile.argv.index("--max-num-batched-tokens") + 1] == "512"
    assert profile.argv[profile.argv.index("--gpu-memory-utilization") + 1] == "0.90"
    assert (
        profile.argv[profile.argv.index("--middleware") + 1]
        == "vllm_gateway_guard.GatewayOnlyMiddleware"
    )
    assert profile.environment["OMP_NUM_THREADS"] == "1"
    assert profile.environment["CUDA_HOME"] == str(environment.cuda_home)
    assert profile.software["synthetic-extra"] == "4.5.6"
    assert profile.software["python"].startswith("3.12.")
    assert len(profile.files) == 17  # model, five metadata, two plugin, seven source, two compiler.
    assert "private-credential" not in environment.output.read_text()
    if os.name != "nt":
        assert environment.output.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="Qualified Linux runner uses POSIX symlinks.")
def test_content_addressed_symlink_is_preserved(installed_profile: SimpleNamespace) -> None:
    environment = installed_profile
    environment.model.unlink()
    environment.model.symlink_to(environment.blob)
    _prepare(environment)
    profile = json.loads(environment.output.read_text())
    assert environment.model.is_symlink()
    assert profile["argv"][1] == str(environment.model)
    assert str(environment.model) in profile["files"]


@pytest.mark.parametrize(
    "fault",
    [
        "model_bytes",
        "model_name",
        "metadata",
        "missing_metadata",
        "extra_metadata_file",
        "extra_metadata_directory",
        "version",
        "plugin_path",
        "missing_cuda_extension",
        "context",
    ],
)
def test_unqualified_inputs_leave_no_receipt(
    installed_profile: SimpleNamespace, fault: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = installed_profile
    updates = {}
    if fault == "model_bytes":
        environment.model.write_bytes(b"changed")
    elif fault == "model_name":
        environment.model = environment.blob
    elif fault == "metadata":
        (environment.metadata / "config.json").write_text("changed")
    elif fault == "missing_metadata":
        (environment.metadata / "tokenizer.json").unlink()
    elif fault == "extra_metadata_file":
        (environment.metadata / "special_tokens_map.json").write_text("{}")
    elif fault == "extra_metadata_directory":
        (environment.metadata / ".cache").mkdir()
    elif fault == "version":
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "unqualified")
    elif fault == "plugin_path":
        environment.wheel.files.append(
            importlib.metadata.PackagePath("vllm_gguf_plugin/../../escape.py")
        )
    elif fault == "missing_cuda_extension":
        environment.wheel.files.pop()
    else:
        updates["context_tokens"] = 131073
    with pytest.raises((ValueError, FileNotFoundError)):
        _prepare(environment, **updates)
    assert not environment.output.exists()


def test_existing_receipt_is_never_overwritten(installed_profile: SimpleNamespace) -> None:
    environment = installed_profile
    environment.output.write_text("operator receipt")
    with pytest.raises(FileExistsError):
        _prepare(environment)
    assert environment.output.read_text() == "operator receipt"


def test_cli_uses_explicit_paths_and_context(
    installed_profile: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment = installed_profile
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_vllm_profile",
            "--model",
            str(environment.model),
            "--metadata",
            str(environment.metadata),
            "--output",
            str(environment.output),
            "--cuda-home",
            str(environment.cuda_home),
            "--cc",
            str(environment.cc),
            "--cxx",
            str(environment.cxx),
            "--context-tokens",
            "32768",
        ],
    )
    # Run the public CLI entry point with the same synthetic approved artifacts.
    environment.module.main()
    value = json.loads(environment.output.read_text())
    assert value["argv"][value["argv"].index("--max-model-len") + 1] == "32768"
    assert len(capsys.readouterr().out.strip()) == 64


def test_installed_receipt_detects_later_plugin_change(installed_profile: SimpleNamespace) -> None:
    environment = installed_profile
    _prepare(environment)
    extension = Path(environment.wheel.locate_file(environment.wheel.files[-1]))
    extension.write_bytes(b"changed extension")
    load_profile = runpy.run_path(str(environment.scripts / "vllm_profile.py"))["load_profile"]
    with pytest.raises(ValueError, match="setup input changed"):
        load_profile(environment.output)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_receipt_rejects_metadata_added_after_sealing(
    installed_profile: SimpleNamespace, kind: str
) -> None:
    environment = installed_profile
    _prepare(environment)
    path = environment.metadata / ("special_tokens_map.json" if kind == "file" else ".cache")
    if kind == "file":
        path.write_text("{}")
    else:
        path.mkdir()
    profile_module = importlib.import_module("vllm_profile")
    with pytest.raises(ValueError, match="metadata inventory changed"):
        profile_module.load_profile(environment.output)


def test_receipt_rejects_new_distribution_after_sealing(installed_profile: SimpleNamespace) -> None:
    environment = installed_profile
    _prepare(environment)
    environment.versions["extra-plugin-provider"] = "1.0.0"
    environment.distributions.append(
        SimpleNamespace(metadata={"Name": "extra-plugin-provider"}, version="1.0.0")
    )
    profile_module = importlib.import_module("vllm_profile")
    with pytest.raises(ValueError, match="setup software changed"):
        profile_module.load_profile(environment.output)


def _distribution(root: Path, name: str, version: str) -> None:
    metadata = root / (name + "-" + version + ".dist-info")
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: " + name + "\nVersion: " + version + "\n"
    )


def test_installed_inventory_ignores_conflicting_vendor_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    module = importlib.import_module("vllm_profile")
    site, vendor = tmp_path / "site-packages", tmp_path / "setuptools/_vendor"
    _distribution(site, "packaging", "26.3")
    _distribution(vendor, "packaging", "26.0")
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(site))
    monkeypatch.syspath_prepend(str(vendor))
    assert module.installed_software() == {
        "python": module.platform.python_version(),
        "packaging": "26.3",
    }


def test_installed_inventory_enumerates_duplicate_install_roots_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    module = importlib.import_module("vllm_profile")
    _distribution(tmp_path, "synthetic", "1.0")
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(tmp_path / "."))
    original = importlib.metadata.distributions
    enumerated = []

    def distributions(**kwargs: object) -> object:
        enumerated.append(kwargs["path"])
        return original(**kwargs)

    monkeypatch.setattr(importlib.metadata, "distributions", distributions)
    assert module.installed_software()["synthetic"] == "1.0"
    assert enumerated == [[str(tmp_path.resolve())]]


def test_added_distribution_in_install_root_invalidates_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    module = importlib.import_module("vllm_profile")
    site = tmp_path / "site-packages"
    _distribution(site, "synthetic", "1.0")
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(site))
    asset = tmp_path / "asset"
    asset.write_bytes(b"synthetic")
    profile = module.SetupProfile(
        argv=["serve"],
        software=module.installed_software(),
        files={str(asset): hashlib.sha256(asset.read_bytes()).hexdigest()},
    )
    receipt = tmp_path / "profile.json"
    receipt.write_text(profile.model_dump_json())
    module.load_profile(receipt)
    _distribution(site, "new-provider", "2.0")
    with pytest.raises(ValueError, match="setup software changed"):
        module.load_profile(receipt)


def test_genuine_duplicate_installed_metadata_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    module = importlib.import_module("vllm_profile")
    _distribution(tmp_path, "synthetic", "1.0")
    _distribution(tmp_path, "synthetic", "2.0")
    monkeypatch.setattr(sysconfig, "get_path", lambda name: str(tmp_path))
    with pytest.raises(ValueError, match="missing or duplicated"):
        module.installed_software()
