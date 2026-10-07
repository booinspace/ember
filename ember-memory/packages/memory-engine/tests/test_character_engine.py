"""Character authority, exact evidence, idempotency and conservative forgetting acceptance."""

import pytest

from ember_memory_abi.errors import (
    AccessDeniedError,
    InvalidEvidenceError,
    NotFoundError,
    RevisionConflictError,
)
from ember_memory_abi.evidence import Evidence, EvidenceAssertion, EvidenceSpan
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ContextRequest,
    DisableRequest,
    ForgetRequest,
    MemoryQuery,
    MutationReceipt,
    PageRequest,
    PinRequest,
    RememberRequest,
    TemporalQuery,
    UpdateRequest,
)
from ember_memory_abi.memory import MemoryDraft, StringValue
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.engine import CanonicalEngine
from ember_memory_engine.errors import IdempotencyConflictError
from ember_memory_engine.models import MutationAuthority, SourceGrant

from .conftest import TEXT, SourceFixture, TestClock


def _remember(
    store: SqliteMemoryStore, draft: MemoryDraft, key: str = "remember"
) -> MutationReceipt:
    return store.remember(RememberRequest(idempotency_key=key, draft=draft))


def test_read_only_binding_cannot_activate_model_supplied_claims(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft
) -> None:
    reader = engine.character(source_fixture.character_id)
    assert reader.capabilities().mutations == ()
    with pytest.raises(AccessDeniedError):
        _remember(reader, draft)


def test_manual_belief_sources_restart_and_receipt_survive(
    store: SqliteMemoryStore, draft: MemoryDraft, engine: CanonicalEngine, clock: TestClock
) -> None:
    receipt = _remember(store, draft)
    identity = receipt.affected[0].memory_id
    assert store.get_sources(identity)[0].quote == TEXT
    assert _remember(store, draft).outcome == "already_applied"
    root, character = engine.root, store.character_id
    original = store.get(identity)
    assert original.revision == 1
    engine.close()
    with CanonicalEngine(root, clock=clock) as reopened:
        client = reopened.character(character, authority=MutationAuthority(mode="confirmed_user"))
        assert client.get(identity) == original
        assert client.get_sources(identity)[0].quote == TEXT
        assert _remember(client, draft).outcome == "already_applied"


def test_compare_and_swap_and_changed_idempotency_content(
    store: SqliteMemoryStore, draft: MemoryDraft
) -> None:
    first = _remember(store, draft)
    identity = first.affected[0].memory_id
    request = UpdateRequest(
        idempotency_key="update",
        memory_id=identity,
        expected_revision=1,
        draft=draft,
        reason="manual_confirmation",
    )
    assert store.update(request).affected[0].revision == 2
    assert store.update(request).outcome == "already_applied"
    with pytest.raises(RevisionConflictError):
        store.update(
            UpdateRequest.model_validate({**request.model_dump(), "idempotency_key": "stale"})
        )
    changed = MemoryDraft.model_validate(
        {**draft.model_dump(), "content": {**draft.content.model_dump(), "importance": 0.8}}
    )
    with pytest.raises(IdempotencyConflictError):
        _remember(store, changed)
    assert store.get(identity, revision=1).revision == 1
    assert store.get(identity).revision == 2


