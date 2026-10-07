"""Cross-store policy, temporal replacement and failure recovery acceptance."""

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from ember_memory_abi.archive import Message, NormalizedArchiveBatch
from ember_memory_abi.common import CanonicalId
from ember_memory_abi.errors import AccessDeniedError, InvalidPredicateError, RevisionConflictError
from ember_memory_abi.evidence import Evidence, EvidenceAssertion, SourceRef, make_evidence_span
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ContextRequest,
    ForgetRequest,
    MemoryQuery,
    PageRequest,
    PinRequest,
    RememberRequest,
    SupersedeRequest,
    TemporalQuery,
    UpdateRequest,
)
from ember_memory_abi.memory import Entity, EntityRef, MemoryDraft, StringValue
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.engine import CanonicalEngine
from ember_memory_engine.errors import CorruptStoreError, StorageError
from ember_memory_engine.models import MutationAuthority, SourceGrant

from .conftest import TEXT, SourceFixture, TestClock


def _changed(draft: MemoryDraft, value: str, *, importance: float = 0.7) -> MemoryDraft:
    return MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "content": {
                **draft.content.model_dump(),
                "importance": importance,
                "payload": {
                    **draft.content.payload.model_dump(),
                    "value": StringValue(value=value),
                },
            },
        }
    )


def _remember(store: SqliteMemoryStore, draft: MemoryDraft, key: str) -> CanonicalId:
    return store.remember(RememberRequest(idempotency_key=key, draft=draft)).affected[0].memory_id


def _fresh_source(engine: CanonicalEngine, fixture: SourceFixture) -> SourceRef:
    message = Message.model_validate(
        {
            **fixture.message.model_dump(),
            "source": {**fixture.message.source.model_dump(), "record_id": uuid4()},
            "parts": ({**fixture.message.parts[0].model_dump(), "part_id": uuid4()},),
        }
    )
    batch = NormalizedArchiveBatch.model_validate(
        {
            **fixture.batch.model_dump(),
            "idempotency_key": "fresh-source",
            "records": (message,),
            "resume_cursor": {**fixture.batch.resume_cursor.model_dump(), "next_record_index": 4},
        }
    )
    engine.archive(batch.source_store_id).write_batch(batch)
    engine.grant_source(
        fixture.character_id,
        SourceGrant(
            references=(message.source,),
            capabilities=frozenset({"evidence_read", "archive_search"}),
        ),
    )
    return SourceRef.model_validate(
        {**message.source.model_dump(), "part_id": message.parts[0].part_id}
    )


def test_recent_orders_by_time_and_context_tracks_archive_dependencies(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    clock: TestClock,
    source_fixture: SourceFixture,
) -> None:
    earlier = _remember(store, _changed(draft, "EARLY", importance=0.99), "early")
    clock.advance()
    later = _remember(store, _changed(draft, "LATE", importance=0.1), "late")
    assert store.search(MemoryQuery(limit=1)).results[0].memory.memory_id == earlier
    assert store.get_recent(PageRequest(limit=1)).results[0].memory.memory_id == later
    context = store.retrieve_context(
        ContextRequest(
            memory_query=MemoryQuery(),
            memory_token_budget=1,
            archive_query=ArchiveQuery(text="SYNTHETIC"),
            archive_token_budget=20000,
        )
    )
    assert context.memories == () and len(context.archive) == 1
    assert context.dependencies == (source_fixture.message.source,)


