"""Rank-first archive reads preserve exact grants, order, provenance and fallback."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest

from ember_memory_abi.archive import ContentPart, Document, SourceRecord
from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.errors import AccessDeniedError, DeadlineExceededError
from ember_memory_abi.evidence import Evidence, SourceRef, source_text_sha256
from ember_memory_abi.interfaces import ArchiveQuery, ForgetRequest, RememberRequest, RequestOptions
from ember_memory_abi.memory import ArchiveChunk, MemoryDraft
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import indexed_retrieval
from ember_memory_engine.bundles import BundleService, restore_bundles
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.embedding import file_sha256
from ember_memory_engine.engine import CanonicalEngine
from ember_memory_engine.errors import EmbeddingError, EngineClosedError
from ember_memory_engine.index_storage import IndexStore, connect
from ember_memory_engine.library import LibraryService, SeedRequest
from ember_memory_engine.models import SourceGrant

from .conftest import STAMP, SourceFixture, TestClock
from .test_libraries_bundles import _approved  # pyright: ignore[reportPrivateUsage]
from .test_phase_five_search import SyntheticEncoder


def _document(library: UUID, *texts: str) -> Document:
    return Document(
        source=SourceRef(
            source_store_id=library, record_kind="document", record_id=uuid4(), record_revision=1
        ),
        recorded_at=STAMP,
        parts=tuple(
            ContentPart(
                part_id=uuid4(),
                kind="text",
                status="available",
                text=text,
                content_sha256=source_text_sha256(text),
            )
            for text in texts
        ),
    )


def _append(engine: CanonicalEngine, fixture: SourceFixture, *records: Document) -> None:
    library = records[0].source.source_store_id
    writer = engine.archive(library)
    writer.write_batch(
        fixture.batch.model_copy(
            update={
                "source_store_id": library,
                "source_namespace": writer.namespace,
                "idempotency_key": f"documents-{uuid4()}",
                "records": records,
                "resume_cursor": fixture.batch.resume_cursor.model_copy(
                    update={
                        "component_sha256": source_text_sha256(str(uuid4())),
                        "next_record_index": len(records),
                    }
                ),
            }
        )
    )


def _grant(
    engine: CanonicalEngine, character: UUID, *documents: Document, expires_at: str | None = None
) -> None:
    engine.grant_source(
        character,
        SourceGrant(
            references=tuple(document.source for document in documents),
            capabilities=frozenset({"archive_search"}),
            expires_at=expires_at,
        ),
    )


@pytest.mark.parametrize("mode", ["lexical", "semantic"])
def test_archive_backfills_candidate_whose_grant_expires_during_exact_read(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["lexical", "semantic"],
) -> None:
    library = source_fixture.batch.source_store_id
    names = ("orchard", "valley", "hill", "ridge")
    docs = tuple(_document(library, f"evergreen {name}") for name in names)
    _append(engine, source_fixture, *docs)
    _grant(engine, store.character_id, *docs)
    encoder = SyntheticEncoder()
    engine.encoding.encoder = encoder
    engine.rebuild_index(library, encoder)
    query = ArchiveQuery(
        text="evergreen",
        mode=mode,
        record_kinds=("document",),
        source_store_ids=(library,),
        limit=4,
    )
    original = store.search_archive(query)
    top, second, backup, _ = (hit.record for hit in original.results)

    def source_id(record: SourceRecord | ArchiveChunk) -> UUID:
        return (
            record.source_spans[0].evidence.source.record_id
            if isinstance(record, ArchiveChunk)
            else record.source.record_id
        )

    expiring = next(doc for doc in docs if doc.source.record_id == source_id(top))
    _grant(
        engine,
        store.character_id,
        expiring,
        expires_at=timestamp_from_datetime(clock.now() + timedelta(seconds=1)),
    )
    resolve, validate = engine.resolve, engine.validate
    reads: list[UUID] = []
    checks: list[UUID] = []

    def advance(reference: SourceRef) -> SourceRecord:
        reads.append(reference.record_id)
        record = resolve(reference)
        if reference.record_id == source_id(second):
            clock.advance(2)
        return record

    def record_validation(evidence: Evidence) -> str:
        checks.append(evidence.source.record_id)
        return validate(evidence)

    monkeypatch.setattr(engine, "resolve", advance)
    monkeypatch.setattr(engine, "validate", record_validation)
    after = store.search_archive(query.model_copy(update={"limit": 2}))
    assert after.coverage.status == original.coverage.status == "complete"
    assert after.coverage.reason_codes == original.coverage.reason_codes == ()
    assert after.results == original.results[1:3]
    assert reads == [source_id(top), source_id(second), source_id(backup)]
    assert checks == (reads if mode == "semantic" else [])


def test_archive_batches_each_store_and_only_hydrates_ranked_unique_winners(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = sorted(
        (source_fixture.batch.source_store_id, engine.create_library("rank-first").store_id)
    )
    text = "evergreen café"
    original = _document(first, text, text)
    revised = _document(first, text).model_copy(
        update={"source": original.source.model_copy(update={"record_revision": 2})}
    )
    other = _document(second, text)
    other = other.model_copy(
        update={"source": other.source.model_copy(update={"record_id": original.source.record_id})}
    )
    second_docs = (other, _document(second, text), _document(second, text))
    encoder = SyntheticEncoder()
    for library, prefix in ((first, (original, revised)), (second, second_docs)):
        docs = (*prefix, *(_document(library, text) for _ in range(40)))
        _append(engine, source_fixture, _document(library, text), *docs)
        _grant(engine, store.character_id, *docs)
        engine.rebuild_index(library, encoder)

    unavailable = engine.create_library("rank-first-unindexed").store_id
    unindexed = _document(unavailable, text)
    _append(engine, source_fixture, unindexed)
    _grant(engine, store.character_id, unindexed)

    statements: list[str] = []
    reads: list[SourceRef] = []
    permissions: list[SourceRef] = []
    resolve = engine.resolve
    source_permitted = store.state.source_permitted

    def traced[**P](opener: Callable[P, sqlite3.Connection]) -> Callable[P, sqlite3.Connection]:
        def open_traced(*args: P.args, **kwargs: P.kwargs) -> sqlite3.Connection:
            connection = opener(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        return open_traced

    def record_read(reference: SourceRef) -> SourceRecord:
        reads.append(reference)
        return resolve(reference)

    def record_permission(
        connection: sqlite3.Connection, reference: SourceRef, capability: str
    ) -> bool:
        assert capability == "archive_search"
        permissions.append(reference)
        return source_permitted(connection, reference, capability)

    monkeypatch.setattr(sqlite3, "connect", traced(sqlite3.connect))
    monkeypatch.setattr(engine, "resolve", record_read)
    monkeypatch.setattr(store.state, "source_permitted", record_permission)
    query = ArchiveQuery(
        text=text, mode="literal", source_store_ids=(first, second, unavailable), limit=5
    )
    certified = store.search_archive(query)
    expected = (original, second_docs[0], second_docs[1], revised, second_docs[2])
    expected_ranks = (1, 1, 2, 3, 3)
    assert certified.coverage.status == "partial"
    assert certified.coverage.index_status == "available"
    assert certified.coverage.reason_codes == ("source_index_unavailable",)
    for item, document, rank in zip(certified.results, expected, expected_ranks, strict=True):
        assert item.record == resolve(document.source)
        assert item.score == 1.0 / (60 + rank)
        assert item.ranking[0].extensions == {"ember.rank": rank}
    assert [ref.identity_key()[:4] for ref in reads] == [
        doc.source.identity_key()[:4] for doc in expected
    ]
    assert permissions == [*reads, *(doc.source for doc in expected)]
    batch_sql = "FROM documents d WHERE d.id IN ("
    payload_sql = "SELECT payload_json FROM documents WHERE id="
    batches = [sql for sql in statements if batch_sql in sql]
    assert len(batches) == 2
    assert all("payload_json" not in sql for sql in batches)
    assert sum(sql.startswith(payload_sql) for sql in statements) == 5

    # A reopened store has the same immutable generations but not their hash certificates.
    for library in (first, second):
        index = engine.indexes[library]
        monkeypatch.setitem(engine.indexes, library, IndexStore(index.canonical_path, library))
    statements.clear()
    reads.clear()
    permissions.clear()
    with monkeypatch.context() as cold:
        cold.setattr(indexed_retrieval, "_ARCHIVE_CERTIFY_BYTES", 1)
        assert store.search_archive(query) == certified
    assert len(reads) == 80  # 40 eligible literal candidates per store, including duplicate parts.
    assert permissions == [*reads, *(doc.source for doc in expected)]
    batches = [sql for sql in statements if batch_sql in sql]
    assert len(batches) == 2
    assert all("payload_json" in sql for sql in batches)
    assert not any(sql.startswith(payload_sql) for sql in statements)

    # Small frozen generations can certify after reopen, without rebuild.
    statements.clear()
    reads.clear()
    permissions.clear()
    assert store.search_archive(query) == certified
    assert len(reads) == 5
    assert permissions == [*reads, *(doc.source for doc in expected)]
    assert sum(sql.startswith(payload_sql) for sql in statements) == 5
    for library in (first, second):
        index = engine.indexes[library]
        assert index.certified(*index.active(), cached_only=True)


def test_archive_backfills_when_exact_record_has_a_suppressed_unprojected_part(
    engine: CanonicalEngine, store: SqliteMemoryStore, source_fixture: SourceFixture
) -> None:
    library = source_fixture.batch.source_store_id
    blocked = _document(library, "evergreen", "")
    backup = _document(library, "evergreen")
    _append(engine, source_fixture, blocked, backup)
    _grant(engine, store.character_id, blocked, backup)
    encoder = SyntheticEncoder()
    engine.rebuild_index(library, encoder)
    query = ArchiveQuery(text="evergreen", mode="literal", source_store_ids=(library,), limit=2)
    original = store.search_archive(query)
    assert len(original.results) == 2 and original.results[0].record == engine.resolve(
        blocked.source
    )

    sibling = blocked.source.model_copy(update={"part_id": blocked.parts[1].part_id})
    key = store.state.source_dependency_key(sibling)

    def suppress(connection: sqlite3.Connection) -> None:
        with store.database.transaction(connection):
            connection.execute("INSERT INTO suppression_targets VALUES('source',?)", (key,))
            connection.execute("UPDATE store_metadata SET policy_epoch=policy_epoch+1")

    store.database.write(suppress)
    after = store.search_archive(query.model_copy(update={"limit": 1}))
    assert after.coverage.status == "complete"
    assert after.results == original.results[1:]  # Projected rank unchanged; exact record rejected.


def test_archive_rejects_delivery_when_recall_selection_changes_during_exact_read(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = source_fixture.batch.source_store_id
    engine.rebuild_index(library, SyntheticEncoder())
    query = ArchiveQuery(text="café", mode="literal", source_store_ids=(library,), limit=1)
    assert store.search_archive(query).results
    resolve = engine.resolve

    def exclude(reference: SourceRef) -> SourceRecord:
        record = resolve(reference)
        store.configure_recall(RecallSelection(import_mode="none"))
        return record

    monkeypatch.setattr(engine, "resolve", exclude)
    with pytest.raises(AccessDeniedError):
        store.search_archive(query)
    after = store.search_archive(query)
    assert after.coverage.status == "complete" and not after.results


def test_archive_retains_its_generation_through_late_hydration(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = source_fixture.batch.source_store_id
    encoder = SyntheticEncoder()
    engine.rebuild_index(library, encoder)
    index = engine.indexes[library]
    generation, path = index.active()
    query = ArchiveQuery(text="café", mode="literal", source_store_ids=(library,), limit=1)
    original = store.search_archive(query)
    assert original.results and original.coverage.status == "complete"
    resolve = engine.resolve

    def publish_during_hydration(reference: SourceRef) -> SourceRecord:
        engine.rebuild_index(library, encoder)
        engine.rebuild_index(library, encoder)
        assert index.active()[0] != generation
        assert path.is_file()
        return resolve(reference)

    monkeypatch.setattr(engine, "resolve", publish_during_hydration)
    assert store.search_archive(query) == original
    index.prune()
    assert not path.exists()


@pytest.mark.parametrize("indexed", [True, False])
def test_archive_does_not_deliver_after_exact_read_crosses_deadline(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
    indexed: bool,
) -> None:
    library = source_fixture.batch.source_store_id
    if indexed:
        engine.rebuild_index(library, SyntheticEncoder())
    query = ArchiveQuery(text="café", mode="literal", source_store_ids=(library,), limit=1)
    original = store.search_archive(query)
    assert original.results
    assert (original.coverage.index_status == "available") == indexed
    resolve = engine.resolve

    def delayed(reference: SourceRef) -> SourceRecord:
        record = resolve(reference)
        clock.advance(2)
        return record

    with monkeypatch.context() as late:
        late.setattr(engine, "resolve", delayed)
        options = RequestOptions(
            deadline_at=timestamp_from_datetime(clock.now() + timedelta(seconds=1))
        )
        with pytest.raises(DeadlineExceededError):
            store.search_archive(query.model_copy(update={"options": options}))
    assert store.search_archive(query) == original


def test_archive_releases_native_lease_when_submission_rejected(
    engine: CanonicalEngine, store: SqliteMemoryStore, source_fixture: SourceFixture
) -> None:
    library = source_fixture.batch.source_store_id
    encoder = SyntheticEncoder()
    engine.encoding.encoder = encoder
    engine.rebuild_index(library, encoder)
    index = engine.indexes[library]
    _, path = index.active()
    engine.deep_vectors.workers.stop_admission()
    with pytest.raises(EngineClosedError):
        store.search_archive(
            ArchiveQuery(text="café", mode="semantic", source_store_ids=(library,))
        )
    engine.rebuild_index(library, encoder)
    engine.rebuild_index(library, encoder)
    assert not path.exists()


def test_archive_linked_restore_keeps_approvals_evidence_and_grants(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    tmp_path: Path,
) -> None:
    library = source_fixture.batch.source_store_id
    service, _, selected = _approved(engine, source_fixture, draft)
    approved = service.knowledge(library, selected)
    original = engine.resolve(source_fixture.message.source)

    seed = SeedRequest(idempotency_key="restore-seed", library_id=library, selections=(selected,))
    receipt = service.seed(store.character_id, seed)
    memory_id = receipt.affected[0].memory_id
    before = store.get(memory_id)
    evidence = store.get_sources(memory_id)
    query = ArchiveQuery(text="café", source_store_ids=(library,))
    encoder = SyntheticEncoder()
    engine.encoding.encoder = encoder
    engine.rebuild_index(library, encoder)
    baseline = store.search_archive(query)
    assert baseline.coverage.status == "complete" and baseline.results
    root = engine.root
    engine.close()

    bundles = BundleService(root)
    source, character = tmp_path / "source-bundle", tmp_path / "character-bundle"
    bundles.export_source(library, source)
    bundles.export_character(store.character_id, character)
    restored_root = tmp_path / "restored"
    restore_bundles((source, character), restored_root)
    with CanonicalEngine(restored_root, encoder=encoder) as restored:
        reader = restored.character(store.character_id)
        assert LibraryService(restored).knowledge(library, selected) == approved
        assert reader.get(memory_id) == before
        assert reader.get_sources(memory_id) == evidence
        missing = reader.search_archive(query)
        assert "canonical_archive_scan" in missing.coverage.reason_codes
        assert [hit.record for hit in missing.results] == [original]
        restored.rebuild_index(library, encoder)
        after = reader.search_archive(query)
        assert after.results == baseline.results
        assert after.coverage.status == baseline.coverage.status
        assert after.coverage.reason_codes == baseline.coverage.reason_codes
        restored.revoke_source(store.character_id, library)
        assert not reader.search_archive(query).results
        assert reader.get(memory_id) == before
        with pytest.raises(AccessDeniedError):
            reader.get_sources(memory_id)


@pytest.mark.parametrize("damage", ["winner", "below_delivery_limit", "invalid_exact_span"])
def test_archive_corrupt_payload_falls_back_and_republishing_prior_generation_restores_parity(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    monkeypatch: pytest.MonkeyPatch,
    damage: Literal["winner", "below_delivery_limit", "invalid_exact_span"],
) -> None:
    library = source_fixture.message.source.source_store_id
    original = engine.resolve(source_fixture.message.source)
    receipt = store.remember(RememberRequest(idempotency_key="approved-input", draft=draft))
    memory_id = receipt.affected[0].memory_id
    canonical = store.get(memory_id)
    proof = store.get_sources(memory_id)
    encoder = SyntheticEncoder()
    engine.encoding.encoder = encoder
    index = engine.indexes[library]
    baseline_generation = engine.rebuild_index(library, encoder)
    query = ArchiveQuery(text="café", limit=1, source_store_ids=(library,))
    baseline = store.search_archive(query)
    full = store.search_archive(query.model_copy(update={"limit": 2}))
    assert baseline.coverage.status == "complete"
    assert len(full.results) == 2 and baseline.results == full.results[:1]
    payload = "invalid"
    if damage == "invalid_exact_span":
        damaged = next(hit.record for hit in full.results if isinstance(hit.record, ArchiveChunk))
        span = damaged.source_spans[0]
        wrong_hash = span.evidence.span.model_copy(update={"quoted_span_sha256": "0" * 64})
        evidence = span.evidence.model_copy(update={"span": wrong_hash})
        payload = damaged.model_copy(
            update={"source_spans": (span.model_copy(update={"evidence": evidence}),)}
        ).model_dump_json()
    else:
        damaged = full.results[0 if damage == "winner" else 1].record
    kind, target = (
        ("chunk", damaged.chunk_id)
        if isinstance(damaged, ArchiveChunk)
        else ("message", damaged.source.record_id)
    )

    encoder.fail = True
    with pytest.raises(EmbeddingError):
        engine.rebuild_index(library, encoder)
    assert index.active()[0] == baseline_generation
    assert store.search_archive(query) == baseline

    encoder.fail = False
    engine.rebuild_index(library, encoder)
    bad_generation, bad_path = index.active()
    with closing(connect(bad_path)) as connection:
        updated = connection.execute(
            "UPDATE documents SET payload_json=? WHERE kind=? AND target_id=?",
            (payload, kind, str(target)),
        )
        assert updated.rowcount == 1
    assert not index.certified(bad_generation, bad_path, cached_only=True)
    fallback = store.search_archive(query)
    assert "canonical_archive_scan" in fallback.coverage.reason_codes
    assert len(fallback.results) == 1 and fallback.results[0].record == original
    # Prove eager validation also falls back when the hash check cannot catch the damage.
    with monkeypatch.context() as cold:
        cold.setattr(indexed_retrieval, "_ARCHIVE_CERTIFY_BYTES", 0)
        assert store.search_archive(query) == fallback
    # A trusted manifest with a logically damaged projection exercises lazy validation.
    index.publish(bad_generation.model_copy(update={"file_sha256": file_sha256(bad_path)}))
    if damage == "below_delivery_limit" or damaged != full.results[0].record:
        display = store.search_archive(query)
        assert display.results == baseline.results
        assert display.coverage.status == "complete"
    assert store.search_archive(query.model_copy(update={"limit": 2})) == fallback
    assert store.get(memory_id) == canonical and store.get_sources(memory_id) == proof

    # Missing derived files must not prevent exact original archive/evidence access.
    bad_path.unlink()
    assert store.search_archive(query) == fallback
    index.publish(baseline_generation)
    assert store.search_archive(query) == baseline

    store.forget(
        ForgetRequest(
            idempotency_key="forget", memory_id=memory_id, expected_revision=1, reason="test"
        )
    )
    assert not store.search_archive(query).results
