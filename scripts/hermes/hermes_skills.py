"""Installed Hermes adapter for one shared portable skill repository and native read path."""

import base64
import hashlib
import importlib
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, cast
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter

OBJECT = TypeAdapter(dict[str, JsonValue])
MAX_PACKAGE = 131072
MAX_PROCEDURE = 8192


class YAMLScanner(Protocol):
    """Narrow the installed native YAML lexer before accepting resource metadata."""

    def scan(self, text: str) -> Iterable[object]: ...


def safe_member(name: str) -> bool:
    """Reject cross-platform traversal, hidden paths and ambiguous package members."""
    path, windows = PurePosixPath(name), PureWindowsPath(name)
    return bool(
        name
        and len(name) <= 256
        and not path.is_absolute()
        and not windows.drive
        and "\\" not in name
        and str(path) == name
        and all(part not in {".", ".."} and not part.startswith(".") for part in path.parts)
    )


def package_files(root: Path) -> dict[str, bytes]:
    """Read bounded ordinary files without following links or traversing support directories."""
    files: dict[str, bytes] = {}
    pending = [root]
    total = 0
    while pending:
        directory = pending.pop()
        if directory.is_symlink():
            raise ValueError("skill_resource_link")
        for path in sorted(directory.iterdir()):
            if not safe_member(name := path.relative_to(root).as_posix()):
                raise ValueError("skill_resource_path")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("skill_resource_link")
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISREG(mode):
                if path.stat().st_size > MAX_PACKAGE:
                    raise ValueError("skill_package_too_large")
                data = path.read_bytes()
                total += len(data)
                if (
                    total > MAX_PACKAGE
                    or len(files) >= 128
                    or name.casefold() in {key.casefold() for key in files}
                ):
                    raise ValueError("skill_resource_limit_or_collision")
                files[name] = data
            else:
                raise ValueError("skill_resource_type")
    if "SKILL.md" not in files:
        raise ValueError("skill_document_missing")
    return files


def package_hash(files: dict[str, bytes]) -> str:
    """Hash paths and exact bytes, independently of filesystem and enumeration order."""
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    return hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode()).hexdigest()


def parse_metadata(text: str) -> dict[str, JsonValue]:
    """Use Hermes' parser after rejecting YAML aliases/tags and its permissive fallback cases."""
    normalized = text.removeprefix("\ufeff").replace("\r\n", "\n")
    if not normalized.startswith("---\n") or "\n---\n" not in normalized[4:]:
        raise ValueError("skill_frontmatter_missing")
    front = normalized.split("\n---\n", 1)[0][4:]
    yaml = importlib.import_module("hermes_yaml")
    factory = cast(Callable[..., YAMLScanner], importlib.import_module("ruamel.yaml").YAML)
    parser = factory(typ="safe", pure=True)
    scan = parser.scan
    parser_error = cast(type[Exception], importlib.import_module("ruamel.yaml.error").YAMLError)
    try:
        tokens = list(scan(front))
        if any(
            type(token).__name__ in {"AliasToken", "AnchorToken", "TagToken"} for token in tokens
        ):
            raise ValueError("skill_frontmatter_alias_or_tag")
        load = cast(Callable[[str], object], yaml.safe_load)
        strict = OBJECT.validate_python(load(front))
    except parser_error as error:
        raise ValueError("skill_frontmatter_invalid") from error
    parse = cast(
        Callable[[str], tuple[object, object]],
        importlib.import_module("agent.skill_utils").parse_frontmatter,
    )
    native, _ = parse(normalized)
    metadata = OBJECT.validate_python(native)
    if metadata != strict:
        raise ValueError("skill_frontmatter_translation")
    if not isinstance(metadata.get("name"), str) or not isinstance(
        metadata.get("description"), str
    ):
        raise ValueError("skill_identity_missing")
    if "version" in metadata and not isinstance(metadata["version"], (str, int, float)):
        raise ValueError("skill_version_invalid")
    if len(str(metadata["name"])) > 128:
        raise ValueError("skill_name_too_long")
    return metadata


def strings(value: JsonValue | None) -> list[str]:
    """Normalize declared requirement lists without treating free-form prose as authority."""
    if isinstance(value, str):
        return [part for part in re.split(r"[\s,]+", value) if part]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [str(item) for item in value]
    return []


