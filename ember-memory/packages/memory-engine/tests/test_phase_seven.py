"""Phase 7 acceptance on sanitized native sources, including restart and suppression."""

import asyncio
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ember_memory_abi.archive import (
    ActorAttribution,
    ContentPart,
    Conversation,
    ImportCursor,
    Message,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.common import CanonicalId, timestamp_from_datetime
from ember_memory_abi.evidence import SourceRef, source_text_sha256
from ember_memory_abi.interfaces import (
    ContextRequest,
    DisableRequest,
    ForgetRequest,
    MemoryQuery,
    PinRequest,
    RetrievalContext,
    TemporalQuery,
)
from ember_memory_abi.related import RelatedRequest
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.authoring import ensure_subject
from ember_memory_engine.consolidation import (
    NativeAcceptanceReceipt,
    NativeAcceptanceRequest,
    NativeAcceptanceService,
)
from ember_memory_engine.database import row_text
from ember_memory_engine.models import SourceKey, SourceKeyAssignment
from ember_memory_engine.processing import ProcessingService
from ember_memory_engine.processing_contracts import (
    ClaimBatch,
    FactClaim,
    JobRequest,
    ModelWindow,
    native_specification,
)
from ember_memory_engine.profile import ProfileSettings

SPEC = native_specification("synthetic", "a" * 64)
AUTHORITY = MutationAuthority(mode="confirmed_user")


def append_native(
    engine: CanonicalEngine,
    character: CanonicalId,
    text: str,
    *,
    role: str = "user",
    bound: bool = True,
    observed: str | None = None,
) -> Message:
    subject = ensure_subject(engine, character)
    stamp = observed or timestamp_from_datetime(engine.clock.now())
    archive = engine.archive(character)
    conversation = SourceRef(
        source_store_id=character, record_kind="conversation", record_id=uuid4(), record_revision=1
    )
    ref = SourceRef(
        source_store_id=character, record_kind="message", record_id=uuid4(), record_revision=1
    )
    from ember_memory_abi.temporal import SourceTimestamp

    message = Message(
        source=ref,
        recorded_at=stamp,
        conversation=conversation,
        channel="final",
        actor=ActorAttribution(role=role, actor_id=subject.entity_id if bound else None),
        created_time=SourceTimestamp(value=stamp, basis="runtime_delivery_observed"),
        parts=(
            ContentPart(
                part_id=uuid4(),
                kind="text",
                status="available",
                text=text,
                content_sha256=source_text_sha256(text),
            ),
        ),
    )
    batch = NormalizedArchiveBatch(
        source_store_id=character,
        source_namespace=archive.namespace,
        idempotency_key=str(uuid4()),
        processing_spec=ProcessingSpecRef(
            name="ember.native_delivery", version="0.2.0", specification_sha256="b" * 64
        ),
        records=(Conversation(source=conversation, recorded_at=stamp), message),
        resume_cursor=ImportCursor(
            adapter="native_delivery",
            adapter_version="0.2.0",
            logical_inventory_sha256="c" * 64,
            component_sha256="d" * 64,
            next_record_index=2,
        ),
    )
    keys = (
        (
            SourceKeyAssignment(
                key=SourceKey(
                    adapter="native_delivery",
                    record_kind="message",
                    external_id="v2:" + role + ":" + str(ref.record_id),
                ),
                record_id=ref.record_id,
            ),
        )
        if bound
        else ()
    )
    archive.write_batch(batch, keys=keys)
    return message


class NativeModel:
    specification = SPEC

    def __init__(
        self,
        predicate: str,
        value: str,
        *,
        modality: str = "FACTUAL_ASSERTION",
        confidence: float = 0.97,
    ) -> None:
        self.predicate = predicate
        self.value = value
        self.modality = modality
        self.confidence = confidence

    async def extract(self, window: ModelWindow) -> ClaimBatch:
        return ClaimBatch(
            claims=(
                FactClaim(
                    kind="fact",
                    statement=window.text,
                    quote=window.text,
                    quote_start=None,
                    predicate=self.predicate,
                    value=self.value,
                    entity_name=None,
                    modality=self.modality,
                    confidence=self.confidence,
                    importance=0.8,
                    verdict="supported",
                ),
            )
        )


def extract(
    engine: CanonicalEngine, character: CanonicalId, model: NativeModel
) -> tuple[CanonicalId, ...]:
    service = ProcessingService(engine, character)
    job = service.create(
        JobRequest(
            idempotency_key=str(uuid4()),
            owner_store_id=character,
            specification=SPEC,
            source_kind="native",
        )
    )
    status = asyncio.run(service.run(job.definition.job_id, model, continue_on_error=True))
    assert status.status == "complete" or status.run_outcome == "finished_with_failures"
    candidates = service.database.read(
        lambda connection: tuple(
            UUID(row_text(row, "candidate_id"))
            for row in connection.execute(
                "SELECT candidate_id FROM processing_candidates WHERE job_id=? ORDER BY ordinal",
                (str(job.definition.job_id),),
            )
        )
    )
    return candidates


def accept(
    engine: CanonicalEngine, character: CanonicalId, candidate: CanonicalId, key: str | None = None
) -> NativeAcceptanceReceipt:
    return NativeAcceptanceService(engine, character).accept(
        NativeAcceptanceRequest(candidate_id=candidate, idempotency_key=key or str(uuid4()))
    )


def context(engine: CanonicalEngine, character: CanonicalId) -> RetrievalContext:
    return engine.character(character).retrieve_context(
        ContextRequest(
            memory_query=MemoryQuery(mode="structured"),
            memory_token_budget=4096,
            include_profile=True,
        )
    )


def test_native_acceptance_support_replay_and_restart(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        candidate = extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0]
        first = accept(engine, character, candidate, "first")
        assert first.outcome == "accepted"
        assert accept(engine, character, candidate, "first").replay
        append_native(engine, character, "Please call me Rowan.")
        candidates = extract(engine, character, NativeModel("user.preferred_name", "Rowan"))
        assert all(accept(engine, character, c).outcome == "supported" for c in candidates)
        assert len(engine.character(character).search(MemoryQuery()).results) == 1
        store = engine.character(character)
        assert (
            store.database.read(
                lambda c: c.execute("SELECT COUNT(*) FROM memory_supports").fetchone()[0]
            )
            == 2
        )
        assert context(engine, character).profile.automatic[0].memory_id == first.memory.memory_id
    with CanonicalEngine(root) as reopened:
        assert context(reopened, character).profile.automatic[0].memory_id == first.memory.memory_id


@pytest.mark.parametrize(
    "text,role,bound,confidence",
    [
        ("Please call me Rowan.", "assistant", True, 0.97),
        ("Please call me Rowan.", "user", False, 0.97),
        ("Maybe please call me Rowan.", "user", True, 0.97),
        ("Please call me Rowan.", "user", True, 0.5),
        ("My colleague said please call me Rowan.", "user", True, 0.97),
    ],
)
def test_unsupported_native_proposals_never_become_profile(
    tmp_path: Path, text: str, role: str, bound: bool, confidence: float
) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        append_native(engine, character, text, role=role, bound=bound)
        candidates = extract(
            engine, character, NativeModel("user.preferred_name", "Rowan", confidence=confidence)
        )
        for candidate in candidates:
            assert accept(engine, character, candidate).outcome in {"deferred", "rejected"}
        assert not context(engine, character).profile.automatic


def test_explicit_correction_and_conflict_history(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        first = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        )
        append_native(engine, character, "Actually, please call me Sky.")
        candidates = extract(engine, character, NativeModel("user.preferred_name", "Sky"))
        correction = accept(engine, character, candidates[-1])
        assert correction.outcome == "superseded"
        assert (
            context(engine, character).profile.automatic[0].memory_id == correction.memory.memory_id
        )
        resolved = engine.character(character).lookup_related(
            RelatedRequest(intent="resolve", anchors=(first.memory,))
        )
        assert resolved.relationships[0].kind == "supersedes"
        assert resolved.memories[-1].memory_id == correction.memory.memory_id
        old = engine.character(character).get(first.memory.memory_id)
        before = engine.character(character).search(
            MemoryQuery(
                mode="structured", temporal=TemporalQuery(recorded_before=old.temporal.recorded_at)
            )
        )
        assert before.results[0].memory.memory_id == first.memory.memory_id
        assert before.results[0].memory.content.payload.value.value == "Rowan"
        assert old.lifecycle == "superseded" and old.temporal.valid_until is None
        append_native(engine, character, "Please call me Alex.")
        candidate = extract(engine, character, NativeModel("user.preferred_name", "Alex"))[-1]
        assert accept(engine, character, candidate).outcome == "deferred"
        assert not context(engine, character).profile.automatic


@pytest.mark.parametrize("operation", ["disable", "forget"])
def test_inactive_beliefs_cannot_resurrect_under_new_job(tmp_path: Path, operation: str) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        first = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        )
        store = engine.character(character, authority=AUTHORITY)
        request = (DisableRequest if operation == "disable" else ForgetRequest)(
            idempotency_key="stop",
            memory_id=first.memory.memory_id,
            expected_revision=1,
            reason="operator_choice",
        )
        getattr(store, operation)(request)
        append_native(engine, character, "Please call me Rowan.")
        for candidate in extract(engine, character, NativeModel("user.preferred_name", "Rowan")):
            assert accept(engine, character, candidate).outcome in {"deferred", "rejected"}
        assert not context(engine, character).profile.automatic
        related = store.lookup_related(RelatedRequest(intent="resolve", anchors=(first.memory,)))
        assert not related.memories


