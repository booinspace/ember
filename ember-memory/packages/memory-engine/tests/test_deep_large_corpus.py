"""Long original conversation paths must not exhaust deep reads for a handful of memories."""

import asyncio
from time import monotonic
from uuid import uuid4

import pytest

from ember_agent.memory.deep_contracts import DeepRecallRequest
from ember_agent.memory.deep_recall import DeepRecallService
from ember_memory_abi.archive import BranchSelection, Conversation, GraphNode, Message, SourceRecord
from ember_memory_abi.errors import InvalidEvidenceError
from ember_memory_abi.evidence import Evidence, SourceRef, make_evidence_span
from ember_memory_abi.interfaces import ArchiveQuery, ArchiveSearchResponse, RememberRequest
from ember_memory_abi.memory import MemoryDraft, StringValue
from ember_memory_abi.related import RelatedRequest, RelatedResponse
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import CanonicalEngine, SourceGrant
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.read_budget import ReadBudget

from .conftest import STAMP, TEXT, SourceFixture


def long_conversation(
    engine: CanonicalEngine, store: SqliteMemoryStore, fixture: SourceFixture, draft: MemoryDraft
) -> None:
    library = engine.create_library("synthetic-long-branch")

    def reference(kind: str) -> SourceRef:
        return SourceRef(
            source_store_id=library.store_id, record_kind=kind, record_id=uuid4(), record_revision=1
        )

    conversation_ref = reference("conversation")
    path = tuple(reference("graph_node") for _ in range(300))
    conversation = Conversation(
        source=conversation_ref,
        recorded_at=STAMP,
        selection=BranchSelection(status="resolved", selected_node=path[-1], path=path),
    )
    records: list[SourceRecord] = [conversation]
    messages: list[Message] = []
    for ordinal, node_ref in enumerate(path):
        message = None
        if ordinal >= 276:
            message = fixture.message.model_copy(
                update={
                    "source": reference("message"),
                    "conversation": conversation_ref,
                }
            )
            messages.append(message)
            records.append(message)
        records.append(
            GraphNode(
                source=node_ref,
                recorded_at=STAMP,
                conversation=conversation_ref,
                message=message.source if message is not None else None,
            )
        )
    engine.archive(library.store_id).write_batch(
        fixture.batch.model_copy(
            update={
                "source_store_id": library.store_id,
                "source_namespace": library.namespace,
                "idempotency_key": "long-conversation",
                "records": tuple(records),
            }
        )
    )
    engine.grant_source(
        store.character_id,
        SourceGrant(
            references=tuple(message.source for message in messages),
            capabilities=frozenset({"evidence_read", "archive_search"}),
        ),
    )
    for ordinal, message in enumerate(messages):
        assertion = draft.content.evidence[0]
        proof = Evidence(
            evidence_id=uuid4(),
            source=message.source.model_copy(update={"part_id": message.parts[0].part_id}),
            span=make_evidence_span(TEXT, 0, len(TEXT)),
        )
        memory = draft.model_copy(
            update={
                "content": draft.content.model_copy(
                    update={
                        "payload": draft.content.payload.model_copy(
                            update={"value": StringValue(value="SYNTHETIC_A " + str(ordinal))}
                        ),
                        "evidence": (assertion.model_copy(update={"evidence": proof}),),
                    }
                )
            }
        )
        store.remember(RememberRequest(idempotency_key="long:" + str(ordinal), draft=memory))


def test_long_original_branch_returns_supported_memories_within_unchanged_budget(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
) -> None:
    long_conversation(engine, store, source_fixture, draft)

    async def exercise() -> None:
        foreground = InProcessAsyncMemoryClient(store)

        def factory(deadline: float) -> InProcessAsyncMemoryClient:
            budget = ReadBudget(deadline=deadline)
            return InProcessAsyncMemoryClient(
                store, deep_sources=DeepSources(engine, budget), read_budget=budget
            )

        service = DeepRecallService(store.character_id, factory, validation=foreground)
        query = DeepRecallRequest(
            request_id=uuid4(),
            character_id=store.character_id,
            context_id=uuid4(),
            query="SYNTHETIC_A",
            reference_at=STAMP,
        )
        try:
            result = await service.investigate(query)
            assert result.memories and result.observations, result.reasons
            assert all(
                observation.branch_status == "selected" for observation in result.observations
            )
            assert result.counters.sql_statements < 1000
            assert result.counters.rows_reported < 1000
            assert not result.counters.limits_hit
            assert len(result.prompt_memories) <= 6
            assert await service.inspect(query.request_id, query.context_id) == result
            store.configure_recall(RecallSelection(import_mode="none"))
            assert await service.inspect(query.request_id, query.context_id) is None
        finally:
            await service.aclose()
            await foreground.aclose()

    asyncio.run(exercise())


