"""Portable skill/data boundaries, explicit authorization and provider-neutral discovery."""

import asyncio
import base64
import importlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from ember_agent.runtime.skills import Skills
from ember_agent.runtime.skills_contracts import SkillBundle, SkillCatalog, SkillSelection


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, object]:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts/hermes"))
    module = importlib.import_module("hermes_skills")
    native = SimpleNamespace(
        parse_frontmatter=lambda text: (yaml.safe_load(text.split("\n---\n", 1)[0][4:]), ""),
        skill_matches_platform=lambda metadata: "macos" in metadata.get("platforms", ["macos"]),
        iter_skill_index_files=lambda root, name: root.rglob(name),
    )
    actual_import = importlib.import_module
    monkeypatch.setattr(
        module,
        "importlib",
        SimpleNamespace(
            import_module=lambda name: (
                native
                if name == "agent.skill_utils"
                else SimpleNamespace(safe_load=yaml.safe_load)
                if name == "hermes_yaml"
                else SimpleNamespace(YAML=lambda **kw: SimpleNamespace(scan=yaml.scan))
                if name == "ruamel.yaml"
                else SimpleNamespace(YAMLError=yaml.YAMLError)
                if name == "ruamel.yaml.error"
                else actual_import(name)
            )
        ),
    )
    source = tmp_path / "sdk"
    (source / "skills").mkdir(parents=True)
    root = tmp_path / "shared"
    return module, module.HermesSkills(source, root, ["searx_search", "native.inspect_text"])


def package(name: str = "brief", extra: str = "", body: str = "A complete procedure.") -> dict:
    text = (
        f"---\nname: {name}\ndescription: Make a concise brief.\n"
        f"version: 1.0.0\n{extra}---\n{body}\n"
    )
    return {
        "name": name,
        "files": {
            "SKILL.md": base64.b64encode(text.encode()).decode(),
            "references/offline.md": base64.b64encode(b"Offline reference.").decode(),
        },
    }


def select(repo: object, identity: str = "shared/brief", enabled: bool = True) -> dict:
    catalog = SkillCatalog.model_validate_json(json.dumps(repo.catalog()))
    item = next(item for item in catalog.skills if item.id == identity)
    return repo.select(
        {
            "id": identity,
            "content_sha256": item.content_sha256,
            "revision": catalog.revision,
            "enabled": enabled,
        }
    )


def test_portable_package_original_bytes_extensions_and_resources_roundtrip(
    repository: tuple[ModuleType, object],
) -> None:
    _, repo = repository
    value = package(
        extra=(
            "metadata:\n  openclaw:\n    custom: [one, two]\n  other_harness:\n    unknown: true\n"
        )
    )
    repo.install(value)
    document = repo.document("shared/brief")
    assert document["metadata"]["metadata"]["openclaw"]["custom"] == ["one", "two"]
    assert document["extensions"]["other_harness"]["unknown"] is True
    assert repo.bundle("shared/brief") == value
    assert {r["path"] for r in document["resources"]} == {"SKILL.md", "references/offline.md"}
    assert select(repo)["skills"][0]["enabled"] is True
    assert repo.check(
        [{"id": "shared/brief", "content_sha256": document["skill"]["content_sha256"]}]
    )
    select(repo, enabled=False)
    assert not repo.check(
        [{"id": "shared/brief", "content_sha256": document["skill"]["content_sha256"]}]
    )


@pytest.mark.parametrize(
    "member",
    [
        "../escape",
        "/etc/hosts",
        "C:/credential",
        "references/../../escape",
        "references\\escape",
        ".env",
    ],
)
def test_import_rejects_cross_platform_path_escape_and_cleans_only_owned_package(
    repository: tuple[ModuleType, object], member: str
) -> None:
    _, repo = repository
    value = package()
    value["files"][member] = base64.b64encode(b"bad").decode()
    with pytest.raises(ValueError):
        repo.install(value)
    assert not (repo.root / "packages/brief").exists()
    repo.install(package())
    with pytest.raises(FileExistsError):
        repo.install(package())
    assert repo.bundle("shared/brief") == package()