def test_source_revision_import_does_not_expand_snapshot_grants(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    identity = _remember(store, draft, "original")
    updated = Message.model_validate(
        {
            **source_fixture.message.model_dump(),
            "source": {**source_fixture.message.source.model_dump(), "record_revision": 2},
        }
    )
    batch = NormalizedArchiveBatch.model_validate(
        {
            **source_fixture.batch.model_dump(),
            "idempotency_key": "revision-two",
            "records": (updated,),
            "resume_cursor": {
                **source_fixture.batch.resume_cursor.model_dump(),
                "next_record_index": 4,
            },
        }
    )
    writer = engine.archive(batch.source_store_id)
    writer.write_batch(batch)
    results = store.search_archive(ArchiveQuery()).results
    assert len(results) == 1 and results[0].record.source.record_revision == 1
    assertion = draft.content.evidence[0]
    evidence = Evidence.model_validate(
        {
            **assertion.evidence.model_dump(),
            "evidence_id": uuid4(),
            "source": {**source_fixture.evidence_ref.model_dump(), "record_revision": 2},
        }
    )
    denied = MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "content": {
                **draft.content.model_dump(),
                "evidence": (
                    EvidenceAssertion.model_validate(
                        {**assertion.model_dump(), "evidence": evidence}
                    ),
                ),
            },
        }
    )
    with pytest.raises(AccessDeniedError):
        _remember(store, denied, "ungranted-revision")
    assert store.get_sources(identity)[0].quote == TEXT
    stored = writer.get_record(updated.source)
    assert stored.created_time.value is None
    assert stored.recorded_at != updated.recorded_at


def test_failure_after_revision_rolls_back_receipt_outbox_and_belief(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    def counts(connection: sqlite3.Connection) -> tuple[int, ...]:
        return tuple(
            connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in (
                "memories",
                "memory_revisions",
                "mutation_keys",
                "index_outbox",
                "audit_events",
            )
        )

    before = store.database.read(counts)
    store.database.write(
        lambda connection: connection.execute(
            "CREATE TRIGGER fail_receipt BEFORE INSERT ON mutation_keys BEGIN "
            "SELECT RAISE(ABORT,'synthetic_failure'); END"
        )
    )
    with pytest.raises(StorageError):
        _remember(store, draft, "atomic")
    assert store.database.read(counts) == before
    store.database.write(lambda connection: connection.execute("DROP TRIGGER fail_receipt"))
    assert _remember(store, draft, "atomic")


def test_concurrent_expected_revision_has_one_winner(
    store: SqliteMemoryStore, draft: MemoryDraft
) -> None:
    identity = _remember(store, draft, "first")
    requests = [
        UpdateRequest(
            idempotency_key="update-" + str(index),
            memory_id=identity,
            expected_revision=1,
            draft=_changed(draft, str(index)),
            reason="manual",
        )
        for index in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [callers.submit(store.update, request) for request in requests]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result().outcome)
            except RevisionConflictError:
                outcomes.append("revision_conflict")
    assert sorted(outcomes) == ["applied", "revision_conflict"]
    assert store.get(identity).revision == 2


def test_explicit_supersession_preserves_historical_truth_and_recorded_view(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    old = _remember(store, draft, "old")
    replacement = MemoryDraft.model_validate(
        {
            **_changed(draft, "SYNTHETIC_B").model_dump(),
            "temporal": TemporalBounds(
                valid_from="2026-06-01T00:00:00.000000Z", basis="explicit_open_end"
            ),
        }
    )
    receipt = store.supersede(
        SupersedeRequest(
            idempotency_key="transition",
            memory_id=old,
            expected_revision=1,
            replacement=replacement,
            transition_evidence=draft.content.evidence,
            previous_valid_until="2026-06-01T00:00:00.000000Z",
            reason="confirmed_transition",
        )
    )
    new = receipt.affected[1].memory_id
    assert new != old and store.get(old).lifecycle == "superseded"
    assert store.search(MemoryQuery()).results[0].memory.memory_id == new
    historic = store.search(
        MemoryQuery(temporal=TemporalQuery(valid_at="2026-05-01T00:00:00.000000Z"))
    )
    assert [item.memory.memory_id for item in historic.results] == [old]
    recorded = store.search(
        MemoryQuery(temporal=TemporalQuery(recorded_before=receipt.recorded_at))
    )
    assert len(recorded.results) == 1 and recorded.results[0].memory.lifecycle == "active"
    assert store.get(old, revision=1).temporal.valid_until is None


def test_plural_ownership_predicate_cannot_use_replacement_semantics(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    plural = MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "content": {
                **draft.content.model_dump(),
                "payload": {**draft.content.payload.model_dump(), "predicate": "hardware.owns_gpu"},
            },
        }
    )
    identity = _remember(store, plural, "owns")
    with pytest.raises(InvalidPredicateError):
        store.supersede(
            SupersedeRequest(
                idempotency_key="invalid-replacement",
                memory_id=identity,
                expected_revision=1,
                replacement=_changed(plural, "SYNTHETIC_B"),
                transition_evidence=draft.content.evidence,
                reason="manual",
            )
        )
    assert store.get(identity).revision == 1


def test_forgetting_prior_revision_blocks_old_claim_from_fresh_source(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    identity = _remember(store, draft, "first")
    store.update(
        UpdateRequest(
            idempotency_key="correct",
            memory_id=identity,
            expected_revision=1,
            draft=_changed(draft, "SYNTHETIC_B"),
            reason="manual",
        )
    )
    assert len(store.preview_forget(identity).claim_fingerprints) == 2
    store.forget(
        ForgetRequest(
            idempotency_key="forget-history",
            memory_id=identity,
            expected_revision=2,
            reason="manual",
        )
    )
    fresh = _fresh_source(engine, source_fixture)
    assertion = draft.content.evidence[0]
    new_evidence = Evidence(
        evidence_id=uuid4(), source=fresh, span=make_evidence_span(TEXT, 0, len(TEXT))
    )
    replay = MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "content": {
                **draft.content.model_dump(),
                "evidence": (
                    EvidenceAssertion.model_validate(
                        {**assertion.model_dump(), "evidence": new_evidence}
                    ),
                ),
            },
        }
    )
    with pytest.raises(AccessDeniedError):
        _remember(store, replay, "old-claim-fresh-origin")


