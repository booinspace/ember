"""Worker protocol and namespace construction never grant host filesystem or network access."""

import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts" / "hermes"))
    return importlib.import_module("hermes_worker")


def test_callbacks_require_matching_reply_and_preserve_broker_denial(worker: ModuleType) -> None:
    output = io.StringIO()
    rpc = worker.RPC(
        io.StringIO('{"type":"reply","protocol":1,"id":1,"ok":true,"body":{"ok":true}}\n'), output
    )
    assert rpc.request("tool", name="searx_search", arguments={"query": "public"}) == {"ok": True}
    request = json.loads(output.getvalue())
    assert request["type"] == "tool" and request["id"] == 1
    for reply in [
        '{"type":"reply","protocol":1,"id":2,"ok":true}\n',
        '{"type":"reply","protocol":1,"id":1,"ok":false}\n',
        '{"type":"reply","protocol":1,"id":true,"ok":true}\n',
        '{"type":"reply","protocol":true,"id":1,"ok":true}\n',
        "",
    ]:
        with pytest.raises(ValueError):
            worker.RPC(io.StringIO(reply), io.StringIO()).request("model", path="/v1/models")


def test_buffered_sdk_stream_preserves_tool_identity_and_terminal_usage(worker: ModuleType) -> None:
    value = {
        "id": "owned",
        "model": "pinned",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call1",
                            "type": "function",
                            "function": {"name": "ember_finish", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"total_tokens": 4},
    }
    frames = (
        importlib.import_module("hermes_transport").completion_sse(value).decode().split("\n\n")
    )
    first, usage = (json.loads(frame.removeprefix("data: ")) for frame in frames[:2])
    call = first["choices"][0]["delta"]["tool_calls"][0]
    assert call["id"] == "call1" and call["index"] == 0
    assert first["choices"][0]["finish_reason"] == "tool_calls"
    assert usage["choices"] == [] and usage["usage"]["total_tokens"] == 4
    assert frames[2] == "data: [DONE]"


@pytest.mark.skipif(sys.platform == "win32", reason="This checks the Linux namespace command.")
def test_sandbox_has_no_host_mount_or_network_and_uses_tmpfs_home(worker: ModuleType) -> None:
    profile_type = importlib.import_module("hermes_setup").HermesProfile
    profile = profile_type(
        source="/opt/worker",
        python="/opt/worker/.venv/bin/python",
        launcher_python=sys.executable,
        launcher_version="3.12.0",
        git="/usr/bin/git",
        software={"python": "3.14.4"},
        files={"/opt/worker/run_agent.py": "a" * 64},
    )
    command = worker.sandbox_command(profile, "b" * 64)
    assert "--unshare-all" in command and "--clearenv" in command
    assert "--die-with-parent" in command and "--cap-drop" in command
    mounts = [
        command[index + 1 : index + 3] for index, flag in enumerate(command) if flag == "--ro-bind"
    ]
    assert ["/usr", "/usr"] in mounts and ["/opt/worker", "/opt/worker"] in mounts
    assert all(pair[0] not in {"/", "/home", "/mnt", "/mnt/c", "/etc"} for pair in mounts)
    assert command[command.index("--tmpfs") + 1] == "/opt/worker/.git"
    assert "/task/home" in command and "OPENAI_API_KEY" not in command


@pytest.mark.parametrize(
    "change",
    [
        {"context": ["private"]},
        {"context_tokens": 63999},
        {"allowed_tools": ["terminal", "ember_finish"]},
    ],
)
def test_parent_scope_rejects_unsupported_context_and_tools(
    worker: ModuleType, change: dict
) -> None:
    fields = dict(
        type="start",
        protocol=1,
        goal="Public research",
        context=[],
        allowed_tools=["searx_search", "ember_finish"],
        model="pinned",
        context_tokens=131072,
        max_iterations=3,
        max_output_tokens=128,
        timeout_seconds=30,
    )
    fields.update(change)
    with pytest.raises(ValidationError):
        worker.Start.model_validate(fields)


