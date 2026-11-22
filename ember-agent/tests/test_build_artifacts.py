"""Build artifacts reject untrusted inputs and publish bounded verified ZIPs."""

import asyncio
import base64
import hashlib
import io
import json
import os
import stat
import subprocess
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import pytest

from ember_agent.runtime import build_artifacts
from ember_agent.runtime.build_artifacts import (
    COLLECT_SCRIPT,
    MAX_COLLECTION_BYTES,
    MAX_DEPTH,
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    MAX_PATH_BYTES,
    MAX_ZIP_BYTES,
    BuildArtifactMetadata,
    BuildArtifacts,
)


def _records(*files: tuple[str, bytes]) -> bytes:
    return json.dumps([[name, base64.b64encode(data).decode()] for name, data in files]).encode()


@pytest.fixture(params=["native", "mock-windows"])
def storage_platform(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.param == "native":
        return
    if os.name == "nt":
        pytest.skip("Windows branch covered without mocks")

    def no_replace(source: Path, destination: Path) -> None:
        # Model Windows rename's no-replace contract with a POSIX exclusive publication.
        os.link(source, destination, follow_symlinks=False)
        source.unlink()

    monkeypatch.setattr(build_artifacts, "_WINDOWS", True)
    monkeypatch.setattr(os, "rename", no_replace)


@pytest.mark.usefixtures("storage_platform")
def test_save_read_and_delete_verified_zip(tmp_path: Path) -> None:
    async def run() -> None:
        storage = BuildArtifacts(tmp_path)
        task_id = uuid4()
        raw = _records(("read me.txt", b"read me"), ("nested/binary.bin", b"\x00\xff"))
        result = await storage.save(task_id, raw)
        data = await storage.read(task_id, result["sha256"], result["size_bytes"])
        assert result == {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "names": ["nested/binary.bin", "read me.txt"],
        }
        with ZipFile(io.BytesIO(data)) as archive:
            assert archive.namelist() == result["names"]
            assert archive.read("nested/binary.bin") == b"\x00\xff"
            assert archive.read("read me.txt") == b"read me"
            assert archive.getinfo("read me.txt").external_attr >> 16 == stat.S_IFREG | 0o644
        with pytest.raises(FileExistsError):
            await storage.save(task_id, _records(("different.txt", b"no")))
        (target,) = storage.root.iterdir()
        assert not target.stat().st_mode & stat.S_IWRITE
        assert target.read_bytes() == data
        with pytest.raises(FileNotFoundError):
            await storage.read(uuid4(), result["sha256"], result["size_bytes"])
        with pytest.raises(ValueError):
            await storage.read(task_id, "0" * 64, result["size_bytes"])
        with pytest.raises(ValueError):
            await storage.read(task_id, result["sha256"], MAX_ZIP_BYTES + 1)
        target.chmod(0o600)
        target.write_bytes(data[:-1] + b"X")
        with pytest.raises(ValueError):
            await storage.read(task_id, result["sha256"], result["size_bytes"])
        await storage.delete(task_id)
        await storage.delete(task_id)
        assert not list(storage.root.iterdir())

    asyncio.run(run())


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/absolute",
        "foo/../bar",
        "foo//bar",
        "foo/./bar",
        "foo/",
        "",
        "C:foo",
        "foo\\bar",
        "foo\x00bar",
        "foo\nbar",
        "NUL.txt",
        "com1 .txt",
        "foo. ",
        "名前.txt",
        "a" * (MAX_PATH_BYTES + 1),
        "x/" * MAX_DEPTH + "too-deep",
    ],
)
def test_untrusted_names_never_publish(tmp_path: Path, name: str) -> None:
    storage = BuildArtifacts(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(storage.save(uuid4(), _records((name, b"data"))))
    assert not storage.root.exists()


@pytest.mark.parametrize("paths", [("same", "same"), ("a", "a/b"), ("a/b", "a"), ("A/b", "a/c")])
def test_conflicting_names_never_publish(tmp_path: Path, paths: tuple[str, str]) -> None:
    storage = BuildArtifacts(tmp_path)
    with pytest.raises(ValueError, match="conflicting_build_artifact_paths"):
        asyncio.run(storage.save(uuid4(), _records(*((name, b"") for name in paths))))
    assert not storage.root.exists()


@pytest.mark.parametrize(
    "raw",
    [b"", b"{}", b"[3]", b'[["ok",42]]', b'[["ok","base64!!"]]', b"[] []", b"\xff"],
)
def test_invalid_records_never_publish(tmp_path: Path, raw: bytes) -> None:
    storage = BuildArtifacts(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(storage.save(uuid4(), raw))
    assert not storage.root.exists()


def test_limits_independently_checked_on_host(tmp_path: Path) -> None:
    storage = BuildArtifacts(tmp_path)
    cases = (
        b" " * (MAX_COLLECTION_BYTES + 1),
        _records(*((str(index), b"") for index in range(MAX_ENTRIES + 1))),
        _records(*((f"{index}/file", b"") for index in range(MAX_ENTRIES // 2 + 1))),
        _records(("a", b"x" * MAX_FILE_BYTES), ("b", b"x")),
    )
    for raw in cases:
        with pytest.raises(ValueError):
            asyncio.run(storage.save(uuid4(), raw))
    with pytest.raises(ValueError):
        asyncio.run(storage.save("../../escape", b"[]"))
    assert not storage.root.exists()


@pytest.mark.skipif(os.name != "posix", reason="Artifact storage uses POSIX dirfds")
def test_no_follow_storage_links_or_read_fifo(tmp_path: Path) -> None:
    storage = BuildArtifacts(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    task_id = uuid4()
    target = storage.root / f"{task_id}.zip"
    storage.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        asyncio.run(storage.save(task_id, b"[]"))
    assert not list(outside.iterdir())
    storage.root.unlink()
    storage.root.mkdir()
    target.symlink_to(outside / "artifact")
    with pytest.raises(OSError):
        asyncio.run(storage.read(task_id, "a" * 64, 22))
    with pytest.raises(FileExistsError):
        asyncio.run(storage.save(task_id, b"[]"))
    asyncio.run(storage.delete(task_id))
    os.mkfifo(target)
    with pytest.raises(ValueError):
        asyncio.run(storage.read(task_id, "a" * 64, 22))
    assert not list(outside.iterdir())


@pytest.mark.parametrize("location", ["runtime", "storage", "entry"])
def test_windows_rejects_reparse_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    storage = BuildArtifacts(tmp_path)
    task_id = uuid4()
    storage.root.mkdir()
    target = storage.root / f"{task_id}.zip"
    target.write_bytes(b"x" * 22)
    blocked = {"runtime": tmp_path, "storage": storage.root, "entry": target}[location]
    real_lstat = Path.lstat

    def reparse_lstat(path: Path) -> os.stat_result | SimpleNamespace:
        info = real_lstat(path)
        if path == blocked:
            return SimpleNamespace(
                st_mode=info.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
            )
        return info

    monkeypatch.setattr(build_artifacts, "_WINDOWS", True)
    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    with pytest.raises(OSError, match="invalid_build_artifact_storage_path"):
        asyncio.run(storage.read(task_id, "a" * 64, 22))
    with pytest.raises(OSError, match="invalid_build_artifact_storage_path"):
        asyncio.run(storage.delete(task_id))
    if location != "entry":
        with pytest.raises(OSError, match="invalid_build_artifact_storage_path"):
            asyncio.run(storage.save(uuid4(), b"[]"))
    assert target.read_bytes() == b"x" * 22


@pytest.mark.usefixtures("storage_platform")
def test_cancelled_save_settles_before_cleanup(tmp_path: Path) -> None:
    task_id = uuid4()
    started = Event()
    release = Event()

    class GatedStorage(BuildArtifacts):
        def _save(self, target: str, raw: bytes) -> BuildArtifactMetadata:
            started.set()
            assert release.wait(2)
            return super()._save(target, raw)

    storage = GatedStorage(tmp_path)

    async def run() -> None:
        pending = asyncio.create_task(storage.save(task_id, b"[]"))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            for _ in range(2):
                pending.cancel()
                await asyncio.sleep(0)
                assert not pending.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert (storage.root / f"{task_id}.zip").is_file()
        await storage.delete(task_id)
        assert not list(storage.root.iterdir())

    asyncio.run(run())


@pytest.mark.integration
def test_linux_collector_in_private_tmpfs(tmp_path: Path) -> None:
    image = os.environ.get("EMBER_BUILD_TEST_IMAGE")
    if not image:
        pytest.skip("set EMBER_BUILD_TEST_IMAGE to an installed Linux image with Python")
    python = os.environ.get("EMBER_BUILD_TEST_PYTHON", "/usr/local/bin/python3")
    driver = """
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

collector, max_bytes, max_entries = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
work = Path('/work')

def run(source=collector):
    process = subprocess.run([sys.executable, '-I', '-S', '-c', source],
                             capture_output=True, timeout=3)
    return process.returncode, process.stdout, process.stderr

def rejected(source=collector):
    code, output, error = run(source)
    assert code != 0 and not output and error == b'build_artifact_collection_failed\\n', (
        code, output, error
    )

assert run() == (0, b'[]', b'')
(work / 'nested').mkdir()
(work / 'nested' / 'bytes.bin').write_bytes(bytes([0, 255, 17]))
code, output, error = run()
assert code == 0 and not error
shutil.rmtree(work / 'nested')
entry = work / 'entry'
entry.symlink_to('/etc/passwd')
rejected()
entry.unlink()
entry.symlink_to('/etc', target_is_directory=True)
rejected()
entry.unlink()
entry.touch()
alias = work / 'alias'
os.link(entry, alias)
rejected()
alias.unlink()
with entry.open('wb') as stream:
    stream.truncate(max_bytes + 1)
rejected()
entry.unlink()
os.mkfifo(entry)
rejected()
entry.unlink()
with socket.socket(socket.AF_UNIX) as listener:
    listener.bind(str(entry))
    rejected()
entry.unlink()

# Make races deterministic: swap a classified regular file or mutate during its read.
entry.write_bytes(b'initial')
line = 'fd = os.open(f"/proc/self/fd/{inode}", READ_FLAGS)'
rejected(collector.replace(line, 'os.unlink(name, dir_fd=directory)\\n'
                            '                        os.mkfifo(name, dir_fd=directory)\\n'
                            '                        ' + line))
entry.unlink()
entry.write_bytes(b'initial')
line = 'content = stream.read(info.st_size + 1)'
rejected(collector.replace(line, line + '\\n                            '
                            'os.truncate("/work/entry", 10)'))
entry.unlink()
for number in range(max_entries + 1):
    (work / str(number)).mkdir()
rejected()
sys.stdout.buffer.write(output)
"""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user=65532:65532",
            "--tmpfs=/work:rw,nodev,nosuid,size=32m,mode=1777",
            "--entrypoint",
            python,
            image,
            "-I",
            "-S",
            "-c",
            driver,
            COLLECT_SCRIPT,
            str(MAX_FILE_BYTES),
            str(MAX_ENTRIES),
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    storage = BuildArtifacts(tmp_path)
    task_id = uuid4()
    metadata = asyncio.run(storage.save(task_id, result.stdout))
    data = asyncio.run(storage.read(task_id, metadata["sha256"], metadata["size_bytes"]))
    with ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["nested/bytes.bin"]
        assert archive.read("nested/bytes.bin") == b"\x00\xff\x11"
