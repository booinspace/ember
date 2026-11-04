"""Shared character/LiveKit slice against real bounded canonical memory clients."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from livekit.agents import llm

from ember_agent.character.core import CharacterCore, Personality, prompt_cost
from ember_agent.inference.contracts import (
    GatewayError,
    GatewayEvent,
    GatewayRequest,
    GenerationUsage,
)
from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_agent.realtime.livekit import EmberAgent
from ember_memory_abi.archive import ArchiveWriteReceipt, Message
from ember_memory_abi.interfaces import ForgetRequest
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_importers.normalization.extraction import explicit_primary_gpu


class FakeModel:
    def __init__(self) -> None:
        self.requests: list[GatewayRequest] = []
        self.release = asyncio.Event()
        self.release.set()

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        self.requests.append(request)
        yield GatewayEvent(
            request_id=request.request_id, context_id=request.context_id, sequence=0, kind="start"
        )
        await self.release.wait()
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=1,
            kind="text",
            text="Synthetic visible reply",
        )
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=2,
            kind="end",
            usage=GenerationUsage(
                prompt_tokens=20,
                output_tokens=3,
                total_seconds=0.1,
                first_visible_seconds=0.05,
                tokens_per_second=30.0,
            ),
        )


def test_memory_recall_prompt_provenance_and_native_delivery(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character()
        subject = ensure_subject(engine, character.store_id)
        author = MemoryAuthoringService(
            engine, subject, authority=MutationAuthority(mode="confirmed_user")
        )
        text = "My primary GPU is RTX 3090."
        receipt = author.create(
            NewMemoryInput(
                idempotency_key="gpu", text=text, payload=explicit_primary_gpu(text, subject)
            )
        )
        identity = receipt.affected[0].memory_id
        memory = InProcessAsyncMemoryClient(engine.character(character.store_id))
        sink = ArchiveDialogueSink(engine.archive(character.store_id))

        async def run() -> None:
            model = FakeModel()
            core = CharacterCore(memory, model, model_tag="synthetic", sink=sink)
            turn = await core.prepare("What is my primary GPU?")
            assert turn.retained_memories == 1
            assert "RTX 3090" in turn.request.messages[1].content
            assert turn.request.messages[0].content == Personality().instructions
            assert prompt_cost(turn.request.messages) <= 7000
            output = "".join([event.text async for event in core.stream(turn)])
            assert output == "Synthetic visible reply" and len(model.requests) == 1
            assert (await memory.get_sources(identity))[0].quote == text
            await sink.aclose()
            await memory.aclose()

        asyncio.run(run())
        assert (
            engine.archive(character.store_id).database.read(
                lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM source_records WHERE record_kind='message'"
                ).fetchone()[0]
            )
            == 2
        )
    with CanonicalEngine(root) as restarted:
        assert restarted.character(character.store_id).get_sources(identity)[0].quote == text


def test_session_archives_multiple_turns_without_rewriting_conversation(tmp_path: Path) -> None:
    root = tmp_path / "data"
    first_stamp = datetime(2026, 10, 9, tzinfo=UTC)
    snapshots: list[tuple[object, ...]] = []
    with patch("ember_agent.memory.backends.dialogue.datetime") as delivery_clock:
        delivery_clock.now.side_effect = [
            first_stamp + timedelta(seconds=index) for index in range(4)
        ]
        with CanonicalEngine(root) as engine:
            character = engine.create_character().store_id
            subject = ensure_subject(engine, character)
            archive = engine.archive(character)
            memory = InProcessAsyncMemoryClient(engine.character(character))
            sink = ArchiveDialogueSink(archive, subject)

            async def run() -> UUID:
                core = CharacterCore(
                    memory,
                    FakeModel(),
                    model_tag="synthetic",
                    sink=sink,
                    reference_clock=delivery_clock.now,
                )
                for prompt in ("First synthetic question.", "Second synthetic question."):
                    turn = await core.prepare(prompt)
                    assert "".join([event.text async for event in core.stream(turn)])
                    snapshots.append(
                        archive.database.read(
                            lambda c: tuple(
                                c.execute(
                                    "SELECT payload_json,payload_sha256,recorded_at_us FROM "
                                    "source_records WHERE record_kind='conversation'"
                                ).fetchone()
                            )
                        )
                    )
                await sink.aclose()
                assert not sink.pending
                await memory.aclose()
                return core.context_id

            conversation_id = asyncio.run(run())
            messages = archive.database.read(
                lambda c: tuple(
                    Message.model_validate_json(row[0])
                    for row in c.execute(
                        "SELECT payload_json FROM source_records WHERE record_kind='message'"
                    )
                )
            )
            assert snapshots[0] == snapshots[1]
            assert len(messages) == len({m.source.record_id for m in messages}) == 4
            assert all(m.conversation.record_id == conversation_id for m in messages)
            assert all(m.conversation.record_revision == 1 for m in messages)
            assert all(m.channel == "final" for m in messages)
            assert all(
                m.recipient == ("input_observed" if m.actor.role == "user" else "complete")
                for m in messages
            )
            users = tuple(m for m in messages if m.actor.role == "user")
            assert len(users) == 2 and all(m.actor.actor_id == subject.entity_id for m in users)
            assert {m.parts[0].text for m in users} == {
                "First synthetic question.",
                "Second synthetic question.",
            }
            assert (
                archive.database.read(
                    lambda c: c.execute("SELECT COUNT(*) FROM source_keys").fetchone()[0]
                )
                == 4
            )
            written = archive.database.read(
                lambda c: tuple(
                    ArchiveWriteReceipt.model_validate_json(row[0]).records_written
                    for row in c.execute("SELECT receipt_json FROM archive_receipts")
                )
            )
            assert sorted(written) == [1, 1, 1, 2]

        with CanonicalEngine(root) as reopened:
            archive = reopened.archive(character)
            sink = ArchiveDialogueSink(archive, subject)

            async def append_after_restart() -> None:
                await sink.commit(
                    conversation_id, "Third synthetic question.", "Third visible reply.", True
                )
                await sink.commit(conversation_id, "Interrupted synthetic question.", "", False)
                await sink.aclose()

            asyncio.run(append_after_restart())
            assert (
                archive.database.read(
                    lambda c: tuple(
                        c.execute(
                            "SELECT payload_json,payload_sha256,recorded_at_us FROM source_records "
                            "WHERE record_kind='conversation'"
                        ).fetchone()
                    )
                )
                == snapshots[0]
            )
            assert (
                archive.database.read(
                    lambda c: c.execute(
                        "SELECT COUNT(*) FROM source_records WHERE record_kind='message'"
                    ).fetchone()[0]
                )
                == 7
            )
            assert (
                archive.database.read(
                    lambda c: c.execute("SELECT COUNT(*) FROM source_keys").fetchone()[0]
                )
                == 7
            )
            assert (
                archive.database.read(
                    lambda c: c.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
                )
                == 6
            )
            messages = archive.database.read(
                lambda c: tuple(
                    Message.model_validate_json(row[0])
                    for row in c.execute(
                        "SELECT payload_json FROM source_records WHERE record_kind='message'"
                    )
                )
            )
            assert sum(m.recipient == "partial_delivery" for m in messages) == 1
            assert all(m.conversation.record_id == conversation_id for m in messages)
        assert delivery_clock.now.call_count == 4


def test_forget_invalidates_prepared_turn_and_copied_history(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character()
        subject = ensure_subject(engine, character.store_id)
        writer = engine.character(
            character.store_id, authority=MutationAuthority(mode="confirmed_user")
        )
        receipt = MemoryAuthoringService(
            engine, subject, authority=MutationAuthority(mode="confirmed_user")
        ).create(NewMemoryInput(idempotency_key="memory", text="Synthetic GPU memory"))
        identity = receipt.affected[0].memory_id
        memory = InProcessAsyncMemoryClient(writer)

        async def run() -> None:
            model = FakeModel()
            core = CharacterCore(
                memory,
                model,
                model_tag="synthetic",
                sink=ArchiveDialogueSink(engine.archive(character.store_id)),
            )
            first = await core.prepare("GPU")
            _ = [event async for event in core.stream(first)]
            assert len(core.history) == 1
            prepared = await core.prepare("GPU")
            await memory.forget(
                ForgetRequest(
                    idempotency_key="forget",
                    memory_id=identity,
                    expected_revision=1,
                    reason="user_request",
                )
            )
            with pytest.raises(GatewayError, match="context_invalidated"):
                _ = [event async for event in core.stream(prepared)]
            clean = await core.prepare("GPU")
            assert clean.retained_memories == 0
            assert not any(m.role == "assistant" for m in clean.request.messages)
            assert sum(m.content == "GPU" for m in clean.request.messages) == 3
            assert len(model.requests) == 1
            await memory.aclose()

        asyncio.run(run())


def test_livekit_real_hook_and_sdk_stream_share_core(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character()
        memory = InProcessAsyncMemoryClient(engine.character(character.store_id))

        async def run() -> None:
            model = FakeModel()
            core = CharacterCore(memory, model, model_tag="synthetic")
            agent = EmberAgent(core)
            context = llm.ChatContext()
            message = context.add_message(role="user", content="Synthetic hello")
            with pytest.raises(GatewayError, match="livekit_unprepared_generation"):
                agent.gateway_llm.chat(chat_ctx=context)
            await agent.on_user_turn_completed(context, message)
            stream = agent.gateway_llm.chat(chat_ctx=context)
            chunks = [chunk async for chunk in stream]
            await stream.aclose()
            assert (
                "".join(chunk.delta.content or "" for chunk in chunks if chunk.delta)
                == "Synthetic visible reply"
            )
            assert chunks[-1].usage is not None and len(model.requests) == 1
            with pytest.raises(GatewayError, match="livekit_unprepared_generation"):
                agent.gateway_llm.chat(chat_ctx=context)
            await memory.aclose()

        asyncio.run(run())


def test_prompt_drops_whole_old_turns_and_preserves_current_input(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character()
        memory = InProcessAsyncMemoryClient(engine.character(character.store_id))

        async def run() -> None:
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic", input_ceiling=2000)
            for _ in range(3):
                _ = [event async for event in core.stream(await core.prepare("old " * 150))]
            latest = await core.prepare("current " * 90)
            assert (
                latest.trimmed_turns > 0 and latest.request.messages[-1].content == "current " * 90
            )
            assert prompt_cost(latest.request.messages) <= 2000
            assert all(
                item.role != "assistant" or latest.request.messages[index - 1].role == "user"
                for index, item in enumerate(latest.request.messages)
            )
            with pytest.raises(GatewayError, match="prompt_overflow"):
                await core.prepare("large " * 1000)
            await memory.aclose()

        asyncio.run(run())


def test_text_composition_human_entry_and_stable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from ember_agent.runtime.composition import LocalRuntime
    from ember_agent.runtime.config import load_config

    example = Path(__file__).parents[1] / "config/deployment.example.toml"
    path = tmp_path / "deployment.toml"
    path.write_text(
        example.read_text(encoding="utf-8")
        .replace('tag = ""', 'tag = "synthetic"')
        .replace('expected_digest = ""', 'expected_digest = "' + "a" * 64 + '"')
        .replace("../../.local/data", "data")
        .replace("../../.local/runtime", "runtime"),
        encoding="utf-8",
    )
    config = load_config(path, require_model_pin=True)
    config.runtime_root.mkdir()
    (config.runtime_root / "gateway.token").write_text("a" * 40)
    inputs = iter(
        [
            "/remember one Synthetic GPU observation",
            "/remember one Changed memory",
            "What GPU?",
            "/quit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    runtime = LocalRuntime(config)
    identity = runtime.memory.character_id
    runtime.core.model = FakeModel()
    session = AsyncMock()
    monkeypatch.setattr(runtime.model, "session", session)
    asyncio.run(runtime.chat())
    runtime.close_workers()
    assert session.await_count == 2
    assert session.await_args_list[0].kwargs == {"connected": True}
    assert session.await_args_list[1].kwargs == {"connected": False}
    assert session.await_args_list[0].args == session.await_args_list[1].args
    output = capsys.readouterr().out
    assert "Memory applied" in output and "Synthetic visible reply" in output
    assert "idempotency" in output.lower()
    reopened = LocalRuntime(config)
    assert reopened.memory.character_id == identity
    asyncio.run(reopened.aclose())
    reopened.close_workers()
