"""Word recall from the real engine through the shared composer, without inference."""

import asyncio
import json
import re
import sqlite3
import struct
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ember_agent.character.core import CharacterCore, prompt_cost
from ember_agent.character.prompt_budget import recall_value
from ember_agent.inference.contracts import GatewayError
from ember_memory_abi.interfaces import ContextRequest, ForgetRequest, MemoryQuery, RequestOptions
from ember_memory_abi.memory import (
    Entity,
    EntityRef,
    EpisodePayload,
    FactPayload,
    GoalPayload,
    Memory,
    MemoryPayload,
    PlanPayload,
    StringValue,
)
from ember_memory_abi.selection import RecallSelection
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.search_contracts import EmbeddingSpec

from .test_character import FakeModel

AUTHORITY = MutationAuthority(mode="confirmed_user")


class EqualEncoder:
    """Equal semantic distances force lexical selection to do the useful work."""

    specification = EmbeddingSpec(
        model_revision="synthetic",
        tokenizer_revision="synthetic",
        model_sha256="a" * 64,
        tokenizer_sha256="b" * 64,
    )
    query_calls = 0

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((m.start(), m.end()) for m in re.finditer(r"\S+", text))

    def token_count(self, text: str) -> int:
        return len(self.offsets(text)) + 2

    def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[bytes, ...]:
        if query:
            self.query_calls += len(texts)
        return tuple(struct.pack("<384f", 1.0, *([0.0] * 383)) for _ in texts)