def test_aliases_installers_requirements_and_oversized_procedures_are_not_activated(
    repository: tuple[ModuleType, object],
) -> None:
    _, repo = repository
    malicious = package(extra="metadata: &authority {tool: shell}\nother: *authority\n")
    with pytest.raises(ValueError, match="alias_or_tag"):
        repo.install(malicious)
    for name, extra, body in [
        ("installer", "deps: [arbitrary-package]\n", "Run installer"),
        ("ungranted", "allowed-tools: [terminal]\n", "Do work"),
        ("oversized", "", "Mandatory step. " * 1000),
    ]:
        repo.install(package(name, extra, body))
        doc = repo.document("shared/" + name)
        assert doc["skill"]["unavailable"]
        assert doc["markdown"].endswith(body + "\n")
        with pytest.raises(ValueError, match="requirements_unavailable"):
            select(repo, "shared/" + name)


def test_changed_bytes_cas_conflict_link_and_case_collision_fail_closed(
    repository: tuple[ModuleType, object],
) -> None:
    _, repo = repository
    repo.install(package())
    catalog = repo.catalog()
    doc = repo.document("shared/brief")
    select(repo)
    with pytest.raises(ValueError, match="revision_conflict"):
        repo.select(
            {
                "id": "shared/brief",
                "enabled": False,
                "revision": catalog["revision"],
                "content_sha256": doc["skill"]["content_sha256"],
            }
        )
    path = repo.locate("shared/brief") / "references/offline.md"
    path.write_text("changed")
    assert not repo.check(
        [{"id": "shared/brief", "content_sha256": doc["skill"]["content_sha256"]}]
    )
    path.unlink()
    path.symlink_to(repo.root / "enabled.json")
    with pytest.raises(ValueError, match="link"):
        repo.document("shared/brief")
    collision = package("collision")
    collision["files"]["references/OFFLINE.md"] = base64.b64encode(b"collision").decode()
    with pytest.raises(ValueError, match="collision"):
        repo.install(collision)
    assert not (repo.root / "packages/collision").exists()


def test_duplicate_names_have_distinct_exact_source_identities(
    repository: tuple[ModuleType, object],
) -> None:
    _, repo = repository
    repo.install(package("first", body="First instructions"))
    second = package("second", body="Second instructions")
    raw = base64.b64decode(second["files"]["SKILL.md"]).replace(b"name: second", b"name: first")
    second["files"]["SKILL.md"] = base64.b64encode(raw).decode()
    repo.install(second)
    catalog = repo.catalog()
    assert [s["name"] for s in catalog["skills"]] == ["first", "first"]
    assert [s["id"] for s in catalog["skills"]] == ["shared/first", "shared/second"]
    select(repo, "shared/second")
    assert [s["id"] for s in repo.catalog()["skills"] if s["enabled"]] == ["shared/second"]


def test_neutral_skill_service_uses_provider_reported_enablement_without_sdk_imports(
    repository: tuple[ModuleType, object],
) -> None:
    _, repo = repository
    repo.install(package())

    class Provider:
        async def call(self, action: str, value: object) -> object:
            return repo.catalog() if action == "list" else repo.select(value)

    async def run() -> None:
        service = Skills("openclaw-fixture", Provider())
        assert not (await service.refresh()).skills[0].enabled
        entry = service.catalog.skills[0]
        await service.select(
            SkillSelection(
                id=entry.id,
                content_sha256=entry.content_sha256,
                enabled=True,
                revision=service.catalog.revision,
            )
        )
        assert service.metadata()[0]["id"] == entry.id

    asyncio.run(run())


def test_operator_bundle_limit_precedes_provider_dispatch() -> None:
    with pytest.raises(ValidationError):
        SkillBundle(name="too-large", files={"SKILL.md": "A" * 200000})


def test_public_page_continuation_reserves_the_bounded_receipt_without_raising_guard() -> None:
    from ember_agent.runtime.hermes_tasks import broker_continuation_fits

    assert broker_continuation_fits(8192, "web.read_page", 16384)
    assert not broker_continuation_fits(8700, "web.read_page", 16384)
    assert not broker_continuation_fits(8192, "mcp.server.tool", 16384)


def test_enabled_skill_tool_schemas_are_supported_by_the_gateway() -> None:
    from ember_agent.inference.contracts import ToolDefinition, ToolFunction
    from ember_agent.inference.openai_bridge import validate_model_schema
    from ember_agent.runtime.hermes_tasks import available_model_tools

    advertised = tuple(
        ToolDefinition(function=ToolFunction(name=name, parameters={}))
        for name in ("ember_skill_view", "ember_skills_list", "ember_finish")
    )
    tools = available_model_tools(
        advertised,
        search_remaining=0,
        recall_available=False,
        evidence_available=True,
        citation_ids=(),
        skill_catalog=({"id": "shared/text-brief"},),
    )
    assert len(tools) == 3
    for tool in tools:
        validate_model_schema(tool.function.parameters)
