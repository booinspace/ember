"""Phase 4 behavior: independent authority, exact portable closure and atomic recovery."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from ember_memory_abi.common import CanonicalId, timestamp_from_datetime
from ember_memory_abi.errors import AccessDeniedError, SourceUnavailableError, UnsupportedWriteError
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ForgetRequest,
    MemoryQuery,
    RememberRequest,
    UpdateRequest,
)
from ember_memory_abi.memory import (
    CandidateMemory,
    Entity,
    EntityRef,
    FactPayload,
    MemoryDraft,
    StringValue,
)
from ember_memory_engine import CanonicalEngine, MutationAuthority, SourceGrant
from ember_memory_engine.bundles import BundleService, restore_bundles
from ember_memory_engine.database import load_model
from ember_memory_engine.errors import BundleError, IdempotencyConflictError, StoreOwnershipError
from ember_memory_engine.library import (
    ApprovalRequest,
    KnowledgeSelection,
    LibraryService,
    SeedRequest,
)

from .conftest import TEXT, SourceFixture, TestClock

AUTHORITY = MutationAuthority(mode="confirmed_user")


def _approved(
    engine: CanonicalEngine, fixture: SourceFixture, draft: MemoryDraft
) -> tuple[LibraryService, ApprovalRequest, KnowledgeSelection]:
    service = LibraryService(engine, authority=AUTHORITY)
    library = fixture.message.source.source_store_id
    subject = EntityRef(store_id=library, entity_id=uuid4())
    service.put_entity(
        library, Entity(reference=subject, revision=1, name="Synthetic library user")
    )
    payload = FactPayload.model_validate({**draft.content.payload.model_dump(), "subject": subject})
    copied = MemoryDraft.model_validate(
        {**draft.model_dump(), "content": {**draft.content.model_dump(), "payload": payload}}
    )
    candidate = CandidateMemory(
        candidate_id=uuid4(),
        owner_store_id=library,
        revision=1,
        draft=copied,
        recorded_at=timestamp_from_datetime(engine.clock.now()),
    )
    request = ApprovalRequest(idempotency_key="approval", candidate=candidate)
    knowledge = service.approve(library, request)
    return service, request, KnowledgeSelection(knowledge_id=knowledge.knowledge_id, revision=1)


def _seed(
    engine: CanonicalEngine, fixture: SourceFixture, draft: MemoryDraft
) -> tuple[CanonicalId, CanonicalId, CanonicalId]:
    service, _, selection = _approved(engine, fixture, draft)
    first, second = engine.create_character().store_id, engine.create_character().store_id
    request = SeedRequest(
        idempotency_key="seed",
        library_id=fixture.message.source.source_store_id,
        selections=(selection,),
    )
    one = service.seed(first, request).affected[0].memory_id
    two = service.seed(second, request).affected[0].memory_id
    assert one != two
    return first, second, one


def test_approval_requires_authority_and_exact_library_evidence(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft
) -> None:
    service, request, selection = _approved(engine, source_fixture, draft)
    library = request.candidate.owner_store_id
    approved = service.knowledge(library, selection)
    assert approved.approval.manual_override
    assert service.approve(library, request) == approved
    with pytest.raises(AccessDeniedError):
        LibraryService(engine).approve(library, request)
    changed = request.model_copy(
        update={"candidate": request.candidate.model_copy(update={"revision": 2})}
    )
    with pytest.raises(IdempotencyConflictError):
        service.approve(library, changed)
    assert not engine.character(source_fixture.character_id).search(MemoryQuery()).results


def test_seeds_are_independent_and_replay_preserves_ids(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft
) -> None:
    service, request, selection = _approved(engine, source_fixture, draft)
    first, second = engine.create_character().store_id, engine.create_character().store_id
    seed = SeedRequest(
        idempotency_key="seed",
        library_id=request.candidate.owner_store_id,
        selections=(selection,),
    )
    receipt = service.seed(first, seed)
    assert service.seed(first, seed).operation_id == receipt.operation_id
    assert service.seed(first, seed).outcome == "already_applied"
    other = service.seed(second, seed)
    reader = engine.character(first)
    memory = reader.get(receipt.affected[0].memory_id)
    assert memory.origin.origin_memory_id == selection.knowledge_id
    assert memory.origin.seed_operation_id == receipt.operation_id
    assert memory.content.payload.subject.store_id == first
    assert (
        engine.character(second).get(other.affected[0].memory_id).content.payload.subject
        != memory.content.payload.subject
    )
    assert reader.get_sources(memory.memory_id)[0].quote == TEXT
    assert reader.search_archive(ArchiveQuery()).results == ()
    # Evolving one character neither rewrites the approved snapshot nor the other.
    revised = MemoryDraft.model_validate(
        {
            "content": {
                **memory.content.model_dump(),
                "payload": {
                    **memory.content.payload.model_dump(),
                    "value": StringValue(value="LOCAL_ONLY"),
                },
            }
        }
    )
    writer = engine.character(first, authority=AUTHORITY)
    writer.update(
        UpdateRequest(
            idempotency_key="edit",
            memory_id=memory.memory_id,
            expected_revision=1,
            draft=revised,
            reason="synthetic edit",
        )
    )
    assert (
        engine.character(second).get(other.affected[0].memory_id).content.payload.value.value
        == "SYNTHETIC_A"
    )
    assert (
        service.knowledge(seed.library_id, selection).draft.content.payload.value.value
        == "SYNTHETIC_A"
    )


def test_forgetting_blocks_reseed_and_does_not_mutate_other_character(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft
) -> None:
    service, request, selection = _approved(engine, source_fixture, draft)
    first, second = engine.create_character().store_id, engine.create_character().store_id
    seed = SeedRequest(
        idempotency_key="seed", library_id=request.candidate.owner_store_id, selections=(selection,)
    )
    memory = service.seed(first, seed).affected[0].memory_id
    other = service.seed(second, seed).affected[0].memory_id
    engine.character(first, authority=AUTHORITY).forget(
        ForgetRequest(
            idempotency_key="forget",
            memory_id=memory,
            expected_revision=1,
            reason="synthetic forget",
        )
    )
    with pytest.raises(AccessDeniedError):
        service.seed(first, seed.model_copy(update={"idempotency_key": "reseed"}))
    assert engine.character(second).get_sources(other)[0].quote == TEXT
    info, database = engine.maintenance_store(first)
    assert (
        database.read(
            lambda connection: connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        )
        == 1
    )
    assert (
        database.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM maintenance_receipts"
            ).fetchone()[0]
        )
        == 1
    )


def test_entity_context_is_frozen_at_approval(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft
) -> None:
    service, request, selection = _approved(engine, source_fixture, draft)
    origin = request.candidate.draft.content.payload.subject
    service.put_entity(origin.store_id, Entity(reference=origin, revision=2, name="LATER_CONTEXT"))
    character = engine.create_character().store_id
    memory = (
        service.seed(
            character,
            SeedRequest(
                idempotency_key="seed", library_id=origin.store_id, selections=(selection,)
            ),
        )
        .affected[0]
        .memory_id
    )
    entity = engine.character(character).get(memory).content.payload.subject
    _, database = engine.maintenance_store(character)
    context = database.read(
        lambda connection: load_model(
            connection.execute(
                "SELECT * FROM entities WHERE entity_id=?", (str(entity.entity_id),)
            ).fetchone(),
            Entity,
        )
    )
    assert context.name == "Synthetic library user"
    assert context.origin == origin


def test_grant_expiry_denies_quotes_and_archive_but_retains_beliefs(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft, clock: TestClock
) -> None:
    character = source_fixture.character_id
    memory = (
        engine.character(character, authority=AUTHORITY)
        .remember(RememberRequest(idempotency_key="memory", draft=draft))
        .affected[0]
        .memory_id
    )
    from datetime import timedelta

    expiry = timestamp_from_datetime(clock.now() + timedelta(seconds=2))
    engine.grant_source(
        character,
        SourceGrant(
            references=(source_fixture.message.source,),
            capabilities=frozenset({"evidence_read", "archive_search"}),
            expires_at=expiry,
        ),
    )
    assert engine.character(character).get_sources(memory)[0].quote == TEXT
    clock.advance(2)
    assert engine.character(character).get(memory).memory_id == memory
    with pytest.raises(AccessDeniedError):
        engine.character(character).get_sources(memory)
    assert engine.character(character).search_archive(ArchiveQuery()).results == ()
    with pytest.raises(AccessDeniedError):
        engine.grant_source(
            character,
            SourceGrant(
                references=(source_fixture.message.source,),
                capabilities=frozenset({"evidence_read"}),
                expires_at=expiry,
            ),
        )


@pytest.mark.integration
def test_linked_backup_requires_library_and_restores_two_characters(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft, tmp_path: Path
) -> None:
    first, second, memory = _seed(engine, source_fixture, draft)
    root = engine.root
    library = source_fixture.message.source.source_store_id
    raw = root / "libraries" / str(library) / "rawsource"
    raw.mkdir()
    (raw / "synthetic.zip").write_bytes(b"VERBATIM_SYNTHETIC_RAW")
    engine.close()
    bundle = BundleService(root)
    source = tmp_path / "source"
    one, two = tmp_path / "one", tmp_path / "two"
    bundle.export_source(library, source)
    manifest = bundle.export_character(first, one)
    bundle.export_character(second, two)
    assert manifest.dependencies == (library,)
    with pytest.raises(BundleError):
        restore_bundles((one,), tmp_path / "missing")
    assert not (tmp_path / "missing").exists()
    restored = tmp_path / "restored"
    restore_bundles((source, one, two), restored)
    with CanonicalEngine(restored) as reopened:
        assert reopened.character(first).get_sources(memory)[0].quote == TEXT
        assert reopened.character(second).search(MemoryQuery()).results
    assert (
        restored / "libraries" / str(library) / "rawsource" / "synthetic.zip"
    ).read_bytes() == b"VERBATIM_SYNTHETIC_RAW"
    assert json.loads((restored / "restore.json").read_text())["providers_disabled"]


@pytest.mark.integration
def test_selective_materialization_preserves_exact_spans_excludes_other_history(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft, tmp_path: Path
) -> None:
    first, _, memory = _seed(engine, source_fixture, draft)
    # A second belief must stay outside the selected character projection.
    from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject

    subject = ensure_subject(engine, first)
    MemoryAuthoringService(engine, subject, authority=AUTHORITY).create(
        NewMemoryInput(idempotency_key="private", text="UNRELATED_NATIVE_SECRET")
    )
    root = engine.root
    engine.close()
    output = tmp_path / "portable"
    manifest = BundleService(root).export_character(
        first, output, mode="self_contained", memory_ids=(memory,)
    )
    assert not manifest.dependencies
    assert manifest.selected_memory_ids == (memory,)
    assert any(item.coverage == "evidence_only" for item in manifest.stores)
    assert all("rawsource" not in item.path for item in manifest.files)
    assert all(
        b"UNRELATED_NATIVE_SECRET" not in (output / member.path).read_bytes()
        for member in manifest.files
    )
    restored = tmp_path / "restored"
    restore_bundles((output,), restored)
    with CanonicalEngine(restored) as reopened:
        assert reopened.character(first).get_sources(memory)[0].quote == TEXT
        assert reopened.character(first).get(memory).memory_id == memory
        assert len(reopened.character(first).search(MemoryQuery()).results) == 1
        assert reopened.character(first).search_archive(ArchiveQuery()).results == ()
        with pytest.raises(SourceUnavailableError):
            reopened.resolve(source_fixture.message.source)
        with pytest.raises(UnsupportedWriteError):
            reopened.archive(source_fixture.message.source.source_store_id).write_batch(
                source_fixture.batch
            )


@pytest.mark.integration
def test_native_selective_restore_can_author_new_memories(
    engine: CanonicalEngine, source_fixture: SourceFixture, tmp_path: Path
) -> None:
    from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject

    character = source_fixture.character_id
    subject = ensure_subject(engine, character)
    original = MemoryAuthoringService(engine, subject, authority=AUTHORITY).create(
        NewMemoryInput(idempotency_key="native", text="Native café 🍰 evidence")
    )
    memory = original.affected[0].memory_id
    root = engine.root
    engine.close()
    output = tmp_path / "native"
    BundleService(root).export_character(
        character, output, mode="self_contained", memory_ids=(memory,)
    )
    restored = tmp_path / "restored"
    restore_bundles((output,), restored)
    with CanonicalEngine(restored) as reopened:
        assert (
            reopened.character(character).get_sources(memory)[0].quote == "Native café 🍰 evidence"
        )
        assert ensure_subject(reopened, character) == subject
        author = MemoryAuthoringService(reopened, subject, authority=AUTHORITY)
        author.create(NewMemoryInput(idempotency_key="new", text="New explicit memory"))
        assert len(reopened.character(character).search(MemoryQuery()).results) == 2


@pytest.mark.integration
@pytest.mark.parametrize("tamper", ["hash", "extra", "traversal", "version"])
def test_invalid_bundle_publishes_no_restore(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    tmp_path: Path,
    tamper: str,
) -> None:
    first, _, _ = _seed(engine, source_fixture, draft)
    root = engine.root
    engine.close()
    output = tmp_path / "portable"
    BundleService(root).export_character(first, output, mode="self_contained")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if tamper == "hash":
        with (output / manifest["files"][0]["path"]).open("ab") as stream:
            stream.write(b"bad")
    elif tamper == "extra":
        (output / "unlisted").write_text("bad")
    elif tamper == "traversal":
        manifest["files"][0]["path"] = "../outside"
    else:
        manifest["bundle_version"] = "999.0.0"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(BundleError):
        restore_bundles((output,), tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_bundle_maintenance_cannot_compete_with_live_owner(
    engine: CanonicalEngine, source_fixture: SourceFixture, tmp_path: Path
) -> None:
    with pytest.raises(StoreOwnershipError):
        BundleService(engine.root).export_source(
            source_fixture.message.source.source_store_id, tmp_path / "busy"
        )
    assert not (tmp_path / "busy").exists()


@pytest.mark.integration
def test_bundle_never_overwrites_existing_output_or_root(
    engine: CanonicalEngine, source_fixture: SourceFixture, tmp_path: Path
) -> None:
    root = engine.root
    library = source_fixture.message.source.source_store_id
    engine.close()
    output = tmp_path / "bundle"
    BundleService(root).export_source(library, output)
    with pytest.raises(BundleError):
        BundleService(root).export_source(library, output)
    with pytest.raises(BundleError):
        restore_bundles((output,), root)
    with pytest.raises(BundleError):
        BundleService(root).export_source(library, root / "nested-bundle")


@pytest.mark.integration
def test_exact_revocation_preserves_seeds_and_other_capability(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft
) -> None:
    character = source_fixture.character_id
    memory = (
        engine.character(character, authority=AUTHORITY)
        .remember(RememberRequest(idempotency_key="m", draft=draft))
        .affected[0]
        .memory_id
    )
    engine.revoke_grant(
        character,
        SourceGrant(
            references=(source_fixture.message.source,), capabilities=frozenset({"archive_search"})
        ),
    )
    assert engine.character(character).search_archive(ArchiveQuery()).results == ()
    assert engine.character(character).get_sources(memory)[0].quote == TEXT
    engine.revoke_grant(
        character,
        SourceGrant(
            references=(source_fixture.message.source,), capabilities=frozenset({"evidence_read"})
        ),
    )
    assert engine.character(character).get(memory).memory_id == memory
    with pytest.raises(AccessDeniedError):
        engine.character(character).get_sources(memory)


def _legacy(root: Path) -> None:
    from ember_memory_engine.schema import PHASE_FOUR_IMMUTABLE

    for path in (
        root / "registry.db",
        *root.glob("libraries/*/memory.db"),
        *root.glob("characters/*/memory.db"),
    ):
        from contextlib import closing

        with closing(sqlite3.connect(path)) as connection:
            if path.name != "registry.db":
                for table in PHASE_FOUR_IMMUTABLE:
                    for action in ("update", "delete"):
                        connection.execute(f"DROP TRIGGER immutable_{table}_{action}")
                for table in (*PHASE_FOUR_IMMUTABLE, "maintenance_receipts", "store_coverage"):
                    connection.execute(f"DROP TABLE {table}")
                connection.execute("ALTER TABLE source_grants DROP COLUMN expires_at")
            connection.execute("DELETE FROM schema_versions WHERE version>1")
            connection.execute("PRAGMA user_version=1")
            connection.commit()


@pytest.mark.integration
def test_schema_one_upgrade_backs_up_and_preserves_ids_evidence(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft
) -> None:
    character = source_fixture.character_id
    memory = (
        engine.character(character, authority=AUTHORITY)
        .remember(RememberRequest(idempotency_key="legacy", draft=draft))
        .affected[0]
        .memory_id
    )
    root = engine.root
    engine.close()
    _legacy(root)
    with CanonicalEngine(root) as reopened:
        assert reopened.character(character).get(memory).memory_id == memory
        assert reopened.character(character).get_sources(memory)[0].quote == TEXT
    from contextlib import closing

    for backup in root.rglob("*.schema-1.backup.db"):
        with closing(sqlite3.connect(backup)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert len(tuple(root.rglob("*.schema-1.backup.db"))) == 3


@pytest.mark.integration
def test_failed_upgrade_rolls_back_library_and_can_resume(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    character = source_fixture.character_id
    memory = (
        engine.character(character, authority=AUTHORITY)
        .remember(RememberRequest(idempotency_key="legacy", draft=draft))
        .affected[0]
        .memory_id
    )
    root = engine.root
    engine.close()
    _legacy(root)
    import ember_memory_engine.database as database_module
    from ember_memory_engine.errors import StorageError

    original = database_module.PHASE_FOUR_SQL
    monkeypatch.setattr(database_module, "PHASE_FOUR_SQL", original + "INVALID SQL;")
    with pytest.raises(StorageError):
        CanonicalEngine(root)
    library_path = (
        root / "libraries" / str(source_fixture.message.source.source_store_id) / "memory.db"
    )
    from contextlib import closing

    with closing(sqlite3.connect(library_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "expires_at" not in {
            row[1] for row in connection.execute("PRAGMA table_info(source_grants)")
        }
    monkeypatch.setattr(database_module, "PHASE_FOUR_SQL", original)
    with CanonicalEngine(root) as reopened:
        assert reopened.character(character).get_sources(memory)[0].quote == TEXT


@pytest.mark.integration
def test_selective_export_keeps_suppression_and_omits_forgotten_evidence(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft, tmp_path: Path
) -> None:
    first, _, memory = _seed(engine, source_fixture, draft)
    engine.character(first, authority=AUTHORITY).forget(
        ForgetRequest(
            idempotency_key="forget",
            memory_id=memory,
            expected_revision=1,
            reason="synthetic forget",
        )
    )
    root = engine.root
    engine.close()
    output = tmp_path / "forgotten"
    manifest = BundleService(root).export_character(first, output, mode="self_contained")
    assert manifest.evidence_closure == ()
    restored = tmp_path / "restored"
    restore_bundles((output,), restored)
    with CanonicalEngine(restored) as reopened:
        assert reopened.character(first).search(MemoryQuery()).results == ()
        with pytest.raises(AccessDeniedError):
            reopened.character(first).get(memory)
        _, database = reopened.maintenance_store(first)
        assert (
            database.read(
                lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM suppression_targets"
                ).fetchone()[0]
            )
            > 0
        )


@pytest.mark.integration
def test_selective_export_does_not_copy_unrelated_text_in_same_message(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft, tmp_path: Path
) -> None:
    from ember_memory_abi.evidence import Evidence, EvidenceAssertion, make_evidence_span

    # TEXT contains the GPU assertion and unrelated material in the same part.
    chosen = "My primary GPU is SYNTHETIC_A"
    evidence = Evidence(
        evidence_id=uuid4(),
        source=source_fixture.evidence_ref,
        span=make_evidence_span(TEXT, 0, len(chosen)),
    )
    limited = MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "content": {
                **draft.content.model_dump(),
                "evidence": (
                    EvidenceAssertion(
                        evidence=evidence,
                        relation="supports",
                        modality="FACTUAL_ASSERTION",
                        authority="confirmed_user",
                        verdict="manual",
                    ),
                ),
            },
        }
    )
    first, _, memory = _seed(engine, source_fixture, limited)
    root = engine.root
    engine.close()
    output = tmp_path / "partial"
    manifest = BundleService(root).export_character(
        first, output, mode="self_contained", memory_ids=(memory,)
    )
    assert all(
        b"synthetic example" not in (output / item.path).read_bytes() for item in manifest.files
    )
    restored = tmp_path / "restored"
    restore_bundles((output,), restored)
    with CanonicalEngine(restored) as reopened:
        assert reopened.character(first).get_sources(memory)[0].quote == chosen


@pytest.mark.integration
def test_publication_failure_leaves_no_visible_partial_bundle(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = engine.root
    library = source_fixture.message.source.source_store_id
    engine.close()
    import ember_memory_engine.bundles as bundle_module

    def fail(*_: object, **_kwargs: object) -> object:
        raise OSError("synthetic failure")

    monkeypatch.setattr(bundle_module, "_manifest", fail)
    with pytest.raises(BundleError):
        BundleService(root).export_source(library, tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not tuple(tmp_path.glob(".ember-bundle-*"))


@pytest.mark.integration
@pytest.mark.end_to_end
def test_operator_cli_seed_and_portable_restore(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ember_memory_cli.main import main

    _, request, selection = _approved(engine, source_fixture, draft)
    character = engine.create_character().store_id
    root = engine.root
    engine.close()
    assert (
        main(
            [
                "seed",
                "--root",
                str(root),
                "--character",
                str(character),
                "--library",
                str(request.candidate.owner_store_id),
                "--knowledge",
                str(selection.knowledge_id) + "@1",
                "--key",
                "cli-seed",
                "--yes",
            ]
        )
        == 0
    )
    receipt = json.loads(capsys.readouterr().out)
    memory = receipt["affected"][0]["memory_id"]
    assert (
        main(
            [
                "bundle",
                "character",
                "--root",
                str(root),
                "--character",
                str(character),
                "--output",
                str(tmp_path / "portable"),
                "--self-contained",
                "--memory",
                memory,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "bundle",
                "restore",
                "--root",
                str(tmp_path / "restored"),
                "--bundle",
                str(tmp_path / "portable"),
            ]
        )
        == 0
    )
    assert "providers disabled" in capsys.readouterr().out
    assert (
        main(
            [
                "bundle",
                "restore",
                "--root",
                str(tmp_path / "restored"),
                "--bundle",
                str(tmp_path / "portable"),
            ]
        )
        == 2
    )
    assert TEXT not in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["quote", "archive"])
def test_grants_expiring_during_source_resolution_are_not_delivered(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    from datetime import timedelta

    character = source_fixture.character_id
    memory = (
        engine.character(character, authority=AUTHORITY)
        .remember(RememberRequest(idempotency_key="m", draft=draft))
        .affected[0]
        .memory_id
    )
    engine.grant_source(
        character,
        SourceGrant(
            references=(source_fixture.message.source,),
            capabilities=frozenset({"evidence_read", "archive_search"}),
            expires_at=timestamp_from_datetime(clock.now() + timedelta(seconds=1)),
        ),
    )
    original = engine.resolve

    def delayed(reference: object) -> object:
        result = original(reference)
        clock.advance(2)
        return result

    monkeypatch.setattr(engine, "resolve", delayed)
    if mode == "quote":
        with pytest.raises(AccessDeniedError):
            engine.character(character).get_sources(memory)
    else:
        assert engine.character(character).search_archive(ArchiveQuery()).results == ()


@pytest.mark.integration
def test_failed_multi_seed_rolls_back_every_destination_effect(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ember_memory_engine._state import CharacterState
    from ember_memory_engine.errors import StorageError

    service, request, selection = _approved(engine, source_fixture, draft)
    second_draft = request.candidate.draft.model_copy(
        update={
            "content": request.candidate.draft.content.model_copy(
                update={
                    "payload": request.candidate.draft.content.payload.model_copy(
                        update={"value": StringValue(value="SYNTHETIC_B")}
                    )
                }
            )
        }
    )
    second_candidate = request.candidate.model_copy(
        update={"candidate_id": uuid4(), "draft": second_draft}
    )
    second = service.approve(
        request.candidate.owner_store_id,
        ApprovalRequest(idempotency_key="approval-2", candidate=second_candidate),
    )
    character = engine.create_character().store_id
    seed = SeedRequest(
        idempotency_key="multi-seed",
        library_id=request.candidate.owner_store_id,
        selections=(selection, KnowledgeSelection(knowledge_id=second.knowledge_id, revision=1)),
    )
    original = CharacterState.append_revision
    calls = 0

    def fail_after_first(
        state: CharacterState, connection: sqlite3.Connection, memory: object
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise StorageError()
        original(state, connection, memory)

    monkeypatch.setattr(CharacterState, "append_revision", fail_after_first)
    with pytest.raises(StorageError):
        service.seed(character, seed)
    _, database = engine.maintenance_store(character)
    for table in (
        "memories",
        "memory_revisions",
        "source_grants",
        "entities",
        "entity_origins",
        "maintenance_receipts",
        "audit_events",
        "index_outbox",
    ):
        assert (
            database.read(
                lambda connection, table=table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
            == 0
        )
    monkeypatch.setattr(CharacterState, "append_revision", original)
    assert len(service.seed(character, seed).affected) == 2


@pytest.mark.integration
def test_linked_restore_rejects_library_snapshot_before_approval(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft, tmp_path: Path
) -> None:
    root = engine.root
    library = source_fixture.message.source.source_store_id
    engine.close()
    old_source = tmp_path / "old-source"
    BundleService(root).export_source(library, old_source)
    with CanonicalEngine(root) as reopened:
        first, _, _ = _seed(reopened, source_fixture, draft)
    character = tmp_path / "character"
    BundleService(root).export_character(first, character)
    with pytest.raises(BundleError):
        restore_bundles((old_source, character), tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_seed_entity_aliases_allow_duplicate_source_context(
    engine: CanonicalEngine, source_fixture: SourceFixture, draft: MemoryDraft
) -> None:
    service, request, _ = _approved(engine, source_fixture, draft)
    origin = request.candidate.draft.content.payload.subject
    service.put_entity(
        origin.store_id,
        Entity(
            reference=origin,
            revision=2,
            name="Synthetic user",
            aliases=("Synthetic user", "user-alias", "user-alias"),
        ),
    )
    candidate = request.candidate.model_copy(update={"candidate_id": uuid4()})
    approved = service.approve(
        origin.store_id, ApprovalRequest(idempotency_key="alias-approval", candidate=candidate)
    )
    character = engine.create_character().store_id
    memory = (
        service.seed(
            character,
            SeedRequest(
                idempotency_key="alias-seed",
                library_id=origin.store_id,
                selections=(KnowledgeSelection(knowledge_id=approved.knowledge_id, revision=1),),
            ),
        )
        .affected[0]
        .memory_id
    )
    assert engine.character(character).get_sources(memory)[0].quote == TEXT
    _, database = engine.maintenance_store(character)
    assert (
        database.read(
            lambda connection: connection.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[
                0
            ]
        )
        == 2
    )


@pytest.mark.integration
def test_bundle_projection_databases_use_local_workspace(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, _, _ = _seed(engine, source_fixture, draft)
    root = engine.root
    engine.close()
    import ember_memory_engine.bundles as bundle_module

    original = bundle_module.SqliteDatabase

    def local(path: Path, *args: object, **kwargs: object) -> object:
        assert not path.is_relative_to(tmp_path / "portable"), (
            "Active projection DBs belong in local scratch space."
        )
        assert not any(part.name.startswith(".ember-bundle-") for part in path.parents)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(bundle_module, "SqliteDatabase", local)
    manifest = BundleService(root).export_character(
        first, tmp_path / "portable", mode="self_contained"
    )
    assert manifest.kind == "character"


@pytest.mark.integration
def test_schema_two_upgrade_backs_up_and_retries_failed_ddl(
    engine: CanonicalEngine,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import closing

    import ember_memory_engine.database as database_module
    from ember_memory_engine.errors import StorageError

    identity = source_fixture.character_id
    memory_id = (
        engine.character(identity, authority=AUTHORITY)
        .remember(RememberRequest(idempotency_key="schema-two", draft=draft))
        .affected[0]
        .memory_id
    )
    before = engine.character(identity).get(memory_id).model_dump_json()
    root = engine.root
    engine.close()
    paths = (
        root / "registry.db",
        *root.glob("libraries/*/memory.db"),
        *root.glob("characters/*/memory.db"),
    )
    for path in paths:
        with closing(sqlite3.connect(path)) as connection:
            if path.name != "registry.db":
                for name in (
                    "memory_retrieval_identity",
                    "memory_retrieval_order",
                    "memory_pointer",
                    "source_conversation",
                ):
                    connection.execute(f"DROP INDEX {name}")
            connection.execute("DELETE FROM schema_versions WHERE version>2")
            connection.execute("PRAGMA user_version=2")
            connection.commit()
    original = database_module.PHASE_FIVE_SQL
    monkeypatch.setattr(database_module, "PHASE_FIVE_SQL", original + "INVALID SQL;")
    with pytest.raises(StorageError):
        CanonicalEngine(root)
    character_path = root / "characters" / str(identity) / "memory.db"
    with closing(sqlite3.connect(character_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    monkeypatch.setattr(database_module, "PHASE_FIVE_SQL", original)
    with CanonicalEngine(root) as reopened:
        assert reopened.character(identity).get(memory_id).model_dump_json() == before
        assert reopened.character(identity).get_sources(memory_id)[0].quote == TEXT
    for backup in root.rglob("*.schema-2*.backup.db"):
        with closing(sqlite3.connect(backup)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert len(tuple(root.rglob("*.schema-2*.backup.db"))) >= 3
