"""Enforce dependency ownership without depending on empty scaffold behavior."""

import ast
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_abi_has_no_backend_runtime_or_harness_imports_or_dependencies() -> None:
    project = ROOT / "ember-memory/packages/memory-abi"
    forbidden = {"sqlite3", "livekit", "ollama", "mcp", "odysseus", "ember_memory_engine"}
    for path in (project / "src").rglob("*.py"):
        assert not _imports(path) & forbidden, path
    with (project / "pyproject.toml").open("rb") as stream:
        dependencies = tomllib.load(stream)["project"]["dependencies"]
    # Contract-only dependencies such as Pydantic remain permitted in phase 1.
    forbidden_distributions = {"livekit", "ollama", "mcp", "odysseus", "ember-memory-engine"}
    for dependency in dependencies:
        name = re.split(r"[\[\s<>=!~@]", dependency, maxsplit=1)[0].lower().replace("_", "-")
        assert name not in forbidden_distributions


def test_agent_engine_construction_stays_in_composition_maintenance_or_adapters() -> None:
    source = ROOT / "ember-agent/src/ember_agent"
    for path in source.rglob("*.py"):
        relative = path.relative_to(source).as_posix()
        allowed = relative == "runtime/composition.py" or relative.startswith(
            (
                "maintenance/",
                "memory/backends/",
            )
        )
        if not allowed:
            assert "ember_memory_engine" not in _imports(path), path
