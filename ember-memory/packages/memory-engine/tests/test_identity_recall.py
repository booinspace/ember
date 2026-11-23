"""Opt-in canonical identity, origin and temporal lookup without derived indexes."""

import sqlite3
from uuid import UUID, uuid4

import pytest

from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.interfaces import (
    ContextRequest,
    ForgetRequest,
    MemoryQuery,
    RememberRequest,
    RequestOptions,
    SupersedeRequest,
    TemporalQuery,
)
from ember_memory_abi.memory import Entity, EntityRef, EntityValue, MemoryDraft, StringValue
from ember_memory_abi.related import RelatedRequest
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.engine import CanonicalEngine
from ember_memory_engine.identity_recall import IDENTITY_RECALL
from ember_memory_engine.models import SourceGrant

from .conftest import SourceFixture, TestClock


def _claims(store: SqliteMemoryStore, text: str) -> list[UUID]:
    return [hit.memory.memory_id for hit in store.search(_query(text)).results]


def _query(text: str, *, temporal: TemporalQuery | None = None) -> MemoryQuery:
    return MemoryQuery(
        text=text,
        mode="structured",
        temporal=temporal or TemporalQuery(),
        options=RequestOptions(extensions={IDENTITY_RECALL: {}}),
    )


def _name(engine: CanonicalEngine, fixture: SourceFixture) -> None:
    engine.put_entity(
        fixture.character_id,
        Entity(reference=fixture.subject, revision=2, name="Observatory relay", aliases=("SK-7",)),
    )


def _value(draft: MemoryDraft, value: str, start: str) -> MemoryDraft:
    return MemoryDraft.model_validate(
        {
            "content": {
                **draft.content.model_dump(),
                "payload": {
                    **draft.content.payload.model_dump(),
                    "value": StringValue(value=value),
                },
            },
            "temporal": TemporalBounds(valid_from=start, basis="explicit_open_end"),
        }
    )


def test_exact_identity_scopes_typed_state_without_using_latest_mention(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
) -> None:
    _name(engine, source_fixture)
    old = store.remember(RememberRequest(idempotency_key="previous", draft=draft)).affected[0]
    transition = "2026-06-01T00:00:00.000000Z"
    new = store.supersede(
        SupersedeRequest(
            idempotency_key="explicit-transition",
            memory_id=old.memory_id,
            expected_revision=1,
            replacement=_value(draft, "replacement", transition),
            transition_evidence=draft.content.evidence,
            previous_valid_until=transition,
            reason="operator-confirmed evidence",
        )
    ).affected[1]
    conflict = store.remember(
        RememberRequest(
            idempotency_key="unresolved-conflict",
            draft=_value(draft, "another assertion", "2026-07-01T00:00:00.000000Z"),
        )
    ).affected[0]
    unrelated = EntityRef(store_id=store.character_id, entity_id=uuid4())
    engine.put_entity(store.character_id, Entity(reference=unrelated, revision=1, name="Lab cart"))
    linked = MemoryDraft.model_validate(
        {
            "content": {
                **draft.content.model_dump(),
                "payload": {
                    **draft.content.payload.model_dump(),
                    "subject": unrelated,
                    "predicate": "person.knows",
                    "value": EntityValue(value=source_fixture.subject),
                },
            },
            "temporal": draft.temporal,
        }
    )
    store.remember(RememberRequest(idempotency_key="mention-only", draft=linked))

    assert IDENTITY_RECALL in store.capabilities().supported_extensions
    assert set(_claims(store, "What does the sk-7 use currently?")) == {
        new.memory_id,
        conflict.memory_id,
    }
    assert _claims(store, "What about SK-70?") == []
    assert _claims(store, "What about missing assets?") == []
    wrong = _query("SK-7").model_copy(
        update={
            "options": RequestOptions(
                extensions={IDENTITY_RECALL: {"origin_store_ids": [str(uuid4())]}}
            )
        }
    )
    assert not store.search(wrong).results

    history = _query("SK-7", temporal=TemporalQuery(valid_at="2026-05-01T00:00:00.000000Z"))
    context = store.retrieve_context(
        ContextRequest(
            memory_query=history,
            memory_token_budget=800,
            include_pinned=False,
        )
    )
    assert [hit.memory.memory_id for hit in context.memories] == [old.memory_id]
    assert context.memories[0].memory.lifecycle == "superseded"
    assert context.memories[0].memory.content.evidence == draft.content.evidence
    assert context.fast_dependencies is not None
    assert store.lookup_related(
        RelatedRequest(
            intent="validate",
            dependencies=context.fast_dependencies,
        )
    ).valid
    assert not store.search(history.model_copy(update={"predicates": ("person.knows",)})).results

    store.forget(
        ForgetRequest(
            memory_id=old.memory_id,
            expected_revision=2,
            idempotency_key="forget",
            reason="operator",
        )
    )
    assert not store.search(history).results
    assert not store.lookup_related(
        RelatedRequest(
            intent="validate",
            dependencies=context.fast_dependencies,
        )
    ).valid


def test_identity_rechecks_expiry_after_source_header_reads(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _name(engine, source_fixture)
    library = source_fixture.message.source.source_store_id
    engine.revoke_source(store.character_id, library)
    clock.advance(5)
    expiry = timestamp_from_datetime(clock.now())
    clock.advance(-5)
    engine.grant_source(
        store.character_id,
        SourceGrant(
            references=(source_fixture.message.source,),
            capabilities=frozenset({"evidence_read"}),
            expires_at=expiry,
        ),
    )
    store.remember(RememberRequest(idempotency_key="now", draft=draft))
    assert _claims(store, "SK-7")

    original = engine.unavailable_sources

    def expire(
        references: tuple[SourceRef, ...],
        *,
        local_connection: sqlite3.Connection,
        local_store_id: UUID,
        call_budget: int,
    ) -> tuple[tuple[SourceRef, ...], int]:
        missing = original(
            references,
            local_connection=local_connection,
            local_store_id=local_store_id,
            call_budget=call_budget,
        )
        clock.advance(5)
        return missing

    monkeypatch.setattr(engine, "unavailable_sources", expire)
    assert not _claims(store, "SK-7")
    engine.revoke_source(store.character_id, library)
    assert not _claims(store, "SK-7")


def test_identity_fails_closed_without_available_proof(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _name(engine, source_fixture)
    store.remember(RememberRequest(idempotency_key="approved", draft=draft))

    def unavailable(
        references: tuple[SourceRef, ...], **_: object
    ) -> tuple[tuple[SourceRef, ...], int]:
        return references, 1

    monkeypatch.setattr(engine, "unavailable_sources", unavailable)
    response = store.search(_query("SK-7"))
    assert not response.results
    assert response.coverage.status == "partial"
    assert response.coverage.reason_codes == ("source_unavailable_or_changed",)
