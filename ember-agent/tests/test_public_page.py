"""The public-page reader cannot reach private addresses or execute page content."""

import asyncio
import json
from pathlib import Path

import pytest

from ember_agent.character.core import CharacterCore
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_character_public_search import NoGeneration


def test_literal_page_preflight_precedes_recall_and_respects_core_guard(tmp_path: Path) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            memory = InProcessAsyncMemoryClient(engine.character(character.store_id))
            core = CharacterCore(memory, NoGeneration(), model_tag="synthetic", input_ceiling=8192)
            calls = []

            async def read(url: str) -> str:
                calls.append(url)
                return json.dumps({"url": url, "text": "Retrieved article.", "partial": True})

            core.public_page = read
            turn = await core.prepare("Summarize https://public.example/article")
            assert calls == ["https://public.example/article"]
            assert "Retrieved article." in turn.request.model_dump_json()
            assert "partial=true" in turn.request.model_dump_json()
            assert turn.accounting and turn.accounting.serialized_total <= 8192

    asyncio.run(run())


def test_missing_page_provider_reports_unavailable_without_hallucinated_fetch(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            core = CharacterCore(
                InProcessAsyncMemoryClient(engine.character(character.store_id)),
                NoGeneration(),
                model_tag="synthetic",
            )
            turn = await core.prepare("Summarize https://public.example/article")
            assert turn.local_reply and "not configured" in turn.local_reply

    asyncio.run(run())


@pytest.mark.parametrize(
    "user_text,expected",
    [
        ("Summarize https://arxiv.org/abs/1706.03762", "https://arxiv.org/abs/1706.03762"),
        (
            "Summarize [https://arxiv.org/abs/1706.03762](https://arxiv.org/abs/1706.03762)",
            "https://arxiv.org/abs/1706.03762",
        ),
        (
            'Read [paper](https://arxiv.org/abs/1706.03762 "paper title")',
            "https://arxiv.org/abs/1706.03762",
        ),
        ("Explain [paper](<https://arxiv.org/abs/1706.03762>)", "https://arxiv.org/abs/1706.03762"),
        (
            "Summarize [label](https://public.example/Path_(subject))",
            "https://public.example/Path_(subject)",
        ),
        ("Read https://public.example/Path_(subject).", "https://public.example/Path_(subject)"),
        ("Read [a](https://a.example) and [b](https://b.example)", None),
        ("Do not read https://arxiv.org/abs/1706.03762", None),
    ],
)
def test_literal_reader_handles_markdown_destinations_without_combining_urls(
    user_text: str, expected: str | None
) -> None:
    from ember_agent.character.public_search import public_page_url

    assert public_page_url(user_text) == expected


def test_page_failure_activity_keeps_only_a_safe_fixed_diagnostic(tmp_path: Path) -> None:
    from ember_agent.inference.contracts import GatewayError
    from ember_agent.observability import ActivityLog

    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            core = CharacterCore(
                InProcessAsyncMemoryClient(engine.character(character.store_id)),
                NoGeneration(),
                model_tag="synthetic",
            )
            core.activity = ActivityLog()

            async def unavailable(url: str) -> str:
                raise GatewayError("page_http_429")

            core.public_page = unavailable
            turn = await core.prepare("Read https://arxiv.org/abs/1706.03762")
            assert turn.local_reply and "unavailable" in turn.local_reply
            event = next(e for e in core.activity.since(0) if e.name == "read_page")
            assert event.code == "page_http_429" and "arxiv" not in event.model_dump_json()

    asyncio.run(run())
