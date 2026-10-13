"""Explicit final speech and text reuse the original input clock without extra model calls."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from livekit.agents import llm

from ember_agent.character.core import CharacterCore, TurnObservation
from ember_agent.inference.contracts import GatewayError
from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_agent.memory.deep_contracts import DeepRecallResult
from ember_agent.realtime.livekit import EmberAgent
from ember_memory_abi.archive import Message
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import ensure_subject
from ember_memory_engine.capture import CaptureService

from .test_character import FakeModel


@pytest.mark.parametrize("text", ["/recall NAS problems", "Recall deeply NAS problems"])
def test_explicit_deep_input_is_archived_before_deferred_recall(tmp_path: Path, text: str) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        sink = ArchiveDialogueSink(engine.archive(character), ensure_subject(engine, character))
        memory = InProcessAsyncMemoryClient(engine.character(character))

        async def exercise() -> None:
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic", sink=sink)
            request_id = uuid4()

            async def investigate(query: str, observation: TurnObservation) -> DeepRecallResult:
                assert query == "NAS problems"
                assert observation.text == text and observation.request_id == request_id
                assert len(CaptureService(engine, character).pending()) == 1
                return DeepRecallResult(
                    request_id=request_id,
                    character_id=character,
                    context_id=core.context_id,
                    query=query,
                    outcome="deferred",
                    reasons=("deep_deferred_for_foreground",),
                )

            agent = EmberAgent(core, investigate=investigate)
            context = llm.ChatContext()
            message = context.add_message(role="user", content=text)
            await agent.prepare_turn(message, request_id=request_id)
            assert agent.gateway_llm.prepared is not None
            turn = agent.gateway_llm.prepared[1]
            assert turn.request.request_id == request_id
            assert turn.local_reply is not None and "deferred" in turn.local_reply
            assert turn.dependencies == ()
            messages = sink.archive.database.read(
                lambda c: tuple(
                    Message.model_validate_json(row[0])
                    for row in c.execute(
                        "SELECT payload_json FROM source_records WHERE record_kind='message'"
                    )
                )
            )
            assert len(messages) == 1 and messages[0].parts[0].text == text
            with pytest.raises(GatewayError, match="turn_observation_mismatch"):
                await core.prepare("changed", observation=await core.observe_input(text))
            await sink.aclose()
            await memory.aclose()

        asyncio.run(exercise())


def test_cancelled_deep_input_still_has_original_capture(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        sink = ArchiveDialogueSink(engine.archive(character), ensure_subject(engine, character))
        memory = InProcessAsyncMemoryClient(engine.character(character))

        async def exercise() -> None:
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic", sink=sink)

            async def investigate(query: str, observation: TurnObservation) -> DeepRecallResult:
                del query, observation
                raise asyncio.CancelledError

            agent = EmberAgent(core, investigate=investigate)
            message = llm.ChatContext().add_message(role="user", content="recall deeply NAS")
            with pytest.raises(asyncio.CancelledError):
                await agent.prepare_turn(message)
            assert agent.gateway_llm.prepared is None
            assert len(CaptureService(engine, character).pending()) == 1
            assert not core.history and core.pending_delivery is None
            await sink.aclose()
            await memory.aclose()

        asyncio.run(exercise())
