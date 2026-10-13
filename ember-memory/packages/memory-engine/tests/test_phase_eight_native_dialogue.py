"""Ordinary dialogue becomes eligible native memory through the durable queue, without seeding."""

import asyncio
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayEvent, GatewayRequest, GenerationUsage
from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import ensure_subject
from ember_memory_engine.capture import CaptureService
from ember_memory_engine.processing_contracts import (
    DatedClaimBatch,
    DatedFactClaim,
    ModelWindow,
    native_temporal_specification,
)

from .test_phase_five_search import SyntheticEncoder

TEXT = "Milo is my synthetic shark plushie."


class ResponseModel:
    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=0,
            kind="text",
            text="I will keep that in mind.",
        )
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=1,
            kind="end",
            usage=GenerationUsage(
                prompt_tokens=1,
                output_tokens=1,
                total_seconds=0.01,
                first_visible_seconds=0.001,
                tokens_per_second=100.0,
            ),
        )


class CaptureModel:
    specification = native_temporal_specification("synthetic", "a" * 64)

    async def extract(self, window: ModelWindow) -> DatedClaimBatch:
        assert window.text == TEXT and window.reference.role == "user"
        return DatedClaimBatch(
            claims=(
                DatedFactClaim(
                    kind="fact",
                    statement=TEXT,
                    quote=TEXT,
                    quote_start=None,
                    predicate="personal.owns_object",
                    value=TEXT,
                    entity_name=None,
                    modality="FACTUAL_ASSERTION",
                    confidence=0.99,
                    importance=0.8,
                    verdict="supported",
                    date=None,
                ),
            )
        )


def test_ordinary_milo_dialogue_accepts_indexes_and_recalls_after_restart(tmp_path: Path) -> None:
    root = tmp_path / "data"
    encoder = SyntheticEncoder()
    with CanonicalEngine(root, encoder=encoder) as engine:
        character = engine.create_character().store_id
        other = engine.create_character().store_id
        subject = ensure_subject(engine, character)
        client = InProcessAsyncMemoryClient(engine.character(character))
        sink = ArchiveDialogueSink(engine.archive(character), subject)

        async def speak() -> None:
            core = CharacterCore(client, ResponseModel(), model_tag="synthetic", sink=sink)
            turn = await core.prepare(TEXT)
            assert len(CaptureService(engine, character).pending()) == 1
            assert not engine.character(character).search(MemoryQuery()).results
            assert "".join([event.text async for event in core.stream(turn)])
            await sink.aclose()
            await client.aclose()

        asyncio.run(speak())
        with ThreadPoolExecutor(max_workers=1) as worker:
            assert not asyncio.run(CaptureService(engine, character).drain(CaptureModel(), worker))
        accepted = engine.character(character).search(MemoryQuery()).results[0].memory
        assert engine.character(character).get_sources(accepted.memory_id)[0].quote == TEXT
        assert not engine.character(other).search(MemoryQuery()).results
        engine.rebuild_index(character, encoder)

    with CanonicalEngine(root, encoder=encoder) as restarted:
        client = InProcessAsyncMemoryClient(restarted.character(character))

        async def recall() -> None:
            core = CharacterCore(client, ResponseModel(), model_tag="synthetic")
            turn = await core.prepare("Do you remember Milo?")
            assert turn.retained_memories == 1, (
                turn.omissions,
                len(accepted.model_dump_json().encode()),
                len(restarted.character(character).search(MemoryQuery(text="Milo")).results),
            )
            assert TEXT in "\n".join(message.content for message in turn.request.messages)
            assert (await client.get_sources(accepted.memory_id))[0].quote == TEXT
            assert not restarted.character(other).search(MemoryQuery()).results
            await client.aclose()

        asyncio.run(recall())