def test_cross_character_and_missing_grants_are_denied(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    identity = _remember(store, draft).affected[0].memory_id
    other = engine.create_character()
    other_store = engine.character(
        other.store_id, authority=MutationAuthority(mode="confirmed_user")
    )
    with pytest.raises(NotFoundError):
        other_store.get(identity)
    with pytest.raises(AccessDeniedError):
        _remember(other_store, draft)
    assert other_store.search_archive(ArchiveQuery()).results == ()


def test_unverified_or_tampered_source_evidence_rolls_back(
    store: SqliteMemoryStore, draft: MemoryDraft
) -> None:
    evidence = draft.content.evidence[0].evidence
    bad = Evidence(
        evidence_id=evidence.evidence_id,
        source=evidence.source,
        span=EvidenceSpan.model_validate(
            {**evidence.span.model_dump(), "quoted_span_sha256": "0" * 64}
        ),
    )
    assertion = EvidenceAssertion.model_validate(
        {**draft.content.evidence[0].model_dump(), "evidence": bad}
    )
    invalid = MemoryDraft.model_validate(
        {**draft.model_dump(), "content": {**draft.content.model_dump(), "evidence": (assertion,)}}
    )
    with pytest.raises(InvalidEvidenceError):
        _remember(store, invalid)
    assert store.search(MemoryQuery()).results == ()
    assert _remember(store, draft).affected[0].revision == 1


def test_pins_disable_and_forget_do_not_resurrect_recalls(
    store: SqliteMemoryStore, draft: MemoryDraft
) -> None:
    identity = _remember(store, draft).affected[0].memory_id
    store.pin(PinRequest(idempotency_key="pin", memory_id=identity, expected_revision=1))
    assert len(store.get_pinned(PageRequest())) == 1
    store.disable(
        DisableRequest(
            idempotency_key="disable", memory_id=identity, expected_revision=1, reason="manual"
        )
    )
    assert store.get_pinned(PageRequest()) == ()
    assert store.search(MemoryQuery()).results == ()
    assert store.get(identity, revision=1).lifecycle == "active"
    store.forget(
        ForgetRequest(
            idempotency_key="forget", memory_id=identity, expected_revision=2, reason="manual"
        )
    )
    with pytest.raises(AccessDeniedError):
        store.get(identity, revision=1)
    with pytest.raises(AccessDeniedError):
        _remember(store, draft, key="cannot-reseed")


def test_forget_suppresses_related_claims_and_original_archive_parts(
    store: SqliteMemoryStore, draft: MemoryDraft
) -> None:
    first = _remember(store, draft, "first").affected[0].memory_id
    second = _remember(store, draft, "second").affected[0].memory_id
    assert set(store.preview_forget(first).affected_memory_ids) == {first, second}
    store.pin(PinRequest(idempotency_key="pin-second", memory_id=second, expected_revision=1))
    store.forget(
        ForgetRequest(
            idempotency_key="forget-first", memory_id=first, expected_revision=1, reason="manual"
        )
    )
    assert store.get_pinned(PageRequest()) == ()
    assert store.search_archive(ArchiveQuery(text="SYNTHETIC")).results == ()
    with pytest.raises(AccessDeniedError):
        store.get(second)


def test_evidence_and_archive_search_are_separate_grants(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    identity = _remember(store, draft).affected[0].memory_id
    engine.revoke_source(store.character_id, source_fixture.evidence_ref.source_store_id)
    with pytest.raises(AccessDeniedError):
        store.get_sources(identity)
    assert store.get(identity).content == draft.content
    assert store.search_archive(ArchiveQuery()).results == ()
    engine.grant_source(
        store.character_id,
        SourceGrant(
            references=(source_fixture.evidence_ref,), capabilities=frozenset({"evidence_read"})
        ),
    )
    assert store.get_sources(identity)[0].quote == TEXT
    assert store.search_archive(ArchiveQuery()).results == ()


def test_budgeted_degraded_context_does_not_need_indexes(
    store: SqliteMemoryStore, draft: MemoryDraft, engine: CanonicalEngine
) -> None:
    identity = _remember(store, draft).affected[0].memory_id
    search_path = engine.root / "characters" / str(store.character_id) / "indexes" / "search.db"
    assert not search_path.exists()
    response = store.search(MemoryQuery(text="SYNTHETIC_A"))
    assert response.results[0].memory.memory_id == identity
    assert response.coverage.index_status == "degraded"
    tiny = store.retrieve_context(ContextRequest(memory_query=MemoryQuery(), memory_token_budget=1))
    assert tiny.memories == ()
    context = store.retrieve_context(
        ContextRequest(memory_query=MemoryQuery(), memory_token_budget=20000)
    )
    assert context.memories[0].memory.memory_id == identity
    assert context.dependencies == (draft.content.evidence[0].evidence.source,)


def test_valid_and_recorded_time_are_independent_primitives(
    store: SqliteMemoryStore, draft: MemoryDraft, clock: TestClock
) -> None:
    first = _remember(store, draft)
    identity = first.affected[0].memory_id
    before = store.get(identity).temporal.recorded_at
    clock.advance()
    replacement = MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "content": {
                **draft.content.model_dump(),
                "payload": {
                    **draft.content.payload.model_dump(),
                    "value": StringValue(value="SYNTHETIC_B"),
                },
            },
        }
    )
    store.update(
        UpdateRequest(
            idempotency_key="correction",
            memory_id=identity,
            expected_revision=1,
            draft=replacement,
            reason="manual_correction",
        )
    )
    current = store.get(identity)
    assert current.temporal.recorded_at > before
    prior = store.search(
        MemoryQuery(temporal=TemporalQuery(recorded_before=current.temporal.recorded_at))
    )
    assert prior.results[0].memory.content.payload.value.value == "SYNTHETIC_A"
    assert (
        store.search(MemoryQuery()).results[0].memory.content.payload.value.value == "SYNTHETIC_B"
    )
    assert (
        store.search(
            MemoryQuery(temporal=TemporalQuery(valid_at="2025-12-31T23:59:59.000000Z"))
        ).results
        == ()
    )