def test_profile_exclusions_manual_pin_ownership_and_batch_validation(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        memory = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        ).memory
        store = engine.character(character, authority=AUTHORITY)
        old = context(engine, character).fast_dependencies
        assert store.lookup_related(RelatedRequest(intent="validate", dependencies=old)).valid
        store.pin(
            PinRequest(
                idempotency_key="pin", memory_id=memory.memory_id, expected_revision=1, priority=3
            )
        )
        store.configure_profile(
            ProfileSettings(
                subject_id=ensure_subject(engine, character).entity_id,
                excluded=("user.preferred_name",),
            )
        )
        selected = context(engine, character)
        assert not selected.profile.automatic
        assert selected.pinned[0].purpose == "manual"
        assert not store.lookup_related(RelatedRequest(intent="validate", dependencies=old)).valid
    with CanonicalEngine(root) as reopened:
        assert reopened.character(character).profile_settings().excluded == ("user.preferred_name",)


def test_temporal_observed_filters_and_unknown_validity(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        stamp = "2025-01-01T00:00:00.000000Z"
        append_native(engine, character, "Please call me Rowan.", observed=stamp)
        first = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        )
        store = engine.character(character)
        found = store.search(
            MemoryQuery(
                mode="structured",
                temporal=TemporalQuery(
                    observed_from=stamp, observed_until="2025-01-02T00:00:00.000000Z"
                ),
            )
        )
        assert found.results[0].memory.memory_id == first.memory.memory_id
        assert not store.search(MemoryQuery(temporal=TemporalQuery(observed_until=stamp))).results
        assert not store.search(MemoryQuery(temporal=TemporalQuery(valid_at=stamp))).results


