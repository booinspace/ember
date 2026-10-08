"""Opt-in pinned local-model acceptance; source text stays synthetic and output is not logged."""

import asyncio
import os
from pathlib import Path

import pytest
from livekit.agents import llm

from ember_agent.character.core import CharacterCore
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.realtime.livekit import EmberAgent
from ember_agent.runtime.config import load_config
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_importers.normalization.extraction import explicit_primary_gpu


@pytest.mark.integration
def test_real_gateway_livekit_recalled_fact_after_restart(tmp_path: Path) -> None:
    setting = os.environ.get("EMBER_REAL_CONFIG")
    if not setting:
        pytest.skip(
            "Set EMBER_REAL_CONFIG and run the matching Ember gateway for real-model acceptance."
        )
    config = load_config(Path(setting), require_model_pin=True)
    token = (config.runtime_root / "gateway.token").read_text(encoding="utf-8").strip()
    root = tmp_path / "canonical"
    with CanonicalEngine(root) as first:
        character = first.create_character()
        subject = ensure_subject(first, character.store_id)
        text = "My primary GPU is RTX 3090."
        receipt = MemoryAuthoringService(
            first, subject, authority=MutationAuthority(mode="confirmed_user")
        ).create(
            NewMemoryInput(
                idempotency_key="synthetic-gpu",
                text=text,
                payload=explicit_primary_gpu(text, subject),
            )
        )
        identity = receipt.affected[0].memory_id
    with CanonicalEngine(root) as restarted:
        memory = InProcessAsyncMemoryClient(restarted.character(character.store_id))

        async def run() -> None:
            client = HttpGatewayClient(config.services.gateway.endpoint, token)
            try:
                assert (await memory.get_sources(identity))[0].quote == text
                core = CharacterCore(memory, client, model_tag=config.model_tag)
                agent = EmberAgent(core)
                context = llm.ChatContext()
                user = context.add_message(
                    role="user", content="What is my primary GPU? Answer briefly."
                )
                await agent.on_user_turn_completed(context, user)
                stream = agent.gateway_llm.chat(chat_ctx=context)
                output = "".join(
                    [chunk.delta.content or "" async for chunk in stream if chunk.delta]
                )
                await stream.aclose()
                assert "3090" in output, "The synthetic remembered hardware fact was not recalled."
            finally:
                await memory.aclose()
                await client.aclose()

        asyncio.run(run())