def test_forgetting_does_not_delete_library_or_suppress_another_character(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    first = _remember(store, draft, "first")
    other = engine.create_character()
    subject = EntityRef(store_id=other.store_id, entity_id=uuid4())
    engine.put_entity(
        other.store_id, Entity(reference=subject, revision=1, name="Other synthetic user")
    )
    engine.grant_source(
        other.store_id,
        SourceGrant(
            references=(source_fixture.message.source,),
            capabilities=frozenset({"evidence_read", "archive_search"}),
        ),
    )
    other_store = engine.character(
        other.store_id, authority=MutationAuthority(mode="confirmed_user")
    )
    other_draft = MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "content": {
                **draft.content.model_dump(),
                "payload": {**draft.content.payload.model_dump(), "subject": subject},
            },
        }
    )
    second = _remember(other_store, other_draft, "second")
    store.forget(
        ForgetRequest(
            idempotency_key="forget-first", memory_id=first, expected_revision=1, reason="manual"
        )
    )
    assert other_store.get_sources(second)[0].quote == TEXT
    assert len(other_store.search_archive(ArchiveQuery()).results) == 1
    assert engine.archive(source_fixture.batch.source_store_id).get_record(
        source_fixture.message.source
    )


def test_registered_missing_store_fails_without_recreating_it(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
) -> None:
    root = engine.root
    path = root / "characters" / str(source_fixture.character_id) / "memory.db"
    engine.close()
    path.rename(path.with_suffix(".held"))
    with pytest.raises(CorruptStoreError):
        CanonicalEngine(root)
    assert not path.exists()


