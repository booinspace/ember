"""Phase 6 source fidelity, policy, recovery, independent seeds and review authority."""

import asyncio
import json
import sqlite3
from collections.abc import Generator
from pathlib import Path
from uuid import uuid4

import pytest

from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_cli.maintenance import EngineImportTarget
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.library import KnowledgeSelection, LibraryService, SeedRequest
from ember_memory_engine.processing import ProcessingService
from ember_memory_engine.processing_contracts import (
    ClaimBatch,
    ExtractionSpec,
    FactClaim,
    JobDefinition,
    JobRequest,
    JobStatus,
    ModelWindow,
    OtherClaim,
    ProcessingError,
    WindowRef,
)
from ember_memory_engine.processing_review import ReviewService
from ember_memory_importers.normalization.memory_summary import import_summary

SPEC = ExtractionSpec(model="synthetic", model_digest="a" * 64, window_bytes=256)


class FakeModel:
    def __init__(self, *, fail_at: int | None = None, invalid_quote: bool = False) -> None:
        self.specification = SPEC
        self.calls = 0
        self.fail_at = fail_at
        self.invalid_quote = invalid_quote
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def extract(self, window: ModelWindow) -> ClaimBatch:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.fail_at == self.calls:
            raise ProcessingError("synthetic_model_failure")
        quote = "invented evidence" if self.invalid_quote else window.text
        return ClaimBatch(
            claims=(
                OtherClaim(
                    kind="episode",
                    statement=window.text,
                    quote=quote,
                    quote_start=0,
                    modality="PREFERENCE",
                    predicate=None,
                    value=None,
                    entity_name=None,
                    confidence=0.98,
                    importance=0.8,
                    verdict="supported",
                ),
            )
        )


def summary(
    engine: CanonicalEngine, tmp_path: Path, entries: tuple[str, ...]
) -> tuple[ProcessingService, Path]:
    source = tmp_path / ("summary-" + uuid4().hex + ".json")
    source.write_text(json.dumps({"memories": list(entries)}, ensure_ascii=False), encoding="utf-8")
    library = engine.create_library("summary-" + uuid4().hex)
    import_summary(
        source,
        target=EngineImportTarget(engine.archive(library.store_id)),
        raw_vault=tmp_path / "raw",
        manifests=tmp_path / "manifests",
        format="json",
    )
    return ProcessingService(engine, library.store_id), source


def create(service: ProcessingService, key: str = "job") -> JobStatus:
    return service.create(
        JobRequest(idempotency_key=key, owner_store_id=service.owner, specification=SPEC)
    )


def test_summary_candidates_stay_below_automatic_threshold_until_confirmed(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, ("I love jazz and café music.",))
    job = create(service)
    model = FakeModel()
    done = asyncio.run(service.run(job.definition.job_id, model))
    assert done.status == "complete" and done.candidates == 1
    review = ReviewService(service).export(job.definition.job_id)
    entry = review.entries[0]
    assert entry.quotes == ("I love jazz and café music.",)
    assert entry.candidate.draft.content.confidence == 0.79
    assert entry.candidate.draft.content.modality == "PREFERENCE"
    assert entry.policy_decision.verdict.verdict == "requires_confirmation"
    assert (
        "summary_requires_independent_support_or_confirmation"
        in entry.policy_decision.verdict.reasons
    )
    assert (
        service.database.read(
            lambda c: c.execute("SELECT COUNT(*) FROM approved_knowledge").fetchone()[0]
        )
        == 0
    )
    assert model.calls == 1
    assert asyncio.run(service.run(job.definition.job_id, model)) == done and model.calls == 1


def test_review_seed_restart_retains_exact_summary_and_modality(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, ("I like café music and 🍰.",))
    job = create(service)
    asyncio.run(service.run(job.definition.job_id, FakeModel()))
    review_service = ReviewService(service, authority=MutationAuthority(mode="confirmed_user"))
    review = review_service.export(job.definition.job_id)
    review = review.model_copy(
        update={"entries": (review.entries[0].model_copy(update={"decision": "approve"}),)}
    )
    receipt = review_service.apply(review)
    assert review_service.apply(review) == receipt
    character = engine.create_character()
    seed = LibraryService(engine, authority=MutationAuthority(mode="confirmed_user")).seed(
        character.store_id,
        SeedRequest(
            idempotency_key="seed",
            library_id=service.owner,
            selections=(KnowledgeSelection(knowledge_id=receipt.approved[0], revision=1),),
        ),
    )
    identity = seed.affected[0].memory_id
    root = engine.root
    engine.close()
    with CanonicalEngine(root) as reopened:
        memory = reopened.character(character.store_id).get(identity)
        assert memory.content.modality == "PREFERENCE"
        assert (
            reopened.character(character.store_id).get_sources(identity)[0].quote
            == "I like café music and 🍰."
        )
        assert reopened.character(character.store_id).search(MemoryQuery()).results


def test_invalid_quote_fails_without_advancing_or_persisting_candidates(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, ("I like synthetic music.",))
    job = create(service)
    failed = asyncio.run(service.run(job.definition.job_id, FakeModel(invalid_quote=True)))
    assert failed.status == "failed" and failed.completed_windows == 0 and failed.candidates == 0
    assert failed.failures[0].ordinal == 0
    assert failed.failures[0].code == "quote_not_exact"
    assert failed.failures[0].attempts == 1
    assert (
        service.database.read(
            lambda c: c.execute("SELECT COUNT(*) FROM processing_failures").fetchone()[0]
        )
        == 1
    )
    done = asyncio.run(service.run(job.definition.job_id, FakeModel(), retry_failed=True))
    assert done.status == "complete" and done.candidates == 1


