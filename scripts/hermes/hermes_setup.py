"""Seal an explicitly installed external Hermes environment; never install at startup."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

REVISION = "71736c9f32597bbca9d89cede5aa136e55946b2f"
_INVENTORY = """
import importlib.metadata,json,platform,sys,sysconfig
roots=sorted({sysconfig.get_path(k) for k in ('purelib','platlib')})
packages={}
for d in importlib.metadata.distributions(path=roots):
 name=d.metadata['Name']
 if not name or name in packages: raise ValueError('ambiguous installed metadata')
 packages[name]=d.version
print(json.dumps({'python':platform.python_version(),'packages':packages,'prefix':sys.prefix,
 'base_prefix':sys.base_prefix}))
"""


class HermesProfile(BaseModel):
    """Verified installation inputs; task context and credentials never enter this receipt."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: str = "ember-hermes-worker-v1"
    revision: str = REVISION
    source: str
    python: str
    launcher_python: str
    launcher_version: str
    git: str
    skills_root: str | None = None
    python_flags: list[str] = Field(default_factory=lambda: ["-B"])
    software: dict[str, JsonValue] = Field(min_length=1)
    files: dict[str, str] = Field(min_length=1)


def fingerprint(path: Path) -> str:
    """Hash an existing installation asset without following any model-generation path."""
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def inventory(python: Path) -> dict[str, JsonValue]:
    """Inspect only installed package metadata using the explicitly selected interpreter."""
    output = subprocess.check_output(
        [str(python), "-I", "-B", "-c", _INVENTORY],
        env={"PATH": "/usr/bin", "HOME": "/nonexistent", "PYTHONNOUSERSITE": "1"},
        timeout=30,
    )
    value = TypeAdapter(dict[str, JsonValue]).validate_json(output)
    if not str(value.get("python", "")).startswith("3.14."):
        raise ValueError("The pinned Hermes worker requires its separate Python 3.14 environment.")
    if os.name != "nt" and value.get("base_prefix") != "/usr":
        raise ValueError("This qualified Linux profile requires the system Python interpreter.")
    return value