def test_immutable_cache_reuses_payload_but_revalidates_every_exact_quote(
    engine: CanonicalEngine, draft: MemoryDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = engine.resolve
    calls = 0

    def resolve(reference: SourceRef, **kwargs: object) -> SourceRecord:
        nonlocal calls
        calls += 1
        return original(reference, **kwargs)

    monkeypatch.setattr(engine, "resolve", resolve)
    reader = DeepSources(engine, ReadBudget(deadline=monotonic() + 5))
    evidence = draft.content.evidence[0].evidence
    assert reader.validate(evidence) == TEXT
    assert reader.resolve(evidence.source).source.record_id == evidence.source.record_id
    assert reader.validate(evidence) == TEXT
    assert calls == 1
    changed = evidence.model_copy(
        update={"span": evidence.span.model_copy(update={"quoted_span_sha256": "0" * 64})}
    )
    with pytest.raises(InvalidEvidenceError):
        reader.validate(changed)


@pytest.mark.parametrize("stage", ["final_validation", "optional_archive"])
def test_budget_failure_keeps_completed_checkpoint_but_not_revoked_evidence(
    engine: CanonicalEngine, store: SqliteMemoryStore, draft: MemoryDraft, stage: str
) -> None:
    accepted = store.remember(RememberRequest(idempotency_key="checkpoint", draft=draft)).affected[
        0
    ]

    class Limited(InProcessAsyncMemoryClient):
        related_calls = 0

        async def lookup_related(self, request: RelatedRequest) -> RelatedResponse:
            self.related_calls += 1
            if stage == "final_validation" and self.related_calls == 2:
                budget = self._state.read_budget
                assert budget is not None
                budget.row_limit = budget.usage().materialized_rows
            return await super().lookup_related(request)

        async def search_archive(self, query: ArchiveQuery) -> ArchiveSearchResponse:
            if stage == "optional_archive":
                budget = self._state.read_budget
                assert budget is not None
                budget.row_limit = budget.usage().materialized_rows
            return await super().search_archive(query)

    async def exercise() -> None:
        foreground = InProcessAsyncMemoryClient(store)

        def factory(deadline: float) -> InProcessAsyncMemoryClient:
            budget = ReadBudget(deadline=deadline)
            return Limited(store, deep_sources=DeepSources(engine, budget), read_budget=budget)

        service = DeepRecallService(store.character_id, factory, validation=foreground)
        query = DeepRecallRequest(
            request_id=uuid4(),
            character_id=store.character_id,
            context_id=uuid4(),
            query="SYNTHETIC_A",
            reference_at=STAMP,
        )
        try:
            result = await service.investigate(query)
            assert result.outcome == "partial" and "read_budget_exceeded" in result.reasons
            assert [item.memory.memory_id for item in result.memories] == [accepted.memory_id]
            assert result.dependencies is not None
            assert result.counters.limits_hit == ("materialized_row_budget",)
            assert await service.inspect(query.request_id, query.context_id) == result
            store.configure_recall(RecallSelection(import_mode="none"))
            assert await service.inspect(query.request_id, query.context_id) is None
        finally:
            await service.aclose()
            await foreground.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("expected", ["selected", "alternative", "ambiguous"])
def test_branch_lookup_preserves_original_path_and_missing_node_semantics(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    expected: str,
) -> None:
    from ember_memory_engine.retrieval import branch_status

    # The existing library contains other conversations: a branch lookup must be exact.
    original = source_fixture.message.source
    conversation_ref = original.model_copy(
        update={"record_kind": "conversation", "record_id": uuid4()}
    )
    target_ref = original.model_copy(update={"record_kind": "graph_node", "record_id": uuid4()})
    other_ref = target_ref.model_copy(update={"record_id": uuid4()})
    missing_ref = target_ref.model_copy(update={"record_id": uuid4()})
    path = (
        (other_ref, target_ref)
        if expected == "selected"
        else (other_ref,)
        if expected == "alternative"
        else (missing_ref, target_ref)
    )
    conversation = Conversation(
        source=conversation_ref,
        recorded_at=STAMP,
        selection=BranchSelection(status="resolved", selected_node=path[-1], path=path),
    )
    message = source_fixture.message.model_copy(
        update={
            "source": original.model_copy(update={"record_id": uuid4()}),
            "conversation": conversation_ref,
        }
    )
    target = GraphNode(
        source=target_ref, recorded_at=STAMP, conversation=conversation_ref, message=message.source
    )
    other = GraphNode(source=other_ref, recorded_at=STAMP, conversation=conversation_ref)
    engine.archive(original.source_store_id).write_batch(
        source_fixture.batch.model_copy(
            update={
                "idempotency_key": "branch:" + expected,
                "records": (conversation, message, target, other),
            }
        )
    )
    assert branch_status(store.state, message) == expected
    reader = DeepSources(engine, ReadBudget(deadline=monotonic() + 5))
    assert reader.branch_status(message) == expected
    assert reader.budget is not None and reader.budget.usage().sql_statements < 30