def test_resume_reuses_completed_windows_and_pins_specification(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, ("I like synthetic jazz.", "I like synthetic folk."))
    job = create(service)
    first = FakeModel(fail_at=2)
    failed = asyncio.run(service.run(job.definition.job_id, first))
    assert failed.completed_windows == 1 and failed.failed_windows == 1
    resumed = FakeModel()
    done = asyncio.run(service.run(job.definition.job_id, resumed, retry_failed=True))
    assert done.completed_windows == 2 and done.candidates == 2 and resumed.calls == 1
    assert create(service) == done
    with pytest.raises(ProcessingError, match="job_key_conflict"):
        service.create(
            JobRequest(
                idempotency_key="job",
                owner_store_id=service.owner,
                specification=SPEC.model_copy(update={"model_digest": "b" * 64}),
            )
        )


def test_rejection_survives_a_new_extraction_job(engine: CanonicalEngine, tmp_path: Path) -> None:
    service, _ = summary(engine, tmp_path, ("I prefer synthetic folk music.",))
    job = create(service)
    asyncio.run(service.run(job.definition.job_id, FakeModel()))
    reviewer = ReviewService(service, authority=MutationAuthority(mode="confirmed_user"))
    review = reviewer.export(job.definition.job_id)
    review = review.model_copy(
        update={"entries": (review.entries[0].model_copy(update={"decision": "reject"}),)}
    )
    reviewer.apply(review)
    second = create(service, "new-job")
    asyncio.run(service.run(second.definition.job_id, FakeModel()))
    candidate = reviewer.export(second.definition.job_id).entries[0]
    assert "operator_rejected_claim" in candidate.policy_decision.verdict.reasons
    assert "duplicate_proposal" in candidate.policy_decision.verdict.reasons


def test_review_payload_and_quotes_cannot_be_changed_or_auto_authorized(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, ("I like synthetic jazz.",))
    job = create(service)
    asyncio.run(service.run(job.definition.job_id, FakeModel()))
    reviewer = ReviewService(service)
    review = reviewer.export(job.definition.job_id)
    with pytest.raises(ProcessingError, match="operator_confirmation_required"):
        reviewer.apply(review)
    authorized = ReviewService(service, authority=MutationAuthority(mode="confirmed_user"))
    entry = review.entries[0].model_copy(update={"decision": "approve", "quotes": ("fabricated",)})
    with pytest.raises(ProcessingError, match="review_quotes_changed"):
        authorized.apply(review.model_copy(update={"entries": (entry,)}))
    assert (
        service.database.read(
            lambda c: c.execute("SELECT COUNT(*) FROM approved_knowledge").fetchone()[0]
        )
        == 0
    )


