"""The public-page reader cannot reach private addresses or execute page content."""

import asyncio
from pathlib import Path

from ember_agent.character.core import CharacterCore
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_character_public_search import NoGeneration


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
            assert turn.local_reply and "unavailable" in turn.local_reply

    asyncio.run(run())


def test_page_failure_activity_keeps_only_a_safe_fixed_diagnostic(tmp_path: Path) -> None:
    from ember_agent.observability import ActivityLog
    from ember_agent.public_web import PublicWeb, WebBinding
    from ember_agent.web_evidence import WebEvidence, web_failure

    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            core = CharacterCore(
                InProcessAsyncMemoryClient(engine.character(character.store_id)),
                NoGeneration(),
                model_tag="synthetic",
            )
            core.activity = ActivityLog()

            async def unavailable(_: str) -> WebEvidence:
                return web_failure("fixture", "read", "rate_limited")

            core.public_web = PublicWeb(
                read_binding=WebBinding(
                    "fixture",
                    "Fixture",
                    "revision",
                    unavailable,
                )
            )
            turn = await core.prepare("Fixture read: https://arxiv.org/abs/1706.03762")
            assert turn.local_reply and "rate-limited" in turn.local_reply
            event = next(e for e in core.activity.since(0) if e.name == "read_page")
            assert (
                event.code == "public_web_rate_limited" and "arxiv" not in event.model_dump_json()
            )

    asyncio.run(run())
