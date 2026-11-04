"""Output enters native history only after an explicit delivered-prefix acknowledgement."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayError
from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_memory_abi.archive import Message
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import ensure_subject
from ember_memory_engine.capture import CaptureService

from .test_character import FakeModel


def test_audio_delivery_keeps_only_acknowledged_prefix_and_replays_once(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        subject = ensure_subject(engine, character)
        archive = engine.archive(character)
        sink = ArchiveDialogueSink(archive, subject)
        memory = InProcessAsyncMemoryClient(engine.character(character))

        async def exercise() -> None:
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic", sink=sink)
            prepared = await core.prepare("Milo is my synthetic shark plushie.")
            assert len(CaptureService(engine, character).pending()) == 1
            output = "".join(
                [event.text async for event in core.stream(prepared, defer_delivery=True)]
            )
            assert output == "Synthetic visible reply"
            assert len(core.history) == 1 and core.history[0].assistant == ""
            records = archive.database.read(
                lambda c: c.execute(
                    "SELECT count(*) FROM source_records WHERE record_kind='message'"
                ).fetchone()[0]
            )
            assert records == 1  # The original user input survived; unheard output did not.
            with pytest.raises(GatewayError, match="delivery_receipt_unknown"):
                await core.acknowledge(uuid4(), output, complete=True)
            with pytest.raises(GatewayError, match="delivery_receipt_invalid"):
                await core.acknowledge(prepared.request.request_id, "different", complete=True)
            delivered = output[:9]
            await core.acknowledge(prepared.request.request_id, delivered, complete=False)
            assert core.history[0].assistant == delivered and not core.history[0].complete
            assert core.pending_delivery is None
            await sink.commit_context(
                core.context_id,
                prepared.request.request_id,
                prepared.observed_at,
                delivered,
                False,
                prepared.dependencies,
                prepared.certificate,
            )
            assert len(CaptureService(engine, character).pending()) == 1
            messages = archive.database.read(
                lambda c: tuple(
                    Message.model_validate_json(row[0])
                    for row in c.execute(
                        "SELECT payload_json FROM source_records WHERE record_kind='message'"
                    )
                )
            )
            assert len(messages) == 2
            assistant = next(message for message in messages if message.actor.role == "assistant")
            assert assistant.parts[0].text == delivered
            assert assistant.recipient == "partial_delivery"
            await sink.aclose()
            await memory.aclose()

        asyncio.run(exercise())