def test_cancel_and_concurrent_runner_keep_a_single_owned_job(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    async def run() -> None:
        service, _ = summary(engine, tmp_path, ("I like synthetic jazz.",))
        job = create(service)
        model = FakeModel()
        model.release.clear()
        task = asyncio.create_task(service.run(job.definition.job_id, model))
        await asyncio.wait_for(model.started.wait(), 2)
        with pytest.raises(ProcessingError, match="job_already_running"):
            await service.run(job.definition.job_id, FakeModel())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.status(job.definition.job_id).status == "paused"
        model.release.set()
        done = await service.run(job.definition.job_id, model)
        assert done.status == "complete"

    asyncio.run(run())


def test_window_spans_cover_unicode_and_do_not_silently_truncate(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, (("café 🍰 sentence. " * 100),))
    job = create(service)
    rows = service.database.read(
        lambda c: c.execute(
            "SELECT payload_json FROM processing_windows ORDER BY ordinal"
        ).fetchall()
    )
    from ember_memory_engine.processing_contracts import WindowRef

    references = [WindowRef.model_validate_json(row[0]) for row in rows]
    text = "café 🍰 sentence. " * 100
    covered = set()
    for ref in references:
        fragment = text[ref.start : ref.end]
        assert len(fragment.encode("utf-8")) <= 256
        covered.update(range(ref.start, ref.end))
    assert covered == set(range(len(text))) and job.total_windows > 1


def test_suppression_stops_processing_before_model_input(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, ("A synthetic summary observation.",))
    job = create(service)
    row = service.database.read(
        lambda c: c.execute("SELECT payload_json FROM processing_windows").fetchone()
    )
    from ember_memory_engine.processing_contracts import WindowRef
    from ember_memory_engine.source_access import source_keys

    reference = WindowRef.model_validate_json(row[0])
    service.database.write(
        lambda c: c.execute(
            "INSERT INTO suppression_targets VALUES('source',?)",
            (source_keys(reference.source)[0],),
        )
    )
    model = FakeModel()
    failed = asyncio.run(service.run(job.definition.job_id, model))
    assert failed.status == "failed" and model.calls == 0


def test_structured_step_commit_rolls_back_all_candidates(
    engine: CanonicalEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = summary(engine, tmp_path, ("A synthetic summary observation.",))
    job = create(service)
    # Fail the durable decision insertion after candidate creation, inside its transaction.
    original = service.database.transaction
    from contextlib import contextmanager

    @contextmanager
    def fail(connection: sqlite3.Connection) -> Generator[None]:
        with original(connection):
            yield
            if connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]:
                raise ProcessingError("synthetic_commit_failure")

    monkeypatch.setattr(service.database, "transaction", fail)
    failed = asyncio.run(service.run(job.definition.job_id, FakeModel()))
    assert failed.status == "failed" and failed.candidates == 0 and failed.completed_windows == 0


@pytest.mark.parametrize(
    "modality",
    [
        "BELIEF",
        "PREFERENCE",
        "INTENTION",
        "UNCERTAINTY",
        "HYPOTHETICAL",
        "PAST_STATE",
        "NEGATED_ASSERTION",
        "QUOTED_THIRD_PARTY_CLAIM",
    ],
)
def test_modalities_are_preserved_instead_of_upgraded_to_facts(
    engine: CanonicalEngine,
    tmp_path: Path,
    modality: str,
) -> None:
    service, _ = summary(engine, tmp_path, ("I might buy a synthetic GPU.",))
    job = create(service)
    model = FakeModel()
    original = model.extract

    async def extract(window: ModelWindow) -> ClaimBatch:
        batch = await original(window)
        return ClaimBatch.model_validate_json(
            json.dumps(
                {"claims": [dict(batch.claims[0].model_dump(mode="json"), modality=modality)]}
            )
        )

    model.extract = extract
    done = asyncio.run(service.run(job.definition.job_id, model))
    assert done.status == "complete"
    entry = ReviewService(service).export(job.definition.job_id).entries[0]
    assert entry.candidate.draft.content.modality == modality
    assert entry.policy_decision.verdict.verdict == "requires_confirmation"


def test_unknown_predicate_and_conflicts_remain_reviewable_unapproved(
    engine: CanonicalEngine,
    tmp_path: Path,
) -> None:
    service, _ = summary(
        engine, tmp_path, ("My primary GPU is SYNTHETIC_A.", "My primary GPU is SYNTHETIC_B.")
    )
    job = create(service)
    model = FakeModel()

    async def extract(window: ModelWindow) -> ClaimBatch:
        return ClaimBatch(
            claims=(
                FactClaim(
                    kind="fact",
                    statement=window.text,
                    quote=window.text,
                    quote_start=None,
                    modality="FACTUAL_ASSERTION",
                    predicate="hardware.primary_gpu",
                    value=window.text.split()[-1],
                    entity_name=None,
                    confidence=0.98,
                    importance=0.8,
                    verdict="supported",
                ),
            )
        )

    model.extract = extract
    assert asyncio.run(service.run(job.definition.job_id, model)).status == "complete"
    entries = ReviewService(service).export(job.definition.job_id).entries
    assert all("unresolved_conflict" in entry.policy_decision.verdict.reasons for entry in entries)


def test_unique_quote_offsets_are_host_computed_and_repeated_quotes_require_a_hint(
    engine: CanonicalEngine,
    tmp_path: Path,
) -> None:
    service, _ = summary(engine, tmp_path, ("café 🍰 I prefer synthetic jazz.",))
    job = create(service)
    model = FakeModel()

    async def extract(window: ModelWindow) -> ClaimBatch:
        return ClaimBatch(
            claims=(
                OtherClaim(
                    kind="episode",
                    statement="Jazz preference",
                    quote="I prefer synthetic jazz.",
                    quote_start=1000,
                    modality="PREFERENCE",
                    predicate=None,
                    value=None,
                    entity_name=None,
                    confidence=0.98,
                    importance=0.8,
                    verdict="supported",
                ),
            )
        )

    model.extract = extract
    assert asyncio.run(service.run(job.definition.job_id, model)).status == "complete"
    entry = ReviewService(service).export(job.definition.job_id).entries[0]
    span = entry.candidate.draft.content.evidence[0].evidence.span
    assert span.start_codepoint == 7 and span.start_byte == 11


def test_summary_text_is_verbatim_and_json_field_is_explicit(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    from ember_memory_importers.normalization.contracts import SourceImportError

    source = tmp_path / "exact.md"
    raw = "café 🍰\r\n\r\nI prefer synthetic jazz.\r\n".encode()
    source.write_bytes(raw)
    library = engine.create_library("exact-text")
    target = EngineImportTarget(engine.archive(library.store_id))
    report = import_summary(
        source, target=target, raw_vault=tmp_path / "raw", manifests=tmp_path / "manifests"
    )
    again = import_summary(
        source, target=target, raw_vault=tmp_path / "raw", manifests=tmp_path / "manifests"
    )
    assert report.records_written == 1 and again.records_written == 0
    assert next((tmp_path / "raw").glob("*.txt")).read_bytes() == raw
    payload = engine.maintenance_store(library.store_id)[1].read(
        lambda c: c.execute("SELECT payload_json FROM source_records").fetchone()[0]
    )
    assert json.loads(payload)["parts"][0]["text"] == raw.decode("utf-8")
    source_json = tmp_path / "memories.json"
    source_json.write_text(
        json.dumps({"memories": [{"id": "one", "content": "A synthetic note."}]}), encoding="utf-8"
    )
    with pytest.raises(SourceImportError):
        import_summary(
            source_json,
            target=target,
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "manifests",
            format="json",
        )
    assert (
        import_summary(
            source_json,
            target=target,
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "manifests",
            format="json",
            text_field="content",
        ).records_written
        == 1
    )


def test_reasoning_summary_is_preserved_raw_but_never_normalized(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    from ember_memory_importers.normalization.contracts import SourceImportError

    source = tmp_path / "reasoning.txt"
    source.write_text("<think>PRIVATE_REASONING_SENTINEL</think>visible", encoding="utf-8")
    library = engine.create_library("exclude-reasoning")
    with pytest.raises(SourceImportError, match="Reasoning-tagged"):
        import_summary(
            source,
            target=EngineImportTarget(engine.archive(library.store_id)),
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "manifests",
        )
    assert (
        engine.maintenance_store(library.store_id)[1].read(
            lambda c: c.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        )
        == 0
    )


def test_summary_cli_and_job_creation_need_no_model_or_personal_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ember_memory_cli.main import main

    source = tmp_path / "summary.txt"
    source.write_text("I like synthetic jazz.", encoding="utf-8")
    root = tmp_path / "cli-root"
    assert (
        main(
            [
                "summary",
                "import",
                str(source),
                "--root",
                str(root),
                "--source-namespace",
                "synthetic-cli",
            ]
        )
        == 0
    )
    library = json.loads(capsys.readouterr().out)["source_store_id"]
    assert (
        main(
            [
                "extract",
                "create",
                "--root",
                str(root),
                "--store",
                library,
                "--key",
                "cli",
                "--model",
                "synthetic",
                "--digest",
                "a" * 64,
            ]
        )
        == 0
    )
    job = json.loads(capsys.readouterr().out)
    assert job["total_windows"] == 1 and job["candidates"] == 0
    assert (
        main(
            [
                "extract",
                "status",
                "--root",
                str(root),
                "--store",
                library,
                "--job",
                job["definition"]["job_id"],
            ]
        )
        == 0
    )


def test_reevaluation_appends_changed_decisions_without_rewriting_accepted_beliefs(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, ("I like synthetic jazz.",))
    job = create(service)
    asyncio.run(service.run(job.definition.job_id, FakeModel()))
    reviewer = ReviewService(service, authority=MutationAuthority(mode="confirmed_user"))
    review = reviewer.export(job.definition.job_id)
    reviewer.apply(
        review.model_copy(
            update={"entries": (review.entries[0].model_copy(update={"decision": "approve"}),)}
        )
    )
    original = service.database.read(
        lambda c: c.execute("SELECT payload_json,payload_sha256 FROM approved_knowledge").fetchall()
    )
    assert reviewer.reevaluate(job.definition.job_id) == 0
    reviewer.apply(
        review.model_copy(
            update={"entries": (review.entries[0].model_copy(update={"decision": "reject"}),)}
        )
    )
    assert reviewer.reevaluate(job.definition.job_id) == 1
    assert reviewer.reevaluate(job.definition.job_id) == 0
    assert (
        service.database.read(
            lambda c: c.execute(
                "SELECT payload_json,payload_sha256 FROM approved_knowledge"
            ).fetchall()
        )
        == original
    )
    assert (
        service.database.read(
            lambda c: c.execute("SELECT COUNT(*) FROM activation_decisions").fetchone()[0]
        )
        == 2
    )


def test_unknown_predicates_cannot_be_approved_until_supported(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    from ember_memory_abi.errors import UnsupportedWriteError

    service, _ = summary(engine, tmp_path, ("I own a synthetic widget.",))
    job = create(service)
    model = FakeModel()

    async def extract(window: ModelWindow) -> ClaimBatch:
        return ClaimBatch(
            claims=(
                FactClaim(
                    kind="fact",
                    statement=window.text,
                    quote=window.text,
                    quote_start=None,
                    modality="FACTUAL_ASSERTION",
                    predicate="unregistered.widget",
                    value="synthetic",
                    entity_name=None,
                    confidence=0.98,
                    importance=0.8,
                    verdict="supported",
                ),
            )
        )

    model.extract = extract
    assert asyncio.run(service.run(job.definition.job_id, model)).status == "complete"
    reviewer = ReviewService(service, authority=MutationAuthority(mode="confirmed_user"))
    review = reviewer.export(job.definition.job_id)
    assert "unsupported_semantics" in review.entries[0].policy_decision.verdict.reasons
    with pytest.raises(UnsupportedWriteError):
        reviewer.apply(
            review.model_copy(
                update={"entries": (review.entries[0].model_copy(update={"decision": "approve"}),)}
            )
        )


def test_repeated_quotes_are_rejected_without_exact_disambiguation(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, ("I like jazz. I like jazz.",))
    job = create(service)
    model = FakeModel()

    async def extract(window: ModelWindow) -> ClaimBatch:
        return ClaimBatch(
            claims=(
                OtherClaim(
                    kind="episode",
                    statement="Jazz preference",
                    quote="I like jazz.",
                    quote_start=None,
                    modality="PREFERENCE",
                    predicate=None,
                    value=None,
                    entity_name=None,
                    confidence=0.98,
                    importance=0.8,
                    verdict="supported",
                ),
            )
        )

    model.extract = extract
    assert asyncio.run(service.run(job.definition.job_id, model)).status == "failed"
    assert service.status(job.definition.job_id).candidates == 0


def test_repeated_quotes_accept_only_the_exact_supplied_occurrence(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    source_text = "I like jazz. I like jazz."
    service, _ = summary(engine, tmp_path, (source_text,))
    job = create(service)
    model = FakeModel()

    async def extract(window: ModelWindow) -> ClaimBatch:
        return ClaimBatch(
            claims=(
                OtherClaim(
                    kind="episode",
                    statement="Jazz preference",
                    quote="I like jazz.",
                    quote_start=13,
                    modality="PREFERENCE",
                    predicate=None,
                    value=None,
                    entity_name=None,
                    confidence=0.98,
                    importance=0.8,
                    verdict="supported",
                ),
            )
        )

    model.extract = extract
    done = asyncio.run(service.run(job.definition.job_id, model))
    assert done.status == "complete" and done.candidates == 1
    evidence = ReviewService(service).export(job.definition.job_id).entries[0].quotes
    assert evidence == ("I like jazz.",)


def test_quote_start_is_required_in_the_structured_claim() -> None:
    with pytest.raises(ValueError):
        FactClaim.model_validate(
            {
                "kind": "episode",
                "statement": "A durable preference",
                "quote": "I prefer jazz.",
                "modality": "PREFERENCE",
                "confidence": 0.98,
                "importance": 0.8,
                "verdict": "supported",
            }
        )


def test_job_policy_hash_cannot_drift(engine: CanonicalEngine, tmp_path: Path) -> None:
    service, _ = summary(engine, tmp_path, ("A synthetic note.",))
    with pytest.raises(ProcessingError, match="processing_policy_changed"):
        service.create(
            JobRequest(
                idempotency_key="invalid-policy",
                owner_store_id=service.owner,
                specification=SPEC.model_copy(update={"policy_sha256": "b" * 64}),
            )
        )


def test_schema_three_upgrade_is_backed_up_and_failed_ddl_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ember_memory_engine.database as database_module
    from ember_memory_engine.errors import StorageError
    from ember_memory_engine.schema import PHASE_SIX_IMMUTABLE

    root = tmp_path / "migration"
    with CanonicalEngine(root) as engine:
        library = engine.create_library("synthetic-schema-three")
        path = engine.maintenance_store(library.store_id)[1].path
    tables = (*PHASE_SIX_IMMUTABLE, "processing_jobs", "processing_windows")
    with sqlite3.connect(path) as connection:
        for table in tables:
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_versions WHERE version=4")
        connection.execute("PRAGMA user_version=3")
    original = database_module.PHASE_SIX_SQL
    monkeypatch.setattr(database_module, "PHASE_SIX_SQL", original + "INVALID DDL;")
    with pytest.raises(StorageError):
        CanonicalEngine(root)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='processing_jobs'"
        ).fetchone()
    backup = path.with_suffix(".schema-3.backup.db")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    monkeypatch.setattr(database_module, "PHASE_SIX_SQL", original)
    with CanonicalEngine(root) as engine:
        assert (
            engine.maintenance_store(library.store_id)[1].read(
                lambda c: c.execute("PRAGMA user_version").fetchone()[0]
            )
            == 4
        )


def test_source_bundle_preserves_resumable_jobs_and_operator_rejection(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    from ember_memory_engine.bundles import BundleService, restore_bundles

    service, _ = summary(engine, tmp_path, ("I like synthetic jazz.", "I like synthetic folk."))
    job = create(service)
    assert (
        asyncio.run(service.run(job.definition.job_id, FakeModel(), max_windows=1)).status
        == "paused"
    )
    reviewer = ReviewService(service, authority=MutationAuthority(mode="confirmed_user"))
    review = reviewer.export(job.definition.job_id)
    reviewer.apply(
        review.model_copy(
            update={"entries": (review.entries[0].model_copy(update={"decision": "reject"}),)}
        )
    )
    root = engine.root
    owner = service.owner
    engine.close()
    bundle = tmp_path / "source-bundle"
    BundleService(root).export_source(owner, bundle)
    restored = tmp_path / "restored"
    restore_bundles((bundle,), restored)
    with CanonicalEngine(restored) as reopened:
        service = ProcessingService(reopened, owner)
        assert service.status(job.definition.job_id).completed_windows == 1
        entry = ReviewService(service).export(job.definition.job_id).entries[0]
        assert "operator_rejected_claim" in entry.policy_decision.verdict.reasons
        model = FakeModel()
        done = asyncio.run(service.run(job.definition.job_id, model))
        assert done.status == "complete" and done.completed_windows == 2 and model.calls == 1


def test_suppression_added_after_extraction_blocks_review_and_approval(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    from ember_memory_engine.source_access import source_keys

    service, _ = summary(engine, tmp_path, ("I like synthetic jazz.",))
    job = create(service)
    asyncio.run(service.run(job.definition.job_id, FakeModel()))
    reviewer = ReviewService(service, authority=MutationAuthority(mode="confirmed_user"))
    review = reviewer.export(job.definition.job_id)
    key = source_keys(review.entries[0].candidate.draft.content.evidence[0].evidence.source)[0]
    service.database.write(
        lambda c: c.execute("INSERT INTO suppression_targets VALUES('source',?)", (key,))
    )
    with pytest.raises(ProcessingError, match="source_suppressed"):
        reviewer.export(job.definition.job_id)
    with pytest.raises(ProcessingError, match="source_suppressed"):
        reviewer.apply(
            review.model_copy(
                update={"entries": (review.entries[0].model_copy(update={"decision": "approve"}),)}
            )
        )
    assert (
        service.database.read(
            lambda c: c.execute("SELECT COUNT(*) FROM approved_knowledge").fetchone()[0]
        )
        == 0
    )


def test_selective_bundle_keeps_selected_processing_specs_without_private_jobs(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    from ember_memory_engine.bundles import BundleService, restore_bundles

    service, _ = summary(engine, tmp_path, ("I like synthetic jazz.",))
    job = create(service)
    asyncio.run(service.run(job.definition.job_id, FakeModel()))
    reviewer = ReviewService(service, authority=MutationAuthority(mode="confirmed_user"))
    review = reviewer.export(job.definition.job_id)
    receipt = reviewer.apply(
        review.model_copy(
            update={"entries": (review.entries[0].model_copy(update={"decision": "approve"}),)}
        )
    )
    character = engine.create_character()
    seeded = LibraryService(engine, authority=MutationAuthority(mode="confirmed_user")).seed(
        character.store_id,
        SeedRequest(
            idempotency_key="selective-spec-seed",
            library_id=service.owner,
            selections=(KnowledgeSelection(knowledge_id=receipt.approved[0], revision=1),),
        ),
    )
    root = engine.root
    engine.close()
    bundle = tmp_path / "selected-processing-bundle"
    BundleService(root).export_character(
        character.store_id,
        bundle,
        mode="self_contained",
        memory_ids=(seeded.affected[0].memory_id,),
    )
    restored = tmp_path / "selected-processing-restored"
    restore_bundles((bundle,), restored)
    with CanonicalEngine(restored) as reopened:
        database = reopened.maintenance_store(character.store_id)[1]
        assert (
            database.read(
                lambda c: c.execute("SELECT COUNT(*) FROM processing_specs").fetchone()[0]
            )
            == 2
        )
        assert (
            database.read(lambda c: c.execute("SELECT COUNT(*) FROM processing_jobs").fetchone()[0])
            == 0
        )
        assert (
            database.read(
                lambda c: c.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0]
            )
            == 0
        )
        assert (
            reopened.character(character.store_id)
            .get_sources(seeded.affected[0].memory_id)[0]
            .quote
            == "I like synthetic jazz."
        )


def test_job_source_and_specification_headers_cannot_be_rewritten(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    from ember_memory_engine.errors import StorageError

    service, _ = summary(engine, tmp_path, ("A synthetic note.",))
    job = create(service)
    for table in ("processing_jobs", "processing_windows"):
        with pytest.raises(StorageError):
            service.database.write(
                lambda c, table=table: c.execute(f"UPDATE {table} SET payload_json='{{}}'")
            )
    assert service.status(job.definition.job_id).definition == job.definition
    assert asyncio.run(service.run(job.definition.job_id, FakeModel())).status == "complete"


class QuoteFailureModel(FakeModel):
    """Exercise pre-commit quote failures, independently for each source window."""

    def __init__(
        self,
        bad_text: str,
        *,
        failures: int | None = None,
        missing_quote: bool = True,
    ) -> None:
        super().__init__()
        self.bad_text = bad_text
        self.failures = failures
        self.missing_quote = missing_quote
        self.bad_calls = 0

    async def extract(self, window: ModelWindow) -> ClaimBatch:
        batch = await super().extract(window)
        if window.text != self.bad_text:
            return batch
        self.bad_calls += 1
        if self.failures is not None and self.bad_calls > self.failures:
            return batch
        claim = batch.claims[0].model_copy(
            update={
                "quote": "invented evidence",
                "quote_start": None if self.missing_quote else 0,
            }
        )
        return ClaimBatch(claims=(claim,))


@pytest.mark.parametrize("missing_quote", [True, False])
def test_bounded_quote_retry_commits_only_valid_output(
    engine: CanonicalEngine, tmp_path: Path, missing_quote: bool
) -> None:
    first = "I like synthetic jazz."
    service, _ = summary(engine, tmp_path, (first, "I like synthetic folk."))
    job = create(service)
    model = QuoteFailureModel(first, failures=1, missing_quote=missing_quote)
    done = asyncio.run(service.run(job.definition.job_id, model, window_attempts=2))
    assert done.status == "complete"
    assert done.completed_windows == 2 and done.failed_windows == 0
    assert done.candidates == 2 and model.calls == 3
    assert (
        service.database.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM processing_failures"
            ).fetchone()[0]
        )
        == 1
    )
    assert (
        service.database.read(
            lambda connection: connection.execute(
                "SELECT attempts FROM processing_windows WHERE job_id=? AND ordinal=0",
                (str(job.definition.job_id),),
            ).fetchone()[0]
        )
        == 2
    )
    quotes = {
        entry.quotes for entry in ReviewService(service).export(job.definition.job_id).entries
    }
    assert quotes == {(first,), ("I like synthetic folk.",)}


def test_exhausted_quote_failure_continues_and_remains_recoverable_after_restart(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    first = "I like synthetic jazz."
    service, _ = summary(engine, tmp_path, (first, "I like synthetic folk."))
    job = create(service)
    model = QuoteFailureModel(first)
    done = asyncio.run(
        service.run(job.definition.job_id, model, window_attempts=3, continue_on_error=True)
    )
    assert done.status == "failed"
    assert done.completed_windows == 1 and done.failed_windows == 1
    assert done.candidates == 1 and model.calls == 4
    assert done.failures[0].ordinal == 0 and done.failures[0].attempts == 3
    root, owner = engine.root, service.owner
    engine.close()
    with CanonicalEngine(root) as reopened:
        resumed = ProcessingService(reopened, owner)
        recovered_model = FakeModel()
        recovered = asyncio.run(
            resumed.run(job.definition.job_id, recovered_model, retry_failed=True)
        )
        assert recovered.status == "complete" and recovered.failed_windows == 0
        assert recovered.completed_windows == 2 and recovered.candidates == 2
        assert recovered_model.calls == 1
        assert (
            resumed.database.read(
                lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM processing_failures"
                ).fetchone()[0]
            )
            == 3
        )
        assert (
            resumed.database.read(
                lambda connection: connection.execute(
                    "SELECT attempts FROM processing_windows WHERE job_id=? AND ordinal=0",
                    (str(job.definition.job_id),),
                ).fetchone()[0]
            )
            == 4
        )


def test_window_limit_bounds_failed_windows_and_resume_skips_unresolved_failure(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    first = "I like synthetic jazz."
    service, _ = summary(engine, tmp_path, (first, "I like synthetic folk."))
    job = create(service)
    model = QuoteFailureModel(first)
    limited = asyncio.run(
        service.run(
            job.definition.job_id,
            model,
            max_windows=1,
            window_attempts=2,
            continue_on_error=True,
        )
    )
    assert limited.status == "failed" and limited.completed_windows == 0
    assert limited.failed_windows == 1 and limited.candidates == 0 and model.calls == 2
    later_model = FakeModel()
    later = asyncio.run(service.run(job.definition.job_id, later_model))
    assert later.completed_windows == 1 and later.failed_windows == 1
    assert later.status == "failed" and later.candidates == 1 and later_model.calls == 1


def test_retry_controls_do_not_continue_infrastructure_failures(
    engine: CanonicalEngine, tmp_path: Path
) -> None:
    service, _ = summary(engine, tmp_path, ("I like synthetic jazz.", "I like synthetic folk."))
    job = create(service)
    model = FakeModel(fail_at=1)
    failed = asyncio.run(
        service.run(job.definition.job_id, model, window_attempts=3, continue_on_error=True)
    )
    assert failed.status == "failed"
    assert failed.completed_windows == 0 and failed.failed_windows == 1
    assert failed.candidates == 0 and model.calls == 1
    assert failed.failures[0].code == "synthetic_model_failure"


@pytest.mark.parametrize("window_attempts", [0, 4])
def test_invalid_window_attempt_limit_leaves_job_unchanged(
    engine: CanonicalEngine, tmp_path: Path, window_attempts: int
) -> None:
    service, _ = summary(engine, tmp_path, ("I like synthetic jazz.",))
    job = create(service)
    model = FakeModel()
    with pytest.raises(ProcessingError, match="invalid_window_attempts"):
        asyncio.run(service.run(job.definition.job_id, model, window_attempts=window_attempts))
    assert service.status(job.definition.job_id) == job and model.calls == 0


class WindowErrorModel(FakeModel):
    def __init__(self, bad_texts: set[str], code: str) -> None:
        super().__init__()
        self.bad_texts = bad_texts
        self.code = code

    async def extract(self, window: ModelWindow) -> ClaimBatch:
        batch = await super().extract(window)
        if window.text in self.bad_texts:
            raise ProcessingError(self.code)
        return batch


@pytest.mark.parametrize(
    "code",
    [
        "model_step_invalid",
        "gateway_structured_output_invalid",
        "fact_value_missing",
        "entity_mention_unverified",
        "extraction_output_too_large",
        "unexpected_extraction_tool_output",
    ],
)
def test_continuation_has_no_failed_window_count_limit(
    engine: CanonicalEngine, tmp_path: Path, code: str
) -> None:
    bad_texts = tuple(f"Bad synthetic note {number}." for number in range(5))
    service, _ = summary(engine, tmp_path, (*bad_texts, "I like synthetic jazz."))
    job = create(service)
    model = WindowErrorModel(set(bad_texts), code)
    result = asyncio.run(
        service.run(job.definition.job_id, model, window_attempts=3, continue_on_error=True)
    )
    assert result.status == "failed" and result.run_outcome == "finished_with_failures"
    assert result.completed_windows == 1 and result.failed_windows == 5
    assert result.candidates == 1 and model.calls == 16
    assert all(failure.attempts == 3 and failure.code == code for failure in result.failures)
    assert (
        service.database.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM processing_windows WHERE job_id=? AND status='pending'",
                (str(job.definition.job_id),),
            ).fetchone()[0]
        )
        == 0
    )


@pytest.mark.parametrize("code", ["source_suppressed", "unsupported_source_window"])
def test_continuation_skips_unusable_source_without_retry_or_inference(
    engine: CanonicalEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    first = "A synthetic unavailable source."
    service, _ = summary(engine, tmp_path, (first, "I like synthetic jazz."))
    job = create(service)
    original = service._input

    def source_input(definition: JobDefinition, reference: WindowRef) -> ModelWindow:
        window = original(definition, reference)
        if window.text == first:
            raise ProcessingError(code)
        return window

    monkeypatch.setattr(service, "_input", source_input)
    model = FakeModel()
    result = asyncio.run(
        service.run(job.definition.job_id, model, window_attempts=3, continue_on_error=True)
    )
    assert result.run_outcome == "finished_with_failures" and result.status == "failed"
    assert result.completed_windows == 1 and result.failed_windows == 1
    assert result.failures[0].attempts == 1 and result.failures[0].code == code
    assert result.candidates == 1 and model.calls == 1


@pytest.mark.parametrize(
    ("continue_on_error", "code", "expected_exit", "expected_calls"),
    [
        (False, "model_step_invalid", 2, 3),
        (True, "model_step_invalid", 0, 7),
        (True, "gateway_model_pin_mismatch", 2, 1),
    ],
)
def test_cli_continuation_exit_reports_gaps_without_hiding_fatal_errors(
    engine: CanonicalEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    continue_on_error: bool,
    code: str,
    expected_exit: int,
    expected_calls: int,
) -> None:
    from ember_memory_cli.processing import main
    from ember_memory_engine.processing_gateway import GatewayExtractionModel

    bad_texts = {"Bad synthetic jazz.", "Bad synthetic folk."}
    service, _ = summary(
        engine, tmp_path, ("Bad synthetic jazz.", "Bad synthetic folk.", "I like synthetic music.")
    )
    job = create(service)
    root, owner = engine.root, service.owner
    engine.close()
    token = tmp_path / "synthetic.token"
    token.write_text("a" * 40, encoding="utf-8")
    model = WindowErrorModel(bad_texts, code)

    async def extract(self: GatewayExtractionModel, window: ModelWindow) -> ClaimBatch:
        return await model.extract(window)

    monkeypatch.setattr(GatewayExtractionModel, "extract", extract)
    arguments = [
        "extract",
        "run",
        "--store",
        str(owner),
        "--job",
        str(job.definition.job_id),
        "--root",
        str(root),
        "--token-file",
        str(token),
        "--window-attempts",
        "3",
    ]
    if continue_on_error:
        arguments.append("--continue-on-error")
    assert main(arguments) == expected_exit
    assert model.calls == expected_calls
    result = JobStatus.model_validate_json(capsys.readouterr().out.splitlines()[-1])
    with CanonicalEngine(root) as reopened:
        persisted = ProcessingService(reopened, owner).status(job.definition.job_id)
        assert persisted.run_outcome is None and persisted.status == "failed"
        assert result.completed_windows == persisted.completed_windows
        assert result.failed_windows == persisted.failed_windows
        assert result.candidates == persisted.candidates
    if expected_exit == 0:
        assert result.run_outcome == "finished_with_failures"
        assert result.completed_windows == 1 and result.failed_windows == 2
        assert result.candidates == 1
    elif continue_on_error:
        assert result.run_outcome == "stopped_on_error" and result.completed_windows == 0


def test_summary_cli_busy_root_keeps_stdout_free_of_error_text(
    engine: CanonicalEngine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ember_memory_cli.processing import main

    source = tmp_path / "synthetic-summary.json"
    source.write_text('{"memories":["A synthetic personal preference."]}', encoding="utf-8")
    before = engine.stores()
    assert (
        main(
            [
                "summary",
                "import",
                str(source),
                "--format",
                "json",
                "--source-namespace",
                "synthetic-summary",
                "--root",
                str(engine.root),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.out == "" and "owner" in output.err
    assert "personal preference" not in output.err
    assert engine.stores() == before


@pytest.mark.parametrize("format", ["text", "json"])
def test_summary_cli_invalid_input_reports_safe_error_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], format: str
) -> None:
    from ember_memory_cli.processing import main

    source = tmp_path / "synthetic-invalid-summary"
    source.write_bytes(b"\xff")
    assert (
        main(
            [
                "summary",
                "import",
                str(source),
                "--format",
                format,
                "--source-namespace",
                "synthetic-invalid-summary",
                "--root",
                str(tmp_path / "root"),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.out == "" and output.err
    assert str(source) not in output.err


def test_existing_review_file_reports_clear_error_and_preserves_operator_decisions(
    engine: CanonicalEngine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ember_memory_cli.processing import main

    service, _ = summary(engine, tmp_path, ("I like synthetic jazz.",))
    job = create(service)
    asyncio.run(service.run(job.definition.job_id, FakeModel()))
    exported = ReviewService(service).export(job.definition.job_id)
    reviewed = exported.model_copy(
        update={"entries": (exported.entries[0].model_copy(update={"decision": "approve"}),)}
    )
    path = tmp_path / "existing-review.json"
    original = reviewed.model_dump_json(indent=2).encode("utf-8")
    path.write_bytes(original)
    root, owner = engine.root, service.owner
    engine.close()
    assert (
        main(
            [
                "extract",
                "review",
                "--store",
                str(owner),
                "--job",
                str(job.definition.job_id),
                "--output",
                str(path),
                "--root",
                str(root),
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.out == "" and "Output already exists" in output.err
    assert "--output" in output.err
    assert path.read_bytes() == original
