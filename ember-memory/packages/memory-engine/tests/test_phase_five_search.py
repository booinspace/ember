"""Scope, recovery, evidence and native deadline acceptance for disposable retrieval."""

import re
import struct
from collections.abc import Sequence
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest

from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ArchiveSearchResponse,
    MemoryQuery,
    RememberRequest,
    RequestOptions,
    UpdateRequest,
)
from ember_memory_abi.memory import ArchiveChunk, MemoryDraft
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.chunking import Unit, group_chunks
from ember_memory_engine.engine import CanonicalEngine
from ember_memory_engine.errors import EmbeddingError, EngineBusyError
from ember_memory_engine.index_storage import connect
from ember_memory_engine.search_contracts import EmbeddingSpec

from .conftest import TEXT, SourceFixture


class SyntheticEncoder:
    """Controlled CPU boundary; equal vectors deliberately stress eligible-before-top-k."""

    def __init__(self) -> None:
        self.specification = EmbeddingSpec(
            model_revision="synthetic",
            tokenizer_revision="synthetic",
            model_sha256="a" * 64,
            tokenizer_sha256="b" * 64,
        )
        self.calls = 0
        self.started = Event()
        self.release = Event()
        self.block = False
        self.fail = False

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((match.start(), match.end()) for match in re.finditer(r"\S+", text))

    def token_count(self, text: str) -> int:
        return len(self.offsets(text)) + 2

    def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[bytes, ...]:
        self.calls += len(texts)
        if self.fail:
            raise EmbeddingError()
        if query and self.block:
            self.started.set()
            assert self.release.wait(5)
        return tuple(struct.pack("<384f", 1.0, *([0.0] * 383)) for _ in texts)


def _build(engine: CanonicalEngine, identity: object, encoder: SyntheticEncoder) -> None:
    engine.encoding.encoder = encoder
    from uuid import UUID

    assert isinstance(identity, UUID)
    engine.rebuild_index(identity, encoder)


