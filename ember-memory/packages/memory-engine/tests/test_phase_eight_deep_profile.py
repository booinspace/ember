"""Deep readers cannot write or introduce uncached CPU encoding ahead of ordinary recall."""

import asyncio

import pytest

from ember_memory_abi.errors import AccessDeniedError, SourceUnavailableError
from ember_memory_abi.interfaces import MemoryQuery, RememberRequest
from ember_memory_abi.memory import MemoryDraft
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.deep_profile import DeepSources

from .test_phase_five_search import SyntheticEncoder
from .test_phase_seven import append_native


def test_deep_profile_defers_uncached_semantics_and_reuses_foreground_vector(
    engine: CanonicalEngine, store: SqliteMemoryStore, draft: MemoryDraft
) -> None:
    store.remember(RememberRequest(idempotency_key="memory", draft=draft))
    encoder = SyntheticEncoder()
    engine.encoding.encoder = encoder
    engine.rebuild_index(store.character_id, encoder)
    query = MemoryQuery(text="primary GPU SYNTHETIC_A")
    count = encoder.calls

    async def exercise() -> None:
        deep = InProcessAsyncMemoryClient(store, deep_sources=DeepSources(engine))
        foreground = InProcessAsyncMemoryClient(store)
        try:
            result = await deep.search(query)
            assert result.results
            assert "deep_semantic_deferred_for_foreground_isolation" in result.coverage.reason_codes
            assert encoder.calls == count
            await foreground.search(query)
            current = encoder.calls
            assert current == count + 1
            assert (await deep.search(query)).results
            assert encoder.calls == current
            with pytest.raises(AccessDeniedError):
                await deep.remember(RememberRequest(idempotency_key="forbidden", draft=draft))
        finally:
            await deep.aclose()
            await foreground.aclose()

    asyncio.run(exercise())


def test_deep_sources_reject_oversized_payload_before_hydration(engine: CanonicalEngine) -> None:
    character = engine.create_character().store_id
    message = append_native(engine, character, "synthetic " * 30000)
    assert engine.resolve(message.source).source == message.source
    with pytest.raises(SourceUnavailableError):
        DeepSources(engine).resolve(message.source)