@pytest.mark.parametrize("indexed", [False, True])
def test_name_value_beats_noisy_predicate_labels_and_reaches_prompt(
    tmp_path: Path, indexed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        live_subject = ensure_subject(engine, character)
        imported_subject = EntityRef(store_id=character, entity_id=uuid4())
        engine.put_entity(character, Entity(reference=imported_subject, revision=1, name="User"))
        author = MemoryAuthoringService(engine, imported_subject, authority=AUTHORITY)
        for index in range(110):
            value = f"Synthetic alias {index}"
            author.create(
                NewMemoryInput(
                    idempotency_key=str(index),
                    text=value,
                    importance=1.0,
                    payload=FactPayload(
                        subject=imported_subject,
                        predicate="entity.name",
                        definition_version="0.1.0",
                        value=StringValue(value=value),
                    ),
                )
            )
        name = author.create(
            NewMemoryInput(
                idempotency_key="name",
                text="The user's name is Rowan.",
                importance=0.9,
            )
        ).affected[0]
        encoder = EqualEncoder()
        if indexed:
            engine.encoding.encoder = encoder
            engine.rebuild_index(character, encoder)
        store = engine.character(character)
        loaded: list[UUID] = []
        read = store.state.read_memory

        def tracked_read(
            connection: sqlite3.Connection,
            memory_id: UUID,
            revision: int | None = None,
            options: RequestOptions | None = None,
            *,
            allow_suppressed: bool = False,
        ) -> Memory:
            loaded.append(memory_id)
            return read(connection, memory_id, revision, options, allow_suppressed=allow_suppressed)

        monkeypatch.setattr(store.state, "read_memory", tracked_read)
        found = store.search(MemoryQuery(text="whats my name", limit=100))
        assert found.results[0].memory.memory_id == name.memory_id
        assert loaded[0] == name.memory_id  # The useful value survives even a tight read deadline.
        assert store.profile_settings().subject_id == live_subject.entity_id

        async def exercise() -> None:
            memory = InProcessAsyncMemoryClient(store)
            try:
                core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
                turn = await core.prepare("whats my name")
                recalled = next(m.content for m in turn.request.messages if "Rowan" in m.content)
                assert "The user's name is Rowan." in recalled
                assert str(name.memory_id) not in recalled and "evidence" not in recalled
                assert (name.memory_id, 1) in turn.dependencies
                assert turn.retained_pins == 0
                assert turn.accounting is not None
                assert turn.accounting.memories <= core.memory_ceiling
                if indexed:
                    assert encoder.query_calls == 1  # One cached hybrid query; no expansion.
            finally:
                await memory.aclose()

        asyncio.run(exercise())


@pytest.mark.parametrize("indexed", [False, True])
def test_words_search_all_eligible_memories_before_limits(tmp_path: Path, indexed: bool) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        subject = ensure_subject(engine, character)
        author = MemoryAuthoringService(engine, subject, authority=AUTHORITY)
        store = engine.character(character, authority=AUTHORITY)
        for index in range(30):
            author.create(
                NewMemoryInput(
                    idempotency_key=f"unrelated-{index}", text=f"Other {index}", importance=1
                )
            )
        expected = set()
        for index in range(28):
            ref = author.create(
                NewMemoryInput(
                    idempotency_key=f"milo-{index}",
                    text=f"Milo observation {index}",
                    importance=0.5,
                )
            ).affected[0]
            expected.add(ref.memory_id)
        payloads: tuple[MemoryPayload, ...] = (
            FactPayload(
                subject=subject,
                predicate="entity.name",
                definition_version="0.1.0",
                value=StringValue(value="Milo"),
            ),
            GoalPayload(description="Visit Milo", state="proposed"),
            PlanPayload(description="Meet Milo", steps=("Arrange a café visit",)),
            EpisodePayload(event="Milo's café visit"),
        )
        for index, payload in enumerate(payloads):
            ref = author.create(
                NewMemoryInput(
                    idempotency_key=f"kind-{index}",
                    text="Milo",
                    payload=payload,
                    modality=payload.type.upper()
                    if isinstance(payload, (GoalPayload, PlanPayload))
                    else "FACTUAL_ASSERTION",
                )
            ).affected[0]
            expected.add(ref.memory_id)
        author.create(NewMemoryInput(idempotency_key="boundary", text="Milonga dancing"))
        forgotten = author.create(
            NewMemoryInput(
                idempotency_key="forgotten", text="Milo forgotten observation", importance=1
            )
        ).affected[0]
        store.forget(
            ForgetRequest(
                idempotency_key="forget",
                memory_id=forgotten.memory_id,
                expected_revision=1,
                reason="synthetic test",
            )
        )
        if indexed:
            encoder = EqualEncoder()
            engine.encoding.encoder = encoder
            engine.rebuild_index(character, encoder)
        response = store.retrieve_context(
            ContextRequest(
                memory_query=MemoryQuery(
                    text="Can you recall memories of MILO?", mode="lexical", limit=100
                ),
                memory_token_budget=16384,
            )
        )
        assert {row.memory.memory_id for row in response.memories} == expected
        assert len(response.memories) == 32
        cafe = store.search(MemoryQuery(text="CAFE", mode="lexical", limit=100))
        assert len(cafe.results) == 2
        both = store.search(MemoryQuery(text="café dancing", mode="lexical", limit=100))
        assert len(both.results) == 3  # Independent words, rather than a full substring.
        store.configure_recall(RecallSelection(local_enabled=False, import_mode="none"))
        assert not store.search(MemoryQuery(text="Milo", mode="lexical", limit=100)).results


@pytest.mark.parametrize("input_ceiling", [7000, 28000])
def test_exact_values_fit_dynamic_budget_and_oversized_value_does_not_block_rest(
    tmp_path: Path, input_ceiling: int
) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        subject = ensure_subject(engine, character)
        author = MemoryAuthoringService(engine, subject, authority=AUTHORITY)
        huge = author.create(
            NewMemoryInput(idempotency_key="huge", text="Milo " + "long " * 1600, importance=1)
        ).affected[0]
        values = [f"Milo value {index}: enjoys a quiet afternoon." for index in range(40)]
        for index, value in enumerate(values):
            author.create(NewMemoryInput(idempotency_key=str(index), text=value))
        # Equal extracted values from independent evidence should only occupy one row.
        author.create(NewMemoryInput(idempotency_key="duplicate", text=values[0]))
        store = engine.character(character, authority=AUTHORITY)

        async def exercise() -> None:
            memory = InProcessAsyncMemoryClient(store)
            try:
                core = CharacterCore(
                    memory, FakeModel(), model_tag="synthetic", input_ceiling=input_ceiling
                )
                turn = await core.prepare("Milo")
                assert turn.retained_memories > 6
                assert (huge.memory_id, 1) not in turn.dependencies
                assert "memory_category_budget" in turn.omissions
                assert turn.accounting is not None
                assert turn.accounting.memories <= min(6144, input_ceiling // 4)
                assert prompt_cost(turn.request.messages) <= input_ceiling
                data = next(m.content for m in turn.request.messages if "data only" in m.content)
                rows = json.loads(data.split("\n", 1)[1])
                assert len({row["value"] for row in rows}) == len(rows)
                assert {row["value"] for row in rows} <= set(values)
                assert all("id" not in row and "time" not in row for row in rows)
                if input_ceiling == 28000:
                    assert turn.retained_memories == 40
                selected = turn.dependencies[0][0]
                store.forget(
                    ForgetRequest(
                        idempotency_key="selected",
                        memory_id=selected,
                        expected_revision=1,
                        reason="synthetic test",
                    )
                )
                with pytest.raises(GatewayError, match="context_invalidated"):
                    _ = [event async for event in core.stream(turn)]
            finally:
                await memory.aclose()

        asyncio.run(exercise())


def test_compact_value_keeps_polarity_modality_subject_and_semantic_dates(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        subject = ensure_subject(engine, character)
        author = MemoryAuthoringService(engine, subject, authority=AUTHORITY)
        reference = author.create(
            NewMemoryInput(
                idempotency_key="negative",
                text="I did not own a synthetic GPU.",
                modality="NEGATED_ASSERTION",
                payload=FactPayload(
                    subject=subject,
                    predicate="hardware.owns_gpu",
                    definition_version="0.1.0",
                    polarity="negative",
                    value=StringValue(value="SYNTHETIC_GPU"),
                ),
                temporal=TemporalBounds(
                    valid_from="2026-01-01T00:00:00.000000Z",
                    basis="explicit_open_end",
                    precision="day",
                ),
            )
        ).affected[0]
        memory = engine.character(character).get(reference.memory_id)
        row = recall_value(memory)
        assert row["polarity"] == "negative" and row["modality"] == "NEGATED_ASSERTION"
        assert row["subject"] == str(subject.entity_id)
        assert row["value"] == {"type": "string", "value": "SYNTHETIC_GPU"}
        assert row["time"] == {
            "valid_from": "2026-01-01T00:00:00.000000Z",
            "basis": "explicit_open_end",
            "precision": "day",
        }
        assert "recorded_at" not in json.dumps(row)
        assert memory.temporal.recorded_at in json.dumps(recall_value(memory, historical=True))
        hypothetical = author.create(
            NewMemoryInput(
                idempotency_key="hypothetical",
                text="Imagine meeting Milo.",
                modality="HYPOTHETICAL",
            )
        ).affected[0]
        assert (
            recall_value(engine.character(character).get(hypothetical.memory_id))["modality"]
            == "HYPOTHETICAL"
        )