def test_fts_hybrid_structured_and_exact_provenance(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    receipt = store.remember(RememberRequest(idempotency_key="new", draft=draft))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    _build(engine, source_fixture.evidence_ref.source_store_id, encoder)
    for mode in ("lexical", "hybrid", "semantic"):
        response = store.search(MemoryQuery(text="primary gpu", mode=mode))
        assert response.coverage.index_status == "available"
        assert response.results[0].memory.memory_id == receipt.affected[0].memory_id
    assert store.search(MemoryQuery(predicates=("hardware.primary_gpu",))).results
    assert not store.search(MemoryQuery(predicates=("unknown.predicate",))).results
    assert not store.search(MemoryQuery(entity_ids=(uuid4(),))).results
    archive = store.search_archive(ArchiveQuery(text="café", include_alternative_branches=True))
    chunks = [item.record for item in archive.results if isinstance(item.record, ArchiveChunk)]
    assert chunks
    for chunk in chunks:
        for span in chunk.source_spans:
            quote = engine.validate(span.evidence)
            assert quote == chunk.text[span.start_codepoint : span.end_codepoint]
    literal = store.search_archive(
        ArchiveQuery(
            text="café 🍰",
            mode="literal",
            include_alternative_branches=True,
        )
    )
    assert literal.results and all(
        not isinstance(item.record, ArchiveChunk) for item in literal.results
    )
    assert not store.search_archive(
        ArchiveQuery(text="café", roles=("assistant",), include_alternative_branches=True)
    ).results
    assert not store.search_archive(
        ArchiveQuery(text="café", conversation_ids=(uuid4(),), include_alternative_branches=True)
    ).results
    assert not store.search_archive(
        ArchiveQuery(text="café", include_alternative_branches=False)
    ).results  # unresolved
    assert store.get_sources(receipt.affected[0].memory_id)[0].quote == TEXT


@pytest.mark.parametrize("certified_headers", [True, False])
def test_scope_precedes_vector_top_k(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    certified_headers: bool,
) -> None:
    for index in range(50):
        store.remember(RememberRequest(idempotency_key=f"m-{index}", draft=draft))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    # Create 50 closer unrelated projections. Their vectors must be excluded by
    # canonical scope inside KNN, not filtered after consuming its 40 slots.
    _, path = engine.indexes[store.character_id].active()
    with connect(path) as connection:
        farther = struct.pack("<384f", 0.8, 0.6, *([0.0] * 382))
        connection.execute("UPDATE vec_documents SET embedding=?", (farther,))
        for _ in range(50):
            row = connection.execute(
                "INSERT INTO documents(kind,target_id,revision,text,source_store_id) "
                "VALUES('memory',?,1,'unrelated',?)",
                (str(uuid4()), str(store.character_id)),
            )
            vector = encoder.encode(("unrelated",))[0]
            inserted = connection.execute(
                "INSERT INTO embedding_records VALUES(NULL,?,'hash',3,1,'document','time')",
                (row.lastrowid,),
            )
            connection.execute(
                "INSERT INTO vec_documents(rowid,embedding,active,valid_from,"
                "valid_until,document_id) "
                "VALUES(?,?,0,0,?,?)",
                (inserted.lastrowid, vector, 2**63 - 1, row.lastrowid),
            )
    from ember_memory_engine.embedding import file_sha256

    generation, _ = engine.indexes[store.character_id].active()
    engine.indexes[store.character_id].publish(
        generation.model_copy(
            update={
                "file_sha256": file_sha256(path),
                "header_projection": generation.header_projection if certified_headers else None,
            }
        )
    )
    response = store.search(MemoryQuery(text="unrelated synonym", mode="semantic"))
    assert response.results, response.coverage
    assert response.coverage.status == "complete"


def test_generation_rebuild_preserves_canonical_and_failed_publish(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    identity = (
        store.remember(RememberRequest(idempotency_key="new", draft=draft)).affected[0].memory_id
    )
    before = store.get(identity).model_dump_json()
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    old, _ = engine.indexes[store.character_id].active()
    encoder.fail = True
    with pytest.raises(EmbeddingError):
        engine.rebuild_index(store.character_id, encoder)
    assert engine.indexes[store.character_id].active()[0] == old
    assert store.get(identity).model_dump_json() == before
    encoder.fail = False
    _build(engine, store.character_id, encoder)
    assert engine.indexes[store.character_id].active()[0].generation != old.generation
    assert store.get(identity).model_dump_json() == before


def test_missing_corrupt_vector_and_catalog_fallback_and_rebuild(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    identity = (
        store.remember(RememberRequest(idempotency_key="new", draft=draft)).affected[0].memory_id
    )
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    _, path = engine.indexes[store.character_id].active()
    with connect(path) as connection:
        connection.execute("DROP TABLE vec_documents")
    response = store.search(MemoryQuery(text="primary"))
    assert response.results and response.coverage.status == "partial"
    assert store.get_sources(identity)
    path.write_bytes(b"damaged disposable index")
    assert store.search(MemoryQuery(text="SYNTHETIC_A")).results
    catalog = engine.indexes[store.character_id].directory / "search.db"
    catalog.write_bytes(b"damaged disposable catalog")
    assert store.search(MemoryQuery(text="SYNTHETIC_A")).results
    _build(engine, store.character_id, encoder)
    assert store.search(MemoryQuery(text="primary")).results


def test_stale_index_never_returns_old_current_revision(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    identity = (
        store.remember(RememberRequest(idempotency_key="new", draft=draft)).affected[0].memory_id
    )
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    store.update(
        UpdateRequest(
            idempotency_key="update",
            memory_id=identity,
            expected_revision=1,
            draft=draft,
            reason="manual_confirmation",
        )
    )
    response = store.search(MemoryQuery(text="primary"))
    assert response.results and all(item.memory.revision == 2 for item in response.results)
    assert response.coverage.status == "partial"
    _build(engine, store.character_id, encoder)
    assert store.search(MemoryQuery(text="primary")).results[0].memory.revision == 2


def test_revocation_filters_existing_shared_vectors(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
) -> None:
    encoder = SyntheticEncoder()
    _build(engine, source_fixture.evidence_ref.source_store_id, encoder)
    query = ArchiveQuery(text="café", include_alternative_branches=True)
    assert store.search_archive(query).results
    engine.revoke_source(store.character_id, source_fixture.evidence_ref.source_store_id)
    assert not store.search_archive(query).results


def test_archive_precision_gates_weak_vectors_and_filler_without_rebuilding(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
) -> None:
    class ContrastEncoder(SyntheticEncoder):
        def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[bytes, ...]:
            return tuple(
                struct.pack(
                    "<384f",
                    *((0.0, 1.0) if query and text != "espresso" else (1.0, 0.0)),
                    *([0.0] * 382),
                )
                for text in texts
            )

    encoder = ContrastEncoder()
    library = source_fixture.evidence_ref.source_store_id
    _build(engine, library, encoder)
    generation, path = engine.indexes[library].active()
    options = RequestOptions(extensions={"ember.archive_ranking": "precision_v1"})

    def search(text: str, *, precision: bool = True) -> ArchiveSearchResponse:
        query = ArchiveQuery(
            text=text,
            source_store_ids=(library,),
            options=options if precision else RequestOptions(),
        )
        return store.search_archive(query)

    assert search("unrelated", precision=False).results
    irrelevant = search("unrelated")
    assert not irrelevant.results and irrelevant.coverage.status == "complete"
    assert not search("what is the").results
    assert search("café").results  # Weak vector, valid lexical evidence.
    paraphrase = search("espresso")
    assert paraphrase.results and paraphrase.coverage.status == "complete"
    assert all(
        "SEMANTIC_MATCH" in explanation.reasons
        for hit in paraphrase.results
        for explanation in hit.ranking
    )
    assert engine.indexes[library].active() == (generation, path)
    engine.revoke_source(store.character_id, library)
    assert not search("espresso").results


def test_native_encode_timeout_remains_owned_and_bounded(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    store.remember(RememberRequest(idempotency_key="new", draft=draft))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    encoder.block = True
    try:
        started = monotonic()
        response = store.search(MemoryQuery(text="primary"))
        assert monotonic() - started < 0.3
        assert response.results and response.coverage.status == "partial"
        assert encoder.started.is_set() and engine.encoding.workers.pending == 1
        engine.encoding.query("queued")
        with pytest.raises(EngineBusyError):
            engine.encoding.query("rejected")
        assert engine.encoding.workers.pending == 2
    finally:
        encoder.release.set()


def test_chunks_preserve_unicode_all_spans_and_isolate_alternatives() -> None:
    encoder = SyntheticEncoder()
    reference = SourceRef(
        source_store_id=uuid4(),
        record_kind="message",
        record_id=uuid4(),
        record_revision=1,
        part_id=uuid4(),
    )
    text = ("café 🍰 sentence. " * 450) + "tail"
    unit = Unit(reference, text, "user", "conversation", "selected", 0, None)
    other = Unit(
        reference.model_copy(update={"record_id": uuid4()}),
        "alternative secret",
        "assistant",
        "conversation",
        "alternative",
        1,
        None,
    )
    chunks = group_chunks((unit, other), encoder)
    covered: set[int] = set()
    for chunk in chunks:
        assert encoder.token_count(chunk.text) <= 512
        for span in chunk.source_spans:
            original = text if span.evidence.source == reference else other.text
            evidence = span.evidence.span
            assert evidence is not None
            assert (
                original[evidence.start_codepoint : evidence.end_codepoint]
                == chunk.text[span.start_codepoint : span.end_codepoint]
            )
            if span.evidence.source == reference:
                covered.update(range(evidence.start_codepoint, evidence.end_codepoint))
        assert not ("alternative secret" in chunk.text and "café" in chunk.text)
    assert all(index in covered for index, char in enumerate(text) if not char.isspace())


def test_update_reuses_vectors_and_entity_change_gets_transactional_outbox(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    from ember_memory_abi.memory import Entity

    store.remember(RememberRequest(idempotency_key="new", draft=draft))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    encoder.calls = 0
    engine.rebuild_index(store.character_id, encoder, reuse=True)
    assert encoder.calls == 0
    engine.put_entity(
        store.character_id,
        Entity(reference=source_fixture.subject, revision=2, name="Renamed synthetic subject"),
    )
    events = store.database.read(
        lambda c: c.execute(
            "SELECT kind FROM index_outbox ORDER BY sequence DESC LIMIT 1"
        ).fetchone()[0]
    )
    assert events == "entity_changed"
    engine.rebuild_index(store.character_id, encoder, reuse=True)
    assert encoder.calls == 1
    assert store.search(MemoryQuery(text="Renamed", mode="lexical")).results


def test_forgetting_suppresses_cached_semantic_and_archive_results(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    from ember_memory_abi.interfaces import ForgetRequest

    identity = (
        store.remember(RememberRequest(idempotency_key="new", draft=draft)).affected[0].memory_id
    )
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    _build(engine, source_fixture.evidence_ref.source_store_id, encoder)
    assert store.search(MemoryQuery(text="primary", mode="semantic")).results
    assert store.search_archive(ArchiveQuery(text="café")).results
    store.forget(
        ForgetRequest(
            idempotency_key="forget",
            memory_id=identity,
            expected_revision=1,
            reason="confirmed_forget",
        )
    )
    assert not store.search(MemoryQuery(text="primary", mode="semantic")).results
    assert not store.search_archive(ArchiveQuery(text="café")).results


def test_deep_search_has_independent_admission_and_event_loop_heartbeat(
    store: SqliteMemoryStore,
) -> None:
    import asyncio

    from ember_memory_engine.async_client import InProcessAsyncMemoryClient

    started, release = Event(), Event()

    def blocking(connection: object) -> None:
        started.set()
        assert release.wait(5)

    future = store.database.submit(blocking, deep=True)
    assert started.wait(2)
    client = InProcessAsyncMemoryClient(store)

    async def scenario() -> None:
        try:
            response = await asyncio.wait_for(client.search(MemoryQuery()), timeout=1)
            assert response.results == ()
            assert store.database.deep_readers.pending == 1
            for _ in range(10):
                await asyncio.sleep(0)
        finally:
            release.set()
            await client.aclose()

    asyncio.run(scenario())
    future.result(timeout=2)


@pytest.mark.integration
def test_actual_cpu_bge_semantic_recall_without_lexical_overlap(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    import os
    from pathlib import Path

    from ember_memory_engine.embedding import BgeEncoder

    directory = Path(os.environ.get("EMBER_EMBEDDING_MODEL", ".local/models/bge-small-en-v1.5"))
    if not (directory / "embedding.json").is_file():
        pytest.skip(
            "Install pinned assets with scripts/setup_embeddings.py for CPU BGE acceptance."
        )
    encoder = BgeEncoder(directory)
    engine.encoding.encoder = encoder
    identity = (
        store.remember(RememberRequest(idempotency_key="new", draft=draft)).affected[0].memory_id
    )
    engine.rebuild_index(store.character_id, encoder)
    response = store.search(MemoryQuery(text="graphics processing device", mode="semantic"))
    assert response.coverage.status == "complete"
    assert response.results[0].memory.memory_id == identity
    assert any("SEMANTIC_MATCH" in rank.reasons for rank in response.results[0].ranking)
    assert not store.search(MemoryQuery(text="graphics processing device", mode="lexical")).results
    with pytest.raises(EmbeddingError):
        encoder.encode(("oversized " * 600,))


def test_recent_native_context_never_includes_imported_library_sources(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
) -> None:
    from ember_memory_abi.interfaces import ContextRequest
    from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
    from ember_memory_engine.models import MutationAuthority

    author = MemoryAuthoringService(
        engine,
        ensure_subject(engine, store.character_id),
        authority=MutationAuthority(mode="confirmed_user"),
    )
    author.create(
        NewMemoryInput(idempotency_key="native", text="A native observation about a garden.")
    )
    response = store.retrieve_context(
        ContextRequest(
            memory_query=MemoryQuery(), memory_token_budget=4096, archive_token_budget=8192
        )
    )
    assert response.archive
    assert all(
        item.record.source.source_store_id == store.character_id for item in response.archive
    )
    assert any("TEMPORAL_MATCH" in rank.reasons for rank in response.archive[0].ranking)
    assert (
        store.retrieve_context(
            ContextRequest(
                memory_query=MemoryQuery(), memory_token_budget=4096, archive_token_budget=0
            )
        ).archive
        == ()
    )


def test_semantic_profile_mismatch_degrades_then_replacement_is_atomic(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    store.remember(RememberRequest(idempotency_key="new", draft=draft))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    original, _ = engine.indexes[store.character_id].active()
    encoder.specification = encoder.specification.model_copy(
        update={"query_prefix": "Changed query: "}
    )
    response = store.search(MemoryQuery(text="primary"))
    assert response.results and response.coverage.status == "partial"
    assert "semantic_encoder_missing_or_profile_mismatch" in response.coverage.reason_codes
    engine.rebuild_index(store.character_id, encoder)
    replacement, _ = engine.indexes[store.character_id].active()
    assert replacement.generation != original.generation
    assert store.search(MemoryQuery(text="primary")).coverage.status == "complete"


def test_incremental_outbox_refresh_reuses_old_vectors_and_updates_revision(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    identity = (
        store.remember(RememberRequest(idempotency_key="first", draft=draft)).affected[0].memory_id
    )
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    previous, _ = engine.indexes[store.character_id].active()
    encoder.calls = 0
    store.update(
        UpdateRequest(
            idempotency_key="update",
            memory_id=identity,
            expected_revision=1,
            draft=draft,
            reason="manual_confirmation",
        )
    )
    refreshed = engine.update_index(store.character_id, encoder)
    assert (
        refreshed.generation != previous.generation
        and refreshed.outbox_sequence > previous.outbox_sequence
    )
    assert encoder.calls == 0
    response = store.search(MemoryQuery(text="primary", mode="semantic"))
    assert response.coverage.status == "complete" and response.results[0].memory.revision == 2
    assert engine.update_index(store.character_id, encoder) == refreshed


def test_incremental_native_source_and_memory_creation(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
) -> None:
    from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
    from ember_memory_engine.models import MutationAuthority

    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    author = MemoryAuthoringService(
        engine,
        ensure_subject(engine, store.character_id),
        authority=MutationAuthority(mode="confirmed_user"),
    )
    receipt = author.create(
        NewMemoryInput(idempotency_key="native", text="The native animal is an otter.")
    )
    engine.update_index(store.character_id, encoder)
    response = store.search(MemoryQuery(text="otter", mode="lexical"))
    assert response.results[0].memory.memory_id == receipt.affected[0].memory_id
    archive = store.search_archive(ArchiveQuery(text="otter"))
    assert any(isinstance(item.record, ArchiveChunk) for item in archive.results)


def test_runtime_refresh_is_owned_coalesced_and_never_bootstraps_missing_indexes(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    from time import sleep

    encoder = SyntheticEncoder()
    engine.encoding.encoder = encoder
    engine.auto_refresh = True
    store.remember(RememberRequest(idempotency_key="first", draft=draft))
    assert engine.index_jobs_pending == 0
    _build(engine, store.character_id, encoder)
    store.remember(RememberRequest(idempotency_key="second", draft=draft))
    deadline = monotonic() + 5
    while engine.index_jobs_pending and monotonic() < deadline:
        sleep(0.01)
    assert engine.index_jobs_pending == 0 and engine.index_error_count == 0
    assert store.search(MemoryQuery(text="primary")).coverage.status == "complete"


def test_expired_world_time_is_filtered_before_cached_vector_top_k(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    from datetime import timedelta

    from ember_memory_abi.common import timestamp_from_datetime

    from .conftest import TestClock

    assert isinstance(engine.clock, TestClock)
    until = timestamp_from_datetime(engine.clock.now() + timedelta(seconds=2))
    timed = draft.model_copy(
        update={
            "temporal": draft.temporal.model_copy(
                update={"valid_until": until, "basis": "explicit_interval"}
            )
        }
    )
    store.remember(RememberRequest(idempotency_key="timed", draft=timed))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    assert store.search(MemoryQuery(text="primary", mode="semantic")).results
    engine.clock.advance(3)
    for mode in ("lexical", "hybrid", "semantic"):
        assert not store.search(MemoryQuery(text="primary", mode=mode)).results


def test_staged_update_failure_preserves_pointer_rows_and_replay(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    from ember_memory_abi.memory import FactPayload, StringValue

    identity = (
        store.remember(RememberRequest(idempotency_key="initial", draft=draft))
        .affected[0]
        .memory_id
    )
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    previous, path = engine.indexes[store.character_id].active()
    original = path.read_bytes()
    assert isinstance(draft.content.payload, FactPayload)
    changed = draft.model_copy(
        update={
            "content": draft.content.model_copy(
                update={
                    "payload": draft.content.payload.model_copy(
                        update={"value": StringValue(value="SYNTHETIC_B")}
                    )
                }
            )
        }
    )
    store.update(
        UpdateRequest(
            idempotency_key="changed",
            memory_id=identity,
            expected_revision=1,
            draft=changed,
            reason="manual_confirmation",
        )
    )
    encoder.fail = True
    with pytest.raises(EmbeddingError):
        engine.update_index(store.character_id, encoder)
    assert engine.indexes[store.character_id].active()[0] == previous
    assert path.read_bytes() == original
    encoder.fail = False
    engine.update_index(store.character_id, encoder)
    assert (
        store.search(MemoryQuery(text="SYNTHETIC_B", mode="lexical")).results[0].memory.revision
        == 2
    )


def test_generation_retention_preserves_native_leases(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    store.remember(RememberRequest(idempotency_key="first", draft=draft))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    index = engine.indexes[store.character_id]
    _, old = index.active()
    release = index.lease(old)
    try:
        _build(engine, store.character_id, encoder)
        _build(engine, store.character_id, encoder)
        assert old.exists()
    finally:
        release()
    index.prune()
    assert not old.exists()
    assert len(tuple((index.directory / "generations").glob("*.db"))) == 2


def test_optional_vector_extension_loss_preserves_fts(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ember_memory_engine.index_storage as storage

    store.remember(RememberRequest(idempotency_key="first", draft=draft))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    engine.encoding.encoder = None
    original = storage.importlib.import_module

    def missing(name: str) -> object:
        if name == "sqlite_vec":
            raise ImportError()
        return original(name)

    monkeypatch.setattr(storage.importlib, "import_module", missing)
    response = store.search(MemoryQuery(text="primary"))
    assert response.results and response.coverage.index_status == "available"
    assert response.coverage.status == "partial"


def test_context_retains_missing_archive_index_coverage(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    from ember_memory_abi.interfaces import ContextRequest

    store.remember(RememberRequest(idempotency_key="context", draft=draft))
    _build(engine, store.character_id, SyntheticEncoder())
    assert store.search(MemoryQuery(text="primary", mode="lexical")).coverage.status == "complete"
    context = store.retrieve_context(
        ContextRequest(
            memory_query=MemoryQuery(text="primary", mode="lexical"),
            archive_query=ArchiveQuery(
                text="café", mode="lexical", include_alternative_branches=True
            ),
            memory_token_budget=10000,
            archive_token_budget=10000,
        )
    )
    assert context.memories
    assert context.coverage.status == "partial"
    assert "source_index_unavailable" in context.coverage.reason_codes


def test_native_sql_setup_failure_releases_lease_and_can_retry(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
) -> None:
    from concurrent.futures import Future

    from ember_memory_engine.errors import IndexError
    from ember_memory_engine.vector_search import VectorRequest

    store.remember(RememberRequest(idempotency_key="native", draft=draft))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    _, path = engine.indexes[store.character_id].active()
    vector: Future[bytes] = Future()
    vector.set_result(encoder.encode(("query",))[0])
    released = Event()
    failed = engine.fast_vectors.submit(
        VectorRequest(
            generation=path,
            canonical=engine.root / "missing.db",
            statement="SELECT document_id FROM vec_documents WHERE embedding MATCH ? AND k=1",
            parameters=(),
            encoding=vector,
            release=released.set,
        )
    )
    with pytest.raises(IndexError):
        failed.result(timeout=2)
    assert released.wait(1)
    retried = engine.fast_vectors.submit(
        VectorRequest(
            generation=path,
            canonical=store.database.path,
            statement="SELECT document_id FROM vec_documents WHERE embedding MATCH ? AND k=1",
            parameters=(),
            encoding=vector,
            release=lambda: None,
        )
    )
    assert retried.result(timeout=2)
    assert engine.fast_vectors.error_count == 1


def test_injected_query_encoder_rejects_invalid_vectors(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.remember(RememberRequest(idempotency_key="bad-query", draft=draft))
    encoder = SyntheticEncoder()
    _build(engine, store.character_id, encoder)
    monkeypatch.setattr(encoder, "encode", lambda *args, **kwargs: (b"invalid",))
    response = store.search(MemoryQuery(text="primary", mode="hybrid"))
    assert response.results and response.coverage.status == "partial"
    assert engine.encoding.error_count == 1
