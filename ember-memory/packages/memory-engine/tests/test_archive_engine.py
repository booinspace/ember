"""Archive/ownership/restart tests exercise actual temporary SQLite files."""

import sqlite3
from pathlib import Path

import pytest

from ember_memory_abi.archive import Conversation, NormalizedArchiveBatch
from ember_memory_abi.errors import RevisionConflictError, UnsupportedVersionError
from ember_memory_abi.evidence import SourceRef
from ember_memory_engine.engine import CanonicalEngine
from ember_memory_engine.errors import (
    CorruptStoreError,
    IdempotencyConflictError,
    StoreOwnershipError,
)
from ember_memory_engine.models import SourceKey

from .conftest import SourceFixture, TestClock


def test_atomic_import_crosswalk_cursor_and_graph_survive_restart(
    engine: CanonicalEngine, source_fixture: SourceFixture, clock: TestClock
) -> None:
    root = engine.root
    writer = engine.archive(source_fixture.batch.source_store_id)
    before = writer.get_record(source_fixture.message.source)
    assert writer.cursor(source_fixture.batch.resume_cursor) == source_fixture.batch.resume_cursor
    keys = writer.prepare_keys(
        (SourceKey(adapter="synthetic", record_kind="message", external_id="synthetic-message"),)
    )
    assert keys[0].record_id == source_fixture.message.source.record_id
    assert before.created_time.value is None
    engine.close()
    with CanonicalEngine(root, clock=clock) as reopened:
        assert (
            reopened.archive(source_fixture.batch.source_store_id).get_record(
                source_fixture.message.source
            )
            == before
        )
        assert len(reopened.stores()) == 2
        graph_ref = source_fixture.batch.records[1].source
        node = reopened.archive(graph_ref.source_store_id).get_record(graph_ref)
        assert node.children[0].status == "missing"
        assert node.message == source_fixture.message.source


def test_same_archive_key_replays_and_changed_key_content_fails(
    engine: CanonicalEngine, source_fixture: SourceFixture
) -> None:
    writer = engine.archive(source_fixture.batch.source_store_id)
    # The initial request included crosswalks; a different key safely rechecks immutable records.
    batch = NormalizedArchiveBatch.model_validate(
        {
            **source_fixture.batch.model_dump(),
            "records": source_fixture.batch.records,
            "idempotency_key": "second-batch",
        }
    )
    assert writer.write_batch(batch).records_written == 0
    assert writer.write_batch(batch).outcome == "already_applied"
    changed = NormalizedArchiveBatch.model_validate(
        {
            **batch.model_dump(),
            "records": batch.records,
            "resume_cursor": {**batch.resume_cursor.model_dump(), "next_record_index": 4},
        }
    )
    with pytest.raises(IdempotencyConflictError):
        writer.write_batch(changed)


def test_failed_revision_rolls_back_new_keys_records_and_cursor(
    engine: CanonicalEngine, source_fixture: SourceFixture
) -> None:
    writer = engine.archive(source_fixture.batch.source_store_id)
    keys = writer.prepare_keys(
        (
            SourceKey(
                adapter="synthetic", record_kind="conversation", external_id="new-before-failure"
            ),
        )
    )
    record = Conversation(
        source=SourceRef(
            source_store_id=writer.source_store_id,
            record_kind="conversation",
            record_id=keys[0].record_id,
            record_revision=2,
        ),
        recorded_at=source_fixture.message.recorded_at,
    )
    batch = NormalizedArchiveBatch.model_validate(
        {
            **source_fixture.batch.model_dump(),
            "records": (record,),
            "idempotency_key": "fails",
            "resume_cursor": {
                **source_fixture.batch.resume_cursor.model_dump(),
                "next_record_index": 4,
            },
        }
    )
    with pytest.raises(RevisionConflictError):
        writer.write_batch(batch, keys=keys)
    fresh = writer.prepare_keys((keys[0].key,))
    assert fresh[0].record_id != keys[0].record_id
    assert writer.cursor(source_fixture.batch.resume_cursor).next_record_index == 3


def test_existing_source_revision_is_immutable(
    engine: CanonicalEngine, source_fixture: SourceFixture
) -> None:
    changed = Conversation.model_validate(
        {**source_fixture.batch.records[0].model_dump(), "title": "altered"}
    )
    batch = NormalizedArchiveBatch.model_validate(
        {
            **source_fixture.batch.model_dump(),
            "records": (changed,),
            "idempotency_key": "cannot-overwrite",
        }
    )
    with pytest.raises(RevisionConflictError):
        engine.archive(batch.source_store_id).write_batch(batch)


def test_duplicate_owner_is_denied_and_clean_close_releases_lock(
    engine: CanonicalEngine, clock: TestClock
) -> None:
    with pytest.raises(StoreOwnershipError):
        CanonicalEngine(engine.root, clock=clock)
    root = engine.root
    engine.close()
    with CanonicalEngine(root, clock=clock):
        pass


def test_unknown_schema_is_not_adopted_or_wiped(tmp_path: Path) -> None:
    root = tmp_path / "unknown"
    with CanonicalEngine(root):
        pass
    with sqlite3.connect(root / "registry.db") as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(UnsupportedVersionError):
        CanonicalEngine(root)
    with sqlite3.connect(root / "registry.db") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_unversioned_foreign_database_is_not_reinitialized(tmp_path: Path) -> None:
    root = tmp_path / "foreign"
    root.mkdir()
    with sqlite3.connect(root / "registry.db") as connection:
        connection.execute("CREATE TABLE keep_me(value TEXT)")
        connection.execute("INSERT INTO keep_me VALUES('unchanged')")
    with pytest.raises(CorruptStoreError):
        CanonicalEngine(root)
    with sqlite3.connect(root / "registry.db") as connection:
        assert connection.execute("SELECT value FROM keep_me").fetchone()[0] == "unchanged"


def test_store_namespace_does_not_merge_accounts(engine: CanonicalEngine) -> None:
    first = engine.create_library("separate-a")
    second = engine.create_library("separate-b")
    assert first.store_id != second.store_id
    with pytest.raises(RevisionConflictError):
        engine.create_library("separate-a")