def test_schema_four_upgrade_backup_and_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ember_memory_engine.database as database_module
    from ember_memory_engine.schema import PHASE_SEVEN_IMMUTABLE

    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        first = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        )
        path = engine.character(character).database.path
    with sqlite3.connect(path) as connection:
        for table in (
            *PHASE_SEVEN_IMMUTABLE,
            "claim_projection",
            "profile_policy",
            "profile_exclusions",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_versions WHERE version=5")
        connection.execute("PRAGMA user_version=4")
    original = database_module.PHASE_SEVEN_SQL
    monkeypatch.setattr(database_module, "PHASE_SEVEN_SQL", original + " INVALID DDL;")
    from ember_memory_engine.errors import StorageError

    with pytest.raises(StorageError):
        CanonicalEngine(root)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert path.with_suffix(".schema-4.backup.db").is_file()
    monkeypatch.setattr(database_module, "PHASE_SEVEN_SQL", original)
    with CanonicalEngine(root) as reopened:
        assert reopened.character(character).get(first.memory.memory_id).revision == 1
        ensure_subject(reopened, character)
        assert context(reopened, character).profile.automatic


def test_literal_owned_object_repeated_support_and_no_index_rebuild(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        quote = "Milo is my shark plushie."
        append_native(engine, character, quote)
        model = NativeModel("personal.owns_object", quote)
        candidate = extract(engine, character, model)[0]
        first = accept(engine, character, candidate)
        assert first.outcome == "accepted"
        store = engine.character(character)
        before = store.database.read(
            lambda c: c.execute("SELECT MAX(sequence) FROM index_outbox").fetchone()[0]
        )
        acceptance = NativeAcceptanceService(engine, character)
        for n in range(1000):
            assert (
                acceptance.accept(
                    NativeAcceptanceRequest(
                        candidate_id=candidate, idempotency_key="support:" + str(n)
                    )
                ).outcome
                == "supported"
            )
        assert (
            store.database.read(lambda c: c.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
            == 1
        )
        assert (
            store.database.read(
                lambda c: c.execute("SELECT COUNT(*) FROM memory_revisions").fetchone()[0]
            )
            == 1
        )
        assert (
            store.database.read(
                lambda c: c.execute("SELECT COUNT(*) FROM memory_supports").fetchone()[0]
            )
            == 1
        )
        assert (
            store.database.read(
                lambda c: c.execute("SELECT MAX(sequence) FROM index_outbox").fetchone()[0]
            )
            == before
        )
        expanded = store.lookup_related(RelatedRequest(intent="expand", anchors=(first.memory,)))
        assert expanded.relationships[0].evidence[0].evidence.source.source_store_id == character


def test_manual_rejection_resolves_conflict_and_survives_new_job(tmp_path: Path) -> None:
    from ember_memory_engine.processing_review import ReviewService

    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        )
        append_native(engine, character, "Please call me Sky.")
        model = NativeModel("user.preferred_name", "Sky")
        candidate = extract(engine, character, model)[-1]
        assert accept(engine, character, candidate).outcome == "deferred"
        assert not context(engine, character).profile.automatic
        service = ProcessingService(engine, character)
        job = service.definitions()[0]
        reviewer = ReviewService(service, authority=AUTHORITY)
        review = reviewer.export(job.job_id)
        selected = tuple(
            entry.model_copy(update={"decision": "reject"})
            for entry in review.entries
            if entry.candidate.candidate_id == candidate
        )
        reviewer.apply(review.model_copy(update={"entries": selected}))
        assert context(engine, character).profile.automatic
        newer = extract(engine, character, model)[-1]
        assert accept(engine, character, newer).outcome == "rejected"


def test_source_revision_edit_omits_old_profile_and_invalidates_context(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        original = append_native(engine, character, "Please call me Rowan.")
        accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        )
        before = context(engine, character)
        part = original.parts[0].model_copy(
            update={
                "text": "Please call me Sky.",
                "content_sha256": source_text_sha256("Please call me Sky."),
            }
        )
        revised = original.model_copy(
            update={
                "source": original.source.model_copy(update={"record_revision": 2}),
                "parts": (part,),
            }
        )
        archive = engine.archive(character)
        batch = NormalizedArchiveBatch(
            source_store_id=character,
            source_namespace=archive.namespace,
            idempotency_key="source-edit",
            processing_spec=ProcessingSpecRef(
                name="synthetic_edit", version="0.1.0", specification_sha256="e" * 64
            ),
            records=(revised,),
            resume_cursor=ImportCursor(
                adapter="synthetic_edit",
                adapter_version="0.1.0",
                logical_inventory_sha256="f" * 64,
                component_sha256="a" * 64,
                next_record_index=1,
            ),
        )
        archive.write_batch(batch)
        after = context(engine, character)
        assert not after.profile.automatic
        assert after.coverage.status == "partial"
        assert any(x.reason == "source_unavailable_or_changed" for x in after.profile.omitted)
        assert (
            not engine.character(character)
            .lookup_related(
                RelatedRequest(intent="validate", dependencies=before.fast_dependencies)
            )
            .valid
        )


def test_legacy_extractor_hashes_remain_exact() -> None:
    from ember_memory_engine.processing_contracts import ExtractionSpec

    spec = ExtractionSpec(model="synthetic", model_digest="a" * 64)
    assert spec.prompt_sha256 == "0fd8825ec58270c64608f78ca40b5f84ab5729b18c021b0cbf498bdab9b0ed66"
    assert (
        spec.output_schema_sha256
        == "9147a8e03bb2148d4c72a1fb25670e1a1ef368883ed2c81285fd2f82a94e66fd"
    )
    assert spec.policy_sha256 == "43332c2c8853f67eddb5ed608e4056f33d19e6b29fd8256c9c35b2afa58fb8a5"


def test_consolidation_redirect_and_interrupted_transaction_preserve_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ember_memory_abi.interfaces import RememberRequest
    from ember_memory_abi.memory import MemoryDraft
    from ember_memory_abi.related import MemoryReference
    from ember_memory_engine._state import CharacterState
    from ember_memory_engine.consolidation import ConsolidateRequest, consolidate

    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        first = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        ).memory
        store = engine.character(character, authority=AUTHORITY)
        memory = store.get(first.memory_id)
        duplicate = store.remember(
            RememberRequest(
                idempotency_key="legacy-copy", draft=MemoryDraft(content=memory.content)
            )
        )
        second = MemoryReference(memory_id=duplicate.affected[0].memory_id, revision=1)
        request = ConsolidateRequest(idempotency_key="merge", survivor=first, duplicate=second)
        original = CharacterState.append_revision

        def interrupted(
            self: CharacterState, connection: sqlite3.Connection, revision: object
        ) -> None:
            raise RuntimeError("synthetic interruption")

        monkeypatch.setattr(CharacterState, "append_revision", interrupted)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            consolidate(engine, character, request, authority=AUTHORITY)
        assert store.get(second.memory_id).lifecycle == "active"
        monkeypatch.setattr(CharacterState, "append_revision", original)
        receipt = consolidate(engine, character, request, authority=AUTHORITY)
        assert (
            consolidate(engine, character, request, authority=AUTHORITY).operation_id
            == receipt.operation_id
        )
        assert store.get(first.memory_id).revision == 1
        assert store.get(second.memory_id).lifecycle == "consolidated"
        resolved = store.lookup_related(RelatedRequest(intent="resolve", anchors=(second,)))
        assert any(e.kind == "redirects" and e.target == first for e in resolved.relationships)


def test_schema_four_supersessions_are_backfilled_as_typed_relationships(tmp_path: Path) -> None:
    from ember_memory_engine.schema import PHASE_SEVEN_IMMUTABLE

    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        first = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        ).memory
        append_native(engine, character, "Actually, please call me Sky.")
        replacement = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Sky"))[-1],
        ).memory
        path = engine.character(character).database.path
    with sqlite3.connect(path) as connection:
        for table in (
            *PHASE_SEVEN_IMMUTABLE,
            "claim_projection",
            "profile_policy",
            "profile_exclusions",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_versions WHERE version=5")
        connection.execute("PRAGMA user_version=4")
    with CanonicalEngine(root) as engine:
        resolved = engine.character(character).lookup_related(
            RelatedRequest(intent="resolve", anchors=(first,))
        )
        assert resolved.relationships[0].kind == "supersedes"
        assert resolved.relationships[0].target == replacement
        assert resolved.relationships[0].evidence


def test_cli_policy_acceptance_profile_inspection_and_owner_diagnostics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    from ember_memory_cli.phase_seven import main

    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        candidates = extract(engine, character, NativeModel("user.preferred_name", "Rowan"))
        job = ProcessingService(engine, character).definitions()[0].job_id
        assert main(["profile", "inspect", "--character", str(character), "--root", str(root)]) == 2
        error = capsys.readouterr().err
        assert "owner" in error and str(root) not in error
    bound = ["--character", str(character), "--root", str(root)]
    assert main(["native", "accept", "--job", str(job), *bound]) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["candidate_id"] == str(candidates[0]) and accepted["outcome"] == "accepted"
    assert main(["profile", "inspect", "--evidence", *bound]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert (
        inspected["profile"]["automatic"]
        and inspected["evidence"][0]["quote"] == "Please call me Rowan."
    )
    assert main(["profile", "disable", *bound]) == 0
    assert not json.loads(capsys.readouterr().out)["enabled"]