def requirements(metadata: dict[str, JsonValue]) -> tuple[list[str], list[str]]:
    """Keep unsupported installation/credential requirements explicit; never satisfy them here."""
    required = strings(metadata.get("allowed-tools"))
    unavailable: list[str] = []
    nested = metadata.get("metadata")
    nested = nested if isinstance(nested, dict) else {}
    for provider in (metadata, *[value for value in nested.values() if isinstance(value, dict)]):
        needs = provider.get("requires")
        needs = needs if isinstance(needs, dict) else {}
        required.extend(strings(needs.get("tools")))
        required.extend(strings(provider.get("requires_tools")))
        if provider.get("requires_toolsets"):
            unavailable.append("skill_toolset_requirements_unavailable")
        if any(needs.get(key) for key in ("bins", "anyBins", "env", "config")):
            unavailable.append("skill_external_requirements")
        if provider.get("deps") or provider.get("install"):
            unavailable.append("skill_installer_not_allowed")
    if any(
        metadata.get(key)
        for key in (
            "deps",
            "required_environment_variables",
            "required_credential_files",
            "requires_apps",
            "setup",
            "prerequisites",
        )
    ):
        unavailable.append("skill_installer_or_credentials_not_allowed")
    return sorted(set(required)), sorted(set(unavailable))