@pytest.mark.skipif(sys.platform == "win32", reason="The qualified launcher uses Linux selectors.")
def test_outer_relay_reaps_natural_exit_with_parent_input_still_open(worker: ModuleType) -> None:
    read, write = os.pipe()
    try:
        with os.fdopen(read, "rb") as source:
            process = subprocess.Popen(
                [sys.executable, "-c", "print('finished', flush=True)"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            )
            output = io.BytesIO()
            assert worker.relay(process, source, output) == 0
            assert process.poll() == 0 and output.getvalue() == b"finished\n"
    finally:
        os.close(write)


@pytest.mark.skipif(sys.platform == "win32", reason="The qualified launcher uses Linux selectors.")
def test_outer_relay_eof_terminates_and_reaps_owned_child(worker: ModuleType) -> None:
    read, write = os.pipe()
    os.close(write)
    with os.fdopen(read, "rb") as source:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        assert worker.relay(process, source, io.BytesIO()) != 0
        assert process.poll() is not None


def test_failed_retained_job_wait_stays_unconfirmed_after_handle_close(worker: ModuleType) -> None:
    native = importlib.import_module("hermes_windows")

    class FailedJob:
        handle = 123
        calls = 0

        def terminate_and_wait(self, timeout: float = 3.0) -> None:
            self.calls += 1
            self.handle = None
            raise OSError("Owned wait failed after closing its handle")

    job = FailedJob()
    reaping = native.Reaping(job)
    with pytest.raises(OSError):
        reaping.wait()
    assert not reaping.confirmed and not reaping.wait()
    assert job.calls == 1


@pytest.mark.parametrize("name", ["../other", "a" * 31, "A" * 32, "a" * 32 + "\n"])
def test_recovery_identity_cannot_select_other_objects(worker: ModuleType, name: str) -> None:
    with pytest.raises(ValueError):
        importlib.import_module("hermes_windows").job_name(name)


@pytest.mark.parametrize(
    "asset", ["hermes_transport.py", "hermes_windows.py", "hermes_skills.py", "hermes_web.py"]
)
@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_receipt_requires_all_executable_boundary_assets(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    asset: str,
    mutation: str,
) -> None:
    setup = importlib.import_module("hermes_setup")
    paths = [
        tmp_path / name
        for name in [
            "hermes_setup.py",
            "hermes_worker.py",
            "hermes_transport.py",
            "hermes_windows.py",
            "hermes_skills.py",
            "hermes_web.py",
        ]
    ]
    for path in paths:
        path.write_text("qualified")
    software = {"python": "3.14.4"}
    profile = setup.HermesProfile(
        source=str(tmp_path),
        python=str(tmp_path / "python"),
        launcher_python=sys.executable,
        launcher_version=setup.platform.python_version(),
        git="git",
        software=software,
        files={str(path): setup.fingerprint(path) for path in paths},
    )
    if mutation == "missing":
        profile.files.pop(str(tmp_path / asset))
    else:
        (tmp_path / asset).write_text("unqualified")
    receipt = tmp_path / "profile.json"
    receipt.write_text(profile.model_dump_json())
    monkeypatch.setattr(setup, "inventory", lambda *_: software)
    monkeypatch.setattr(setup, "tracked_files", lambda *_: [])
    monkeypatch.setattr(setup, "control_sources", lambda *_: paths)
    monkeypatch.setattr(setup, "runtime_assets", lambda *_: [])
    with pytest.raises(ValueError, match="changed"):
        setup.load_profile(receipt)


@pytest.mark.parametrize(
    "extra",
    [
        "namespace/optional.py",
        "namespace/__init__.py",
        "new.pyi",
        "native.pyd",
        "native.dll",
        "native.so",
        "hook.pth",
        "optional-config.json",
        "__pycache__/new.cpython-314.pyc",
        "legacy.pyo",
    ],
)
def test_receipt_rejects_added_optional_modules_and_configuration(
    worker: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra: str
) -> None:
    setup = importlib.import_module("hermes_setup")
    source = tmp_path / "source"
    source.mkdir()
    original = source / "known.py"
    original.write_text("qualified")
    software = {"python": "3.14.4"}
    profile = setup.HermesProfile(
        source=str(source),
        python=str(tmp_path / "python"),
        launcher_python=sys.executable,
        launcher_version=setup.platform.python_version(),
        git="git",
        software=software,
        files={str(original): setup.fingerprint(original)},
    )
    receipt = tmp_path / "profile.json"
    receipt.write_text(profile.model_dump_json())
    monkeypatch.setattr(setup, "inventory", lambda *_: software)
    monkeypatch.setattr(setup, "tracked_files", lambda *_: [original])
    monkeypatch.setattr(setup, "control_sources", lambda *_: [])
    monkeypatch.setattr(setup, "runtime_assets", lambda *_: [])
    setup.load_profile(receipt)
    addition = source / extra
    addition.parent.mkdir(parents=True, exist_ok=True)
    addition.write_text("unqualified")
    with pytest.raises(ValueError, match="inventory changed"):
        setup.load_profile(receipt)


@pytest.mark.parametrize("tree", ["base", "venv"])
@pytest.mark.parametrize("directory", [False, True])
def test_runtime_inventory_rejects_nested_links_to_unqualified_code(
    worker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tree: str,
    directory: bool,
) -> None:
    setup = importlib.import_module("hermes_setup")
    base, venv = tmp_path / "base", tmp_path / "venv"
    base.mkdir()
    (venv / "Scripts").mkdir(parents=True)
    python = venv / "Scripts/python.exe"
    python.write_bytes(b"qualified")
    target = tmp_path / "unqualified"
    target.mkdir()
    (target / "module.py").write_text("unqualified")
    link = (base if tree == "base" else venv) / "optional"
    if sys.platform == "win32" and directory:
        subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        try:
            link.symlink_to(
                target if directory else target / "module.py", target_is_directory=directory
            )
        except OSError as error:
            pytest.skip("The host does not permit symbolic-link creation: " + str(error.winerror))
    profile = setup.HermesProfile(
        source=str(tmp_path),
        python=str(python),
        launcher_python=sys.executable,
        launcher_version=setup.platform.python_version(),
        git="git",
        software={"base_prefix": str(base)},
        files={str(python): "a" * 64},
    )
    monkeypatch.setattr(setup, "os", SimpleNamespace(name="nt", scandir=os.scandir))
    with pytest.raises(ValueError, match="redirects outside"):
        setup.runtime_assets(profile)


@pytest.mark.skipif(sys.platform == "win32", reason="Linux installed dependency inventory.")
@pytest.mark.parametrize("mutation", ["changed", "added", "redirected"])
def test_linux_receipt_seals_installed_dependency_bytes(
    worker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    setup = importlib.import_module("hermes_setup")
    prefix = tmp_path / "venv"
    packages = prefix / "lib/python3.14/site-packages"
    packages.mkdir(parents=True)
    dependency = packages / "dependency.py"
    dependency.write_text("qualified")
    (prefix / "pyvenv.cfg").write_text("home = /usr/bin")
    profile = setup.HermesProfile(
        source=str(tmp_path / "source"),
        python=str(prefix / "bin/python"),
        launcher_python=sys.executable,
        launcher_version=setup.platform.python_version(),
        git="/usr/bin/git",
        software={"python": "3.14.4"},
        files={str(dependency): setup.fingerprint(dependency)},
    )
    assets = setup.runtime_assets(profile)
    profile = profile.model_copy(
        update={"files": {str(path): setup.fingerprint(path) for path in assets}}
    )
    receipt = tmp_path / "profile.json"
    receipt.write_text(profile.model_dump_json())
    monkeypatch.setattr(setup, "inventory", lambda *_: profile.software)
    monkeypatch.setattr(setup, "tracked_files", lambda *_: [])
    monkeypatch.setattr(setup, "installed_source_files", lambda *_: [])
    monkeypatch.setattr(setup, "control_sources", lambda *_: [])
    assert setup.load_profile(receipt)
    if mutation == "changed":
        dependency.write_text("unqualified")
    elif mutation == "added":
        (packages / "optional.py").write_text("unqualified")
    else:
        dependency.unlink()
        dependency.symlink_to(receipt)
    with pytest.raises(ValueError, match="changed|redirects outside"):
        setup.load_profile(receipt)
