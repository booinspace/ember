"""Native session continuity stays independent of speech and bounded prompt selection."""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from ember_agent.character.core import CharacterCore, CompletedTurn, prompt_cost
from ember_agent.inference.contracts import GatewayError
from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_memory_abi.errors import NotFoundError
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.errors import IdempotencyConflictError
from ember_memory_engine.sessions import NativeSessions

from .test_character import FakeModel

STAMP = "2026-11-04T12:00:00.000000Z"


def test_long_session_exact_lookup_order_retries_and_fixed_prompt_cap(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        archive = engine.archive(character)
        sink = ArchiveDialogueSink(archive)
        memory = InProcessAsyncMemoryClient(engine.character(character))

        context_id = uuid4()

        async def run() -> None:
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic", input_ceiling=126976)
            core.context_id = context_id
            core.sink = sink
            with pytest.raises(NotFoundError):
                await sink.commit_delivery(core.context_id, uuid4(), STAMP, "Orphaned reply", True)
            assert (
                archive.database.read(
                    lambda db: db.execute("SELECT count(*) FROM source_records").fetchone()[0]
                )
                == 0
            )
            first = uuid4()
            await sink.begin_input(core.context_id, first, STAMP, "My aquarium is called Moonbeam.")
            await sink.begin_input(core.context_id, first, STAMP, "My aquarium is called Moonbeam.")
            with pytest.raises(IdempotencyConflictError):
                await sink.begin_input(core.context_id, first, STAMP, "Changed original")
            # Equal timestamps do not determine order. No assistant receipt is required.
            for index in range(110):
                request = uuid4()
                await sink.begin_input(
                    core.context_id, request, STAMP, f"Synthetic exchange {index}."
                )
                await sink.commit_context(
                    core.context_id, request, STAMP, "Long reply " * 1800, index % 2 == 0, (), None
                )
            info = sink.sessions.inspect(core.context_id)
            assert info.turns == 111
            question = "What did I say at the start of our chat?"
            with patch.object(archive, "get_record", wraps=archive.get_record) as reads:
                turn = await core.prepare(question)
                assert reads.call_count <= 129
            assert turn.local_reply is None
            assert any("Moonbeam" in message.content for message in turn.request.messages)
            assert prompt_cost(turn.request.messages) <= 24576
            assert turn.accounting is not None and turn.accounting.excerpts <= 4096
            assert turn.accounting.recent_and_current <= core.history_ceiling + 512
            # The bounded selection did not remove any original transcript entries.
            assert sink.sessions.inspect(core.context_id).turns == 112
            topic = await core.prepare("What did I say about aquarium?")
            assert any("Moonbeam" in message.content for message in topic.request.messages)
            recent = sink.sessions.read(core.context_id, limit=2)
            assert [row.sequence for row in recent.turns] == [112, 113]
            await sink.aclose()
            await memory.aclose()

        asyncio.run(run())
        other = engine.create_character().store_id
        with pytest.raises(NotFoundError):
            NativeSessions(engine.archive(other)).inspect(context_id)


def test_observed_input_survives_failed_generation_long_reply_and_partial_delivery(
    tmp_path: Path,
) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        memory = InProcessAsyncMemoryClient(engine.character(character))
        sink = ArchiveDialogueSink(engine.archive(character))

        async def run() -> None:
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic", sink=sink)
            # A prepare with no generation/receipt still records the finalized input.
            await core.prepare("My shark plushie is called Milo.")
            second = await core.prepare("What is my plushie called?")
            assert any("Milo" in item.content for item in second.request.messages[:-1])
            _ = [event async for event in core.stream(second, defer_delivery=True)]
            await core.acknowledge(second.request.request_id, "Synthetic", complete=False)
            third = await core.prepare("Continue.")
            content = "\n".join(item.content for item in third.request.messages)
            assert "Milo" in content and "[Partial delivered reply]" in content
            assert "visible reply" not in content
            assert len(core.history) == 3 and not core.history[1].complete
            assert core.history[1].assistant == "Synthetic"
            # Even a long previous reply does not evict its user message as a pair.
            core.sink = None
            core.history = [CompletedTurn("Remember synthetic cobalt.", "Reply" * 9000, ())]
            last = await core.prepare("Which colour?")
            assert any("cobalt" in item.content for item in last.request.messages)
            assert "[Passage clipped]" in "\n".join(item.content for item in last.request.messages)
            await sink.aclose()
            await memory.aclose()

        asyncio.run(run())


def test_prompt_growth_unicode_and_mandatory_overflow(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        memory = InProcessAsyncMemoryClient(engine.character(character))

        author = MemoryAuthoringService(
            engine,
            ensure_subject(engine, character),
            authority=MutationAuthority(mode="confirmed_user"),
        )
        for index in range(12):
            author.create(
                NewMemoryInput(
                    idempotency_key=f"guard-{index}",
                    text=f"Current input memory {index}: " + "Synthetic aquarium fact. " * 12,
                )
            )

        async def run() -> None:
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic", input_ceiling=128000)
            sizes = []
            for count in (10, 100, 1000):
                core.history = [CompletedTurn('"🌊\\\n' * 200, "Visible" * 1000, ())] * count
                turn = await core.prepare("Current input remains exact.")
                sizes.append(prompt_cost(turn.request.messages))
                assert sizes[-1] <= 24576
                assert turn.retained_memories > 0
                assert turn.accounting and 0 < turn.accounting.memories <= core.memory_ceiling
                assert turn.request.messages[-1].content == "Current input remains exact."
                assert turn.accounting and turn.accounting.recent_and_current < 8500
                assert len(core.history) <= 64
            assert sizes[0] == sizes[1] == sizes[2]
            small = CharacterCore(memory, FakeModel(), model_tag="synthetic", input_ceiling=1800)
            with pytest.raises(GatewayError, match="prompt_overflow"):
                await small.prepare("🌊" * 600)
            await memory.aclose()

        asyncio.run(run())


def test_schema_six_upgrade_backs_up_and_keeps_originals(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        _, database = engine.maintenance_store(character)
        path = database.path
        sink = ArchiveDialogueSink(engine.archive(character))
        asyncio.run(sink.commit(uuid4(), "Synthetic original", "Delivered", True))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE native_turns")
        connection.execute("DROP TABLE native_sessions")
        connection.execute("DELETE FROM schema_versions WHERE version=7")
        connection.execute("PRAGMA user_version=6")
    with CanonicalEngine(root) as engine:
        _, database = engine.maintenance_store(character)
        assert database.read(lambda db: db.execute("PRAGMA user_version").fetchone()[0]) == 7
        assert database.read(lambda db: db.execute("PRAGMA quick_check").fetchone()[0]) == "ok"
        assert (
            database.read(
                lambda db: db.execute(
                    "SELECT count(*) FROM source_records WHERE record_kind='message'"
                ).fetchone()[0]
            )
            == 2
        )
    backup = path.with_suffix(".schema-6.backup.db")
    assert backup.is_file()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