class HermesSkills:
    """One external shared repository; discovery/enablement do not run a model or a skill."""

    def __init__(self, source: Path, root: Path, capabilities: list[str]) -> None:
        self.source = source
        self.root = root
        if not self.root.is_absolute() or self.root.is_symlink():
            raise ValueError("skills_repository_invalid")
        self.capabilities = set(capabilities)
        sys.path.insert(0, str(source))

    def state(self) -> tuple[dict[str, JsonValue], str]:
        path = self.root / "enabled.json"
        raw = path.read_bytes() if path.exists() else b'{"enabled":{}}'
        if len(raw) > 8192 or path.is_symlink():
            raise ValueError("skills_state_invalid")
        state = OBJECT.validate_json(raw)
        if (
            set(state) != {"enabled"}
            or not isinstance(state["enabled"], dict)
            or len(state["enabled"]) > 8
            or any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                for value in state["enabled"].values()
            )
        ):
            raise ValueError("skills_state_invalid")
        return state, hashlib.sha256(raw).hexdigest()

    def roots(self) -> dict[str, Path]:
        return {
            "bundled": self.source / "skills",
            "optional": self.source / "optional-skills",
            "shared": self.root / "packages",
        }

    def locate(self, identity: str) -> Path:
        source, separator, name = identity.partition("/")
        if not separator or source not in self.roots() or not safe_member(name):
            raise ValueError("skill_identity_invalid")
        root = self.roots()[source]
        path = root / name
        for node in (path, *path.parents):
            if node == root.parent:
                break
            if node.is_symlink():
                raise ValueError("skill_resource_link")
        if not (path / "SKILL.md").is_file():
            raise ValueError("skill_not_found")
        return path

    def document(self, identity: str) -> dict[str, JsonValue]:
        files = package_files(self.locate(identity))
        text = files["SKILL.md"].decode("utf-8")
        if len(files["SKILL.md"]) > 65536:
            raise ValueError("skill_document_too_large")
        metadata = parse_metadata(text)
        required, unavailable = requirements(metadata)
        if len(files["SKILL.md"]) > MAX_PROCEDURE:
            unavailable.append("skill_procedure_exceeds_task_budget")
        matches = cast(
            Callable[[dict[str, JsonValue]], bool],
            importlib.import_module("agent.skill_utils").skill_matches_platform,
        )
        if not matches(metadata):
            unavailable.append("skill_platform_unsupported")
        aliases = {
            "web_search": "searx_search",
            "web_extract": "web.read_page",
            "inspect_text": "native.inspect_text",
            "skill_view": "skills.view",
            "skills_list": "skills.list",
        }
        unavailable.extend(
            "tool_required:" + name
            for name in required
            if aliases.get(name, name) not in self.capabilities
        )
        state, _ = self.state()
        enabled = state["enabled"]
        assert isinstance(enabled, dict)
        digest = package_hash(files)
        if identity in enabled and enabled[identity] != digest:
            unavailable.append("skill_package_changed")
        extensions = metadata.get("metadata")
        skill: dict[str, JsonValue] = {
            "id": identity,
            "name": metadata["name"],
            "description": str(metadata["description"])[:512],
            "version": str(metadata.get("version", "unversioned")),
            "source": identity.split("/")[0],
            "content_sha256": digest,
            "enabled": identity in enabled,
            "requirements": list[JsonValue](required),
            "unavailable": list[JsonValue](sorted(set(unavailable))),
            "diagnostics": [] if "version" in metadata else ["skill_version_unspecified"],
        }
        return {
            "skill": skill,
            "markdown": text,
            "metadata": metadata,
            "extensions": extensions if isinstance(extensions, dict) else {},
            "resources": [
                {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
                for name, data in sorted(files.items())
            ],
        }

    def catalog(self) -> dict[str, JsonValue]:
        summaries: list[JsonValue] = []
        for source, root in self.roots().items():
            if not root.exists():
                continue
            scan = cast(
                Callable[[Path, str], Iterable[Path]],
                importlib.import_module("agent.skill_utils").iter_skill_index_files,
            )
            for path in sorted(scan(root, "SKILL.md")):
                identity = source + "/" + path.parent.relative_to(root).as_posix()
                try:
                    summaries.append(self.document(identity)["skill"])
                except (ValueError, OSError, UnicodeError):
                    summaries.append(
                        {
                            "id": identity,
                            "name": path.parent.name,
                            "description": "Package is unavailable; inspect its source before use.",
                            "version": "unknown",
                            "source": source,
                            "content_sha256": "0" * 64,
                            "enabled": False,
                            "requirements": [],
                            "unavailable": ["skill_package_invalid"],
                            "diagnostics": [],
                        }
                    )
                if len(summaries) > 256:
                    raise ValueError("skills_catalog_too_large")
        _, revision = self.state()
        return {
            "provider": "hermes",
            "revision": revision,
            "skills": summaries,
            "unavailable": None,
        }

    def select(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        identity, expected = value.get("id"), value.get("content_sha256")
        if not isinstance(identity, str) or not isinstance(value.get("enabled"), bool):
            raise ValueError("skills_selection_invalid")
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / ".enable.lock"
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, 0)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state, revision = self.state()
            if value.get("revision") != revision:
                raise ValueError("skills_revision_conflict")
            summary = self.document(identity)["skill"]
            assert isinstance(summary, dict)
            if summary["content_sha256"] != expected:
                raise ValueError("skill_package_changed")
            enabled = state["enabled"]
            assert isinstance(enabled, dict)
            if value["enabled"]:
                if summary["unavailable"]:
                    raise ValueError("skill_requirements_unavailable")
                enabled[identity] = expected
                if len(enabled) > 8:
                    raise ValueError("skills_enabled_limit")
            else:
                enabled.pop(identity, None)
            temporary = self.root / (".enabled-" + uuid4().hex + ".next")
            with temporary.open("x", encoding="utf-8") as output:
                output.write(json.dumps(state, sort_keys=True, separators=(",", ":")))
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.root / "enabled.json")
        finally:
            os.close(descriptor)
        return self.catalog()

    def bundle(self, identity: str) -> dict[str, JsonValue]:
        files = package_files(self.locate(identity))
        return {
            "name": self.locate(identity).name,
            "files": {name: base64.b64encode(data).decode() for name, data in files.items()},
        }

    def install(self, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        name, encoded = value.get("name"), value.get("files")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name):
            raise ValueError("skill_import_name")
        if not isinstance(encoded, dict) or not 1 <= len(encoded) <= 128:
            raise ValueError("skill_import_members")
        if len({member.casefold() for member in encoded}) != len(encoded):
            raise ValueError("skill_resource_collision")
        target = self.root / "packages" / name
        if target.parent.is_symlink():
            raise ValueError("skill_resource_link")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir()  # An existing identity is never overwritten.
        try:
            for member, data in encoded.items():
                if not safe_member(member) or not isinstance(data, str):
                    raise ValueError("skill_resource_path")
                destination = target / member
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(base64.b64decode(data, validate=True))
            self.document("shared/" + name)
        except BaseException:
            shutil.rmtree(target)
            raise
        return self.catalog()

    def check(self, values: list[JsonValue]) -> bool:
        for value in values:
            if not isinstance(value, dict) or not isinstance(identity := value.get("id"), str):
                return False
            document = self.document(identity)
            summary = document["skill"]
            if not isinstance(summary, dict) or not summary["enabled"] or summary["unavailable"]:
                return False
            if summary["content_sha256"] != value.get("content_sha256"):
                return False
        return True

    def native_view(self, identity: str, resource: str | None = None) -> str:
        """Read through Hermes, with preprocessing/installers excluded before its loader runs."""
        document = self.document(identity)
        skill = document["skill"]
        assert isinstance(skill, dict)
        if not skill["enabled"] or skill["unavailable"]:
            raise ValueError("skill_not_enabled_or_available")
        metadata = document["metadata"]
        assert isinstance(metadata, dict)
        if resource is not None and not safe_member(resource):
            raise ValueError("skill_resource_path")
        files = package_files(self.locate(identity))
        member = resource or "SKILL.md"
        if member not in files or len(files[member]) > MAX_PROCEDURE:
            raise ValueError("skill_resource_unavailable_or_too_large")
        module = importlib.import_module("tools.skills_tool")
        # Only the chosen exact package is exposed to Hermes' native locator.
        vars(module)["SKILLS_DIR"] = self.locate(identity).parent
        clear = cast(Callable[[], None], module.clear_skills_cache)
        clear()
        view = cast(Callable[..., str], module.skill_view)
        result = OBJECT.validate_json(
            view(name=self.locate(identity).name, file_path=resource, preprocess=False)
        )
        if result.get("success") is not True or not isinstance(
            content := result.get("content"), str
        ):
            raise ValueError("skill_native_load_failed")
        expected = files[member].decode("utf-8").removeprefix("\ufeff").replace("\r\n", "\n")
        if content.removeprefix("\ufeff").replace("\r\n", "\n") != expected:
            raise ValueError("skill_native_content_changed")
        return content