def tracked_files(source: Path, git: str = "git") -> list[Path]:
    """Enumerate the pinned public checkout, excluding untracked runtime state."""
    revision = subprocess.check_output(
        [git, "-C", str(source), "rev-parse", "HEAD"], timeout=10, text=True
    ).strip()
    if revision != REVISION or (source / ".env").exists():
        raise ValueError("Use the pinned clean external checkout without a source dotenv file.")
    subprocess.run(
        [git, "-C", str(source), "diff", "--exit-code", "HEAD"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    names = (
        subprocess.check_output([git, "-C", str(source), "ls-files", "-z"], timeout=10)
        .decode()
        .split("\0")
    )
    paths = [source / name for name in names if name]
    if any(not path.is_relative_to(source) or ".." in path.parts for path in paths):
        raise ValueError("A tracked worker asset escapes its installation.")
    # Resolving every full Windows path repeats native ancestor traversal tens of thousands
    # of times. Reject links at each unique node instead; no asset may redirect outside its root.
    nodes = set(paths)
    for path in paths:
        nodes.update(parent for parent in path.parents if parent.is_relative_to(source))
    groups: dict[Path, set[str]] = {}
    for path in nodes - {source}:
        groups.setdefault(path.parent, set()).add(path.name)
    regular: set[Path] = set()
    for parent in sorted(groups, key=lambda item: len(item.parts)):
        expected = groups[parent]
        with os.scandir(parent) as entries:
            for entry in entries:
                if entry.name not in expected:
                    continue
                expected.remove(entry.name)
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("A tracked worker asset redirects outside its installation.")
                if entry.is_file(follow_symlinks=False):
                    regular.add(parent / entry.name)
        if expected:
            raise ValueError("A tracked worker asset is missing.")
    return [path for path in paths if path in regular]


def bridge_sources() -> list[Path]:
    """Every executable first-party boundary module is a mandatory receipt input."""
    return [
        Path(__file__).resolve().with_name(name)
        for name in (
            "hermes_setup.py",
            "hermes_worker.py",
            "hermes_transport.py",
            "hermes_windows.py",
            "hermes_skills.py",
            "hermes_web.py",
        )
    ]


def installed_files(root: Path, *, exclude: frozenset[str] = frozenset()) -> list[Path]:
    """Inventory a resolved installation tree without following nested links or reparse points."""
    root = root.resolve(strict=True)
    pending: list[Path] = [root]
    files: list[Path] = []
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if directory == root and entry.name in exclude:
                    continue
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError(
                        "An installed worker asset redirects outside its installation."
                    )
                path = directory / entry.name
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
                else:
                    raise ValueError("Unsupported installed worker asset.")
    return files


def installed_source_files(source: Path) -> list[Path]:
    """Seal optional modules and data too; an untracked file can change a pinned import path."""
    return installed_files(source, exclude=frozenset({".git", ".venv"}))


def control_sources(profile: HermesProfile) -> list[Path]:
    """Require the executable interpreter/ownership inputs as well as imported bridge modules."""
    assets = bridge_sources() + [
        Path(profile.python),
        Path(profile.launcher_python),
        Path(profile.git),
    ]
    if os.name == "nt":
        from ember_agent.runtime import config, windows_job

        base = profile.software.get("base_prefix")
        if not isinstance(base, str):
            raise ValueError("Missing worker interpreter root.")
        assets.extend([Path(config.__file__), Path(windows_job.__file__)])
        assets.extend(Path(base).glob("*.exe"))
        assets.extend(Path(base).glob("*.dll"))
        assets.extend((Path(base) / "DLLs").glob("*.dll"))
        assets.extend((Path(base) / "DLLs").glob("*.pyd"))
        assets.append(Path(profile.python).parent.parent / "pyvenv.cfg")
    return assets


def runtime_assets(profile: HermesProfile) -> list[Path]:
    """Seal installed native SDK/interpreter bytes, including bytecode and package data."""
    if os.name != "nt":
        # Linux venv bin/lib64 contain standard interpreter links; inventory actual imports.
        prefix = Path(profile.python).parent.parent
        return [prefix / "pyvenv.cfg", *installed_files(prefix / "lib")]
    base = profile.software.get("base_prefix")
    if not isinstance(base, str):
        raise ValueError("Missing worker interpreter root.")
    roots = [Path(base), Path(profile.python).parent.parent]
    return sorted({path for root in roots for path in installed_files(root)})


def load_profile(path: Path) -> tuple[HermesProfile, str]:
    """Fail closed on installation or bridge changes before creating an isolated worker."""
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Hermes receipt exceeds its bound.")
    profile = HermesProfile.model_validate_json(path.read_bytes())
    if profile.version != "ember-hermes-worker-v1" or profile.revision != REVISION:
        raise ValueError("Unsupported Hermes compatibility profile.")
    source = Path(profile.source)
    if profile.skills_root is not None:
        root = Path(profile.skills_root)
        if (
            not root.is_absolute()
            or root.is_relative_to(source)
            or source.is_relative_to(root)
            or not (root / "packages").is_dir()
            or any(node.is_symlink() for node in (root / "packages", *(root / "packages").parents))
            or (root / "enabled.json").is_symlink()
        ):
            raise ValueError("Unsafe shared skills repository.")
    if (
        Path(profile.launcher_python).absolute() != Path(sys.executable).absolute()
        or profile.launcher_version != platform.python_version()
        or profile.python_flags != ["-B"]
    ):
        raise ValueError("Hermes launcher interpreter or flags changed.")
    if not source.is_absolute() or inventory(Path(profile.python)) != profile.software:
        raise ValueError("Hermes installed software changed.")
    paths = (
        tracked_files(source, profile.git)
        + installed_source_files(source)
        + control_sources(profile)
        + runtime_assets(profile)
    )
    if any(str(asset) not in profile.files for asset in paths):
        raise ValueError("Hermes checkout inventory changed.")
    for name, expected in profile.files.items():
        asset = Path(name)
        if not asset.is_absolute() or fingerprint(asset) != expected:
            raise ValueError("Hermes worker installation changed.")
    canonical = json.dumps(profile.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    return profile, hashlib.sha256(canonical).hexdigest()


def main() -> None:
    """Create an exclusive receipt after the operator has installed the pinned checkout."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skills-root", type=Path)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if args.output.absolute().is_relative_to(source):
        raise ValueError("Store the receipt outside the installed source inventory.")
    software = inventory(args.python.absolute())
    git = shutil.which("git")
    if git is None:
        raise ValueError("Install Git during explicit setup first.")
    base = software.get("base_prefix")
    if not isinstance(base, str):
        raise ValueError("Missing worker interpreter root.")
    assets = (
        tracked_files(source, git)
        + installed_source_files(source)
        + bridge_sources()
        + [args.python.absolute(), Path(sys.executable).absolute(), Path(git)]
    )
    if os.name == "nt":
        from ember_agent.runtime import config, windows_job

        assets.extend([Path(config.__file__), Path(windows_job.__file__)])
        assets.extend(Path(base).glob("*.exe"))
        assets.extend(Path(base).glob("*.dll"))
        assets.extend((Path(base) / "DLLs").glob("*.dll"))
        assets.extend((Path(base) / "DLLs").glob("*.pyd"))
        assets.append(args.python.parent.parent / "pyvenv.cfg")
    profile = HermesProfile(
        source=str(source),
        skills_root=str(args.skills_root.resolve(strict=True)) if args.skills_root else None,
        python=str(args.python.absolute()),
        launcher_python=str(Path(sys.executable).absolute()),
        launcher_version=platform.python_version(),
        git=git,
        software=software,
        files={str(path): fingerprint(path) for path in set(assets)},
    )
    profile = profile.model_copy(
        update={
            "files": profile.files
            | {str(path): fingerprint(path) for path in runtime_assets(profile)}
        }
    )
    with args.output.open("x", encoding="utf-8") as output:
        output.write(profile.model_dump_json(indent=2) + "\n")
    args.output.chmod(0o600)
    _, digest = load_profile(args.output)
    print(digest)


if __name__ == "__main__":
    main()