def test_sql_immutability_and_corrupt_payload_fail_closed(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    identity = _remember(store, draft, "first")
    with pytest.raises(StorageError):
        store.database.write(
            lambda connection: connection.execute(
                "UPDATE memory_revisions SET payload_json='{}' WHERE memory_id=?", (str(identity),)
            )
        )
    assert store.get(identity).revision == 1

    # Simulate disk/administrative corruption after bypassing the engine's immutable trigger.
    def corrupt(connection: sqlite3.Connection) -> None:
        connection.execute("DROP TRIGGER immutable_memory_revisions_update")
        connection.execute(
            "UPDATE memory_revisions SET payload_sha256=? WHERE memory_id=?",
            ("0" * 64, str(identity)),
        )

    store.database.write(corrupt)
    with pytest.raises(CorruptStoreError):
        store.get(identity)


@pytest.mark.integration
def test_process_death_releases_ownership_and_rolls_back_wal_transaction(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    clock: TestClock,
) -> None:
    root, library = engine.root, source_fixture.batch.source_store_id
    engine.close()
    code = """
import os, sys
from pathlib import Path
from uuid import UUID, uuid4
from ember_memory_engine.engine import CanonicalEngine
engine = CanonicalEngine(Path(sys.argv[1]))
writer = engine.archive(UUID(sys.argv[2]))
def crash(connection):
    connection.execute('BEGIN IMMEDIATE')
    connection.execute('INSERT INTO audit_events VALUES(?,?,?,?,?)',
        (str(uuid4()), 'uncommitted_crash', None, 1, 'synthetic'))
    os._exit(7)
writer.database.write(crash)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(root), str(library)],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert process.returncode == 7, process.stderr.decode()
    with CanonicalEngine(root, clock=clock) as recovered:
        writer = recovered.archive(library)
        assert writer.get_record(source_fixture.message.source)
        assert (
            writer.database.read(
                lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE operation='uncommitted_crash'"
                ).fetchone()[0]
            )
            == 0
        )


def test_missing_library_preserves_adopted_state_and_reports_unavailable_evidence(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
    clock: TestClock,
) -> None:
    identity = _remember(store, draft, "adopted")
    root = engine.root
    library_id = source_fixture.batch.source_store_id
    path = root / "libraries" / str(library_id) / "memory.db"
    engine.close()
    path.rename(path.with_suffix(".held"))
    with CanonicalEngine(root, clock=clock) as recovered:
        client = recovered.character(store.character_id)
        assert client.get(identity).content == draft.content
        assert client.get_sources(identity)[0].availability == "unavailable"
        assert client.search_archive(ArchiveQuery()).results == ()
        assert (
            next(info for info in recovered.stores() if info.store_id == library_id).availability
            == "unavailable"
        )
    assert not path.exists()


def test_native_evidence_uses_current_writer_without_pool_deadlock(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    writer = engine.archive(store.character_id)
    message = Message.model_validate(
        {
            **source_fixture.message.model_dump(),
            "source": {
                **source_fixture.message.source.model_dump(),
                "source_store_id": store.character_id,
            },
            "conversation": {
                **source_fixture.message.conversation.model_dump(),
                "source_store_id": store.character_id,
            },
        }
    )
    batch = NormalizedArchiveBatch.model_validate(
        {
            **source_fixture.batch.model_dump(),
            "source_store_id": store.character_id,
            "source_namespace": writer.namespace,
            "records": (message,),
        }
    )
    writer.write_batch(batch)
    assertion = draft.content.evidence[0]
    evidence = Evidence.model_validate(
        {
            **assertion.evidence.model_dump(),
            "evidence_id": uuid4(),
            "source": {
                **assertion.evidence.source.model_dump(),
                "source_store_id": store.character_id,
            },
        }
    )
    native = MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "content": {
                **draft.content.model_dump(),
                "evidence": (
                    EvidenceAssertion.model_validate(
                        {**assertion.model_dump(), "evidence": evidence}
                    ),
                ),
            },
        }
    )
    # A finite caller wait catches recursion onto the same single writer pool.
    with ThreadPoolExecutor(max_workers=1) as callers:
        identity = callers.submit(_remember, store, native, "native").result(timeout=3)
    assert store.get_sources(identity)[0].quote == TEXT
    assert len(store.search_archive(ArchiveQuery(text="SYNTHETIC")).results) == 2


def test_transition_evidence_remains_in_provenance_and_context_dependencies(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    old = _remember(store, draft, "old")
    fresh = _fresh_source(engine, source_fixture)
    transition = EvidenceAssertion.model_validate(
        {
            **draft.content.evidence[0].model_dump(),
            "relation": "transition",
            "evidence": Evidence(
                evidence_id=uuid4(), source=fresh, span=make_evidence_span(TEXT, 0, len(TEXT))
            ),
        }
    )
    receipt = store.supersede(
        SupersedeRequest(
            idempotency_key="different-transition-source",
            memory_id=old,
            expected_revision=1,
            replacement=_changed(draft, "SYNTHETIC_B"),
            transition_evidence=(transition,),
            reason="manual",
        )
    )
    new = receipt.affected[1].memory_id
    assert len(store.get_sources(new)) == 2
    context = store.retrieve_context(
        ContextRequest(memory_query=MemoryQuery(), memory_token_budget=20000)
    )
    assert fresh in context.dependencies
    assert fresh in store.preview_forget(new).source_parts


def test_registered_alias_suppression_cannot_be_bypassed_with_fresh_evidence(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    alias = EntityRef(store_id=store.character_id, entity_id=uuid4())
    engine.put_entity(
        store.character_id,
        Entity(reference=alias, revision=1, name="SYNTHETIC_A", aliases=("SYNTHETIC_ALIAS",)),
    )
    identity = _remember(store, draft, "first")
    assert len(store.preview_forget(identity).claim_fingerprints) == 2
    store.forget(
        ForgetRequest(
            idempotency_key="forget-aliases",
            memory_id=identity,
            expected_revision=1,
            reason="manual",
        )
    )
    fresh = _fresh_source(engine, source_fixture)
    assertion = draft.content.evidence[0]
    alternate = _changed(draft, "SYNTHETIC_ALIAS")
    alternate = MemoryDraft.model_validate(
        {
            **alternate.model_dump(),
            "content": {
                **alternate.content.model_dump(),
                "evidence": (
                    EvidenceAssertion.model_validate(
                        {
                            **assertion.model_dump(),
                            "evidence": Evidence(
                                evidence_id=uuid4(),
                                source=fresh,
                                span=make_evidence_span(TEXT, 0, len(TEXT)),
                            ),
                        }
                    ),
                ),
            },
        }
    )
    with pytest.raises(AccessDeniedError):
        _remember(store, alternate, "alias-fresh-source")


def test_future_pin_cannot_bypass_current_temporal_visibility(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    future = MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "temporal": TemporalBounds(
                valid_from="2027-01-01T00:00:00.000000Z", basis="explicit_open_end"
            ),
        }
    )
    identity = _remember(store, future, "future")
    store.pin(PinRequest(idempotency_key="pin-future", memory_id=identity, expected_revision=1))
    assert store.get_pinned(PageRequest()) == ()
    assert (
        store.retrieve_context(
            ContextRequest(memory_query=MemoryQuery(), memory_token_budget=20000)
        ).memories
        == ()
    )


def test_fallback_rejects_unsupported_cursor_instead_of_repeating_first_page(
    store: SqliteMemoryStore,
) -> None:
    from ember_memory_engine.errors import UnsupportedQueryError

    with pytest.raises(UnsupportedQueryError):
        store.search(MemoryQuery(cursor="not-an-engine-cursor"))
    with pytest.raises(UnsupportedQueryError):
        store.search_archive(ArchiveQuery(cursor="not-an-engine-cursor"))


@pytest.mark.parametrize("table", ("entities", "evidence_spans"))
def test_maintenance_replay_verifies_immutable_evidence_and_entity_bytes(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
    table: str,
) -> None:
    _remember(store, draft, "first")
    writer = (
        engine.archive(source_fixture.batch.source_store_id) if table == "evidence_spans" else store
    )

    def corrupt(connection: sqlite3.Connection) -> None:
        connection.execute("DROP TRIGGER immutable_" + table + "_update")
        connection.execute("UPDATE " + table + " SET payload_sha256=?", ("0" * 64,))

    writer.database.write(corrupt)
    with pytest.raises(CorruptStoreError) as caught:
        if table == "evidence_spans":
            _remember(store, draft, "replay-with-corruption")
        else:
            engine.put_entity(
                store.character_id,
                Entity(reference=source_fixture.subject, revision=1, name="Synthetic user"),
            )
    assert TEXT not in str(caught.value)
    assert str(engine.root) not in str(caught.value)
