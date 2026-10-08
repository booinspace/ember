"""Real pinned Qwen extraction from synthetic source, review, seed, restart and LiveKit hooks."""

import asyncio
import os
from pathlib import Path

import pytest
from livekit.agents import llm

from ember_agent.character.core import CharacterCore
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.realtime.livekit import EmberAgent
from ember_agent.runtime.config import load_config
from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_cli.maintenance import EngineImportTarget
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.library import KnowledgeSelection, LibraryService, SeedRequest
from ember_memory_engine.processing import ProcessingService
from ember_memory_engine.processing_contracts import ExtractionSpec, JobRequest
from ember_memory_engine.processing_gateway import GatewayExtractionModel
from ember_memory_engine.processing_review import ReviewService
from ember_memory_importers.normalization.memory_summary import import_summary


@pytest.mark.integration
@pytest.mark.end_to_end
def test_real_structured_extraction_seed_restart_and_livekit_gateway_recall(tmp_path: Path) -> None:
    setting = os.environ.get("EMBER_REAL_CONFIG")
    model_path = os.environ.get("EMBER_EMBEDDING_MODEL")
    if not setting or not model_path:
        pytest.skip("Set EMBER_REAL_CONFIG and EMBER_EMBEDDING_MODEL with a ready Phase 6 gateway.")
    config = load_config(Path(setting), require_model_pin=True)
    background_token = (
        (config.runtime_root / "gateway-background.token").read_text(encoding="utf-8").strip()
    )
    foreground_token = (config.runtime_root / "gateway.token").read_text(encoding="utf-8").strip()
    root = tmp_path / "synthetic-canonical"
    source = tmp_path / "synthetic-summary.txt"
    source.write_text("My favorite color is cobalt blue.", encoding="utf-8")
    with CanonicalEngine(root) as engine:
        library = engine.create_library("synthetic-summary-account")
        import_summary(
            source,
            target=EngineImportTarget(engine.archive(library.store_id)),
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "manifests",
        )
        service = ProcessingService(engine, library.store_id)
        spec = ExtractionSpec(
            model=config.model_tag,
            model_digest=config.model_digest.removeprefix("sha256:"),
            workload="P3",
        )
        job = service.create(
            JobRequest(
                idempotency_key="synthetic-extraction",
                owner_store_id=library.store_id,
                specification=spec,
            )
        )

        async def extract() -> None:
            model = GatewayExtractionModel(config.services.gateway.endpoint, background_token, spec)
            try:
                done = await service.run(job.definition.job_id, model)
                assert done.status == "complete" and done.candidates > 0, done.model_dump_json()
            finally:
                await model.aclose()

        asyncio.run(extract())
        reviewer = ReviewService(service, authority=MutationAuthority(mode="confirmed_user"))
        review = reviewer.export(job.definition.job_id)
        selected = tuple(
            entry for entry in review.entries if "cobalt" in " ".join(entry.quotes).lower()
        )
        assert selected
        review = review.model_copy(
            update={
                "entries": tuple(
                    entry.model_copy(update={"decision": "approve"}) for entry in selected
                )
            }
        )
        receipt = reviewer.apply(review)
        character = engine.create_character()
        seeded = LibraryService(engine, authority=MutationAuthority(mode="confirmed_user")).seed(
            character.store_id,
            SeedRequest(
                idempotency_key="synthetic-seed",
                library_id=library.store_id,
                selections=tuple(
                    KnowledgeSelection(knowledge_id=identity, revision=1)
                    for identity in receipt.approved
                ),
            ),
        )
        encoder = BgeEncoder(Path(model_path))
        engine.rebuild_index(character.store_id, encoder)
    with CanonicalEngine(root, encoder=encoder) as restarted:
        memory = InProcessAsyncMemoryClient(restarted.character(character.store_id))

        async def recall() -> None:
            client = HttpGatewayClient(config.services.gateway.endpoint, foreground_token)
            try:
                response = await memory.search(MemoryQuery(text="preferred hue", mode="semantic"))
                assert response.results and response.coverage.index_status == "available"
                for affected in seeded.affected:
                    assert (await memory.get_sources(affected.memory_id))[
                        0
                    ].quote in source.read_text(encoding="utf-8")
                core = CharacterCore(memory, client, model_tag=config.model_tag)
                agent = EmberAgent(core)
                context = llm.ChatContext()
                user = context.add_message(
                    role="user", content="What is my favorite color? Answer briefly."
                )
                await agent.on_user_turn_completed(context, user)
                stream = agent.gateway_llm.chat(chat_ctx=context)
                output = "".join(
                    [chunk.delta.content or "" async for chunk in stream if chunk.delta]
                )
                await stream.aclose()
                assert "cobalt" in output.lower()
            finally:
                await memory.aclose()
                await client.aclose()

        asyncio.run(recall())
