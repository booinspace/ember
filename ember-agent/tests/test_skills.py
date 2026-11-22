"""Portable skill metadata and explicit selection at the provider-neutral boundary."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from ember_agent.runtime.skills import Skills
from ember_agent.runtime.skills_contracts import (
    SkillBundle,
    SkillDocument,
    SkillSelection,
)


def test_neutral_skill_service_dispatches_exact_reviewed_selection() -> None:
    async def run() -> None:
        summary = {
            "id": "shared/brief",
            "name": "brief",
            "description": "A concise brief.",
            "version": "1.0.0",
            "source": "fixture",
            "content_sha256": "a" * 64,
            "enabled": False,
        }
        initial = {"provider": "fixture", "revision": "before", "skills": [summary]}
        provider = AsyncMock()
        provider.call.side_effect = [
            initial,
            {
                **initial,
                "revision": "after",
                "skills": [{**summary, "enabled": True}],
            },
        ]
        service = Skills("fixture", provider)
        (entry,) = (await service.refresh()).skills
        assert not service.enabled
        selection = SkillSelection(
            id=entry.id,
            content_sha256=entry.content_sha256,
            enabled=True,
            revision=service.catalog.revision,
        )
        await service.select(selection)
        provider.call.assert_awaited_with("select", selection.model_dump(mode="json"))
        assert service.metadata()[0]["id"] == "shared/brief"
        assert service.catalog.revision == "after"

    asyncio.run(run())


def test_skill_document_preserves_provider_fields_and_original_markdown() -> None:
    document = SkillDocument.model_validate_json("""{
      "skill": {
        "id": "shared/brief", "name": "brief", "description": "A concise brief.",
        "version": "1.0.0", "source": "fixture", "enabled": false,
        "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      },
      "markdown": "---\\nname: brief\\n---\\nFull instructions.\\n",
      "metadata": {"openclaw": {"unknown": [1, 2]}},
      "extensions": {"fixture": {"unknown": true}},
      "resources": []
    }""")
    assert SkillDocument.model_validate_json(document.model_dump_json()) == document
    assert document.markdown == "---\nname: brief\n---\nFull instructions.\n"
    assert document.metadata == {"openclaw": {"unknown": [1, 2]}}
    assert document.extensions == {"fixture": {"unknown": True}}


def test_operator_bundle_limit_precedes_provider_dispatch() -> None:
    with pytest.raises(ValidationError):
        SkillBundle(name="too-large", files={"SKILL.md": "A" * 200000})
