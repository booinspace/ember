"""Native capture uses exact delivered sources, durable jobs and idempotent acceptance."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Literal

import pytest

from ember_memory_abi.errors import RevisionConflictError
from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_engine import CanonicalEngine, SourceGrant
from ember_memory_engine.capture import CaptureCheckpoint, CaptureService, CaptureSettings
from ember_memory_engine.consolidation import (
    NativeAcceptanceReceipt,
    NativeAcceptanceRequest,
    NativeAcceptanceService,
)
from ember_memory_engine.processing_contracts import (
    ClaimBatch,
    ModelWindow,
    ProcessingDeferred,
    native_specification,
)
from ember_memory_engine.processing_review import ReviewService

from .test_phase_seven import AUTHORITY, NativeModel, append_native
from .test_phase_six_processing import FakeModel, create, summary

SPEC = native_specification("synthetic", "a" * 64, workload="P3")


@pytest.mark.parametrize("dated", [False, True])
@pytest.mark.parametrize(
    "source",
    [
        "Imagine Milo is my shark plushie.",
        "Pretend that Milo is my shark plushie.",
        "Suppose Milo is my shark plushie.",
        "Assume Milo is my shark plushie.",
        "Ember told me: Milo is my shark plushie.",
        "The assistant wrote: Milo is my shark plushie.",
        "Use this example: Milo is my shark plushie.",
        "If Milo is my shark plushie, what should I call the other toy?",
        "Milo is my shark plushie. That is a fictional scenario.",
        "Maybe Milo is my shark plushie.",
        "Milo is my shark plushie. That is not true; it is just a roleplay.",
    ],
)
def test_capture_cannot_strip_source_qualifiers(tmp_path: Path, dated: bool, source: str) -> None:
    from ember_memory_engine.processing_contracts import (
        DatedClaimBatch,
        DatedFactClaim,
        FactClaim,
        native_temporal_specification,
    )

    quote = "Milo is my shark plushie"

    class SelectiveModel:
        specification = native_temporal_specification("synthetic", "a" * 64) if dated else SPEC

        async def extract(self, window: ModelWindow) -> ClaimBatch | DatedClaimBatch:
            assert window.text == source
            fields = {
                "kind": "fact",
                "statement": quote,
                "quote": quote,
                "quote_start": None,
                "predicate": "personal.owns_object",
                "value": quote,
                "entity_name": None,
                "modality": "FACTUAL_ASSERTION",
                "confidence": 0.99,
                "importance": 0.8,
                "verdict": "supported",
            }
            if dated:
                return DatedClaimBatch(
                    claims=(DatedFactClaim.model_validate({**fields, "date": None}),)
                )
            return ClaimBatch(claims=(FactClaim.model_validate(fields),))

    root = tmp_path / "data"
    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        character = engine.create_character().store_id
        append_native(engine, character, source)
        capture = CaptureService(engine, character)
        assert not asyncio.run(capture.drain(SelectiveModel(), worker))
        assert capture.summary().deferred == 1
        assert not engine.character(character).search(MemoryQuery()).results
    with CanonicalEngine(root) as restarted:
        capture = CaptureService(restarted, character)
        assert capture.summary().deferred == 1
        assert not restarted.character(character).search(MemoryQuery()).results


class CaptureModel(NativeModel):
    specification = SPEC


class StagedModel:
    specification = SPEC

    async def extract(self, window: ModelWindow) -> ClaimBatch:
        raise AssertionError("A staged window must not be extracted again.")


def test_foreground_wins_acceptance_lock_without_losing_staged_window(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        service = CaptureService(engine, character)

        async def stage() -> tuple[CaptureCheckpoint, ...]:
            waiting = asyncio.Event()
            allowed = True
            loop = asyncio.get_running_loop()

            def should_continue() -> bool:
                assert asyncio.get_running_loop() is loop
                return allowed

            class TrackingLock(asyncio.Lock):
                async def acquire(self) -> Literal[True]:
                    if self.locked():
                        waiting.set()
                    return await super().acquire()

            lock = TrackingLock()
            async with lock:
                task = asyncio.create_task(
                    service.drain(
                        CaptureModel("user.preferred_name", "Rowan"),
                        worker,
                        should_continue=should_continue,
                        acceptance_lock=lock,
                    )
                )
                await asyncio.wait_for(waiting.wait(), 5)
                allowed = False
            return await task

        (paused,) = asyncio.run(stage())
        assert paused.status == "paused" and paused.job_id is not None
        job = service.processing.status(paused.job_id)
        assert job.status == "complete" and job.completed_windows == job.candidates == 1
        assert service.summary().accepted == 0

    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        service = CaptureService(engine, character)
        assert service.pending() == (paused,)
        assert not asyncio.run(service.drain(StagedModel(), worker))
        assert service.summary().accepted == 1
        (result,) = engine.character(character).search(MemoryQuery()).results
        (assertion,) = result.memory.content.evidence
        assert engine.validate(assertion.evidence) == "Please call me Rowan."


@pytest.mark.parametrize("cancel", [False, True])
def test_capture_yields_between_acceptances_and_preserves_first_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    class TwoClaims(CaptureModel):
        async def extract(self, window: ModelWindow) -> ClaimBatch:
            name = await super().extract(window)
            timezone = await CaptureModel("user.timezone", "Europe/London").extract(window)
            return ClaimBatch(claims=(*name.claims, *timezone.claims))

    def receipts(service: CaptureService) -> tuple[NativeAcceptanceReceipt, ...]:
        return service.database.read(
            lambda connection: tuple(
                NativeAcceptanceReceipt.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM native_acceptance ORDER BY rowid"
                )
            )
        )

    root = tmp_path / "data"
    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan. My timezone is Europe/London.")
        service = CaptureService(engine, character)

        async def interrupt() -> None:
            accepting, release = asyncio.Event(), Event()
            lock = asyncio.Lock()
            loop = asyncio.get_running_loop()
            allowed = True
            accept = NativeAcceptanceService.accept

            def should_continue() -> bool:
                assert asyncio.get_running_loop() is loop
                return allowed

            def block(
                writer: NativeAcceptanceService, request: NativeAcceptanceRequest
            ) -> NativeAcceptanceReceipt:
                loop.call_soon_threadsafe(accepting.set)
                assert release.wait(5)
                return accept(writer, request)

            with monkeypatch.context() as patch:
                patch.setattr(NativeAcceptanceService, "accept", block)
                task = asyncio.create_task(
                    service.drain(
                        TwoClaims("user.preferred_name", "Rowan"),
                        worker,
                        should_continue=should_continue,
                        acceptance_lock=lock,
                    )
                )
                try:
                    await asyncio.wait_for(accepting.wait(), 5)
                    allowed = False
                    if cancel:
                        for _ in range(2):
                            task.cancel()
                            await asyncio.sleep(0)
                            assert not task.done() and lock.locked()
                finally:
                    release.set()
                if cancel:
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    await task

        asyncio.run(interrupt())
        (paused,) = service.pending()
        assert paused.status == "paused" and paused.job_id is not None
        assert service.processing.status(paused.job_id).candidates == 2
        (first,) = receipts(service)
        assert first.outcome == "accepted"

    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        service = CaptureService(engine, character)
        assert not asyncio.run(service.drain(StagedModel(), worker))
        kept, second = receipts(service)
        assert kept == first and second.outcome == "accepted"
        assert len(engine.character(character).search(MemoryQuery()).results) == 2


def test_capture_model_deferral_pauses_job_and_checkpoint_across_restart(tmp_path: Path) -> None:
    class DeferredModel(CaptureModel):
        async def extract(self, window: ModelWindow) -> ClaimBatch:
            raise ProcessingDeferred()

    root = tmp_path / "data"
    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        service = CaptureService(engine, character)
        with pytest.raises(ProcessingDeferred, match="idle_capture_deferred"):
            asyncio.run(service.drain(DeferredModel("user.preferred_name", "Rowan"), worker))
        (checkpoint,) = service.pending()
        assert checkpoint.status == "paused" and checkpoint.error_code is None
        assert checkpoint.job_id is not None

    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        service = CaptureService(engine, character)
        assert service.pending() == (checkpoint,)
        job = service.processing.status(checkpoint.job_id)
        assert job.status == "paused" and job.completed_windows == job.failed_windows == 0
        assert not job.failures
        assert service.database.read(
            lambda connection: tuple(
                connection.execute("SELECT status,attempts FROM processing_windows").fetchone()
            )
        ) == ("pending", 0)
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        assert service.processing.status(checkpoint.job_id).status == "complete"
        assert service.summary().accepted == 1


def test_native_capture_leaves_granted_import_candidate_pending_review(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine, ThreadPoolExecutor(max_workers=1) as worker:
        character = engine.create_character().store_id
        imported, _ = summary(engine, tmp_path, ("Please call me Quinn.",))
        job = create(imported)
        done = asyncio.run(imported.run(job.definition.job_id, FakeModel()))
        assert done.status == "complete" and done.candidates == 1
        reviewer = ReviewService(imported)
        review = reviewer.export(job.definition.job_id, include_decisions=True)
        (entry,) = review.entries
        assert entry.quotes == ("Please call me Quinn.",) and entry.decision == "pending"
        engine.grant_source(
            character,
            SourceGrant(
                references=(entry.candidate.draft.content.evidence[0].evidence.source,),
                capabilities=frozenset({"evidence_read"}),
            ),
        )
        append_native(engine, character, "Please call me Rowan.")
        capture = CaptureService(engine, character)
        assert not asyncio.run(capture.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        assert capture.summary().accepted == 1
        (result,) = engine.character(character).search(MemoryQuery()).results
        assert result.memory.origin is None
        assert engine.character(character).get_sources(result.memory.memory_id)[0].quote == (
            "Please call me Rowan."
        )
        (after,) = reviewer.export(job.definition.job_id, include_decisions=True).entries
        assert after.candidate == entry.candidate and after.quotes == entry.quotes
        assert after.decision == "pending"
        assert after.policy_decision.verdict.verdict == entry.policy_decision.verdict.verdict
        assert (
            imported.database.read(
                lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM approved_knowledge"
                ).fetchone()[0]
            )
            == 0
        )


def test_capture_resume_accepts_once_and_does_not_rescan_history(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        service = CaptureService(engine, character)
        assert len(service.pending()) == 1
    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        service = CaptureService(engine, character)
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        assert len(engine.character(character).search(MemoryQuery()).results) == 1
        append_native(engine, character, "Please call me Rowan.")
        assert len(service.pending()) == 1
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        definitions = service.processing.definitions()
        assert len(definitions) == 2
        assert all(service.processing.status(job.job_id).total_windows == 1 for job in definitions)
        assert len(engine.character(character).search(MemoryQuery()).results) == 1


def test_capture_disabled_is_independent_of_recall_and_survives_restart(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        service = CaptureService(engine, character)
        service.configure(CaptureSettings(enabled=False), AUTHORITY)
        append_native(engine, character, "Please call me Rowan.")
        assert service.pending()[0].status == "paused"
        assert engine.character(character).recall_selection().local_enabled
    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        service = CaptureService(engine, character)
        assert not service.settings().enabled
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        service.configure(CaptureSettings(enabled=True, revision=1), AUTHORITY)
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        assert len(engine.character(character).search(MemoryQuery()).results) == 1


def test_new_native_capture_has_supported_event_time_without_repinning_legacy_queue(
    tmp_path: Path,
) -> None:
    from ember_memory_engine.consolidation import DATED_POLICY
    from ember_memory_engine.processing_contracts import (
        DatedClaimBatch,
        DatedOtherClaim,
        DateSupport,
        ExtractionModel,
        ExtractionSpec,
        JobRequest,
        native_temporal_specification,
    )

    dated_spec = native_temporal_specification("synthetic", "a" * 64)

    class DatedModel:
        specification = dated_spec

        async def extract(self, window: ModelWindow) -> DatedClaimBatch:
            return DatedClaimBatch(
                claims=(
                    DatedOtherClaim(
                        kind="episode",
                        statement=window.text,
                        quote=window.text,
                        quote_start=None,
                        modality="FACTUAL_ASSERTION",
                        confidence=0.99,
                        importance=0.8,
                        verdict="supported",
                        predicate=None,
                        value=None,
                        entity_name=None,
                        date=DateSupport(
                            expression="last December", quote=window.text, status="completed"
                        ),
                    ),
                )
            )

    with CanonicalEngine(tmp_path / "data") as engine, ThreadPoolExecutor(max_workers=1) as worker:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        service = CaptureService(engine, character)
        checkpoint = service.pending()[0]
        legacy = service.processing.create(
            JobRequest(
                idempotency_key="native-capture:" + checkpoint.delivery_key,
                owner_store_id=character,
                specification=SPEC,
                source_kind="native",
                source_references=checkpoint.sources,
            )
        )
        service._mark(checkpoint.delivery_key, "paused", legacy.definition.job_id)
        original = legacy.definition.model_dump_json()
        append_native(
            engine,
            character,
            "I went to Berlin last December.",
            observed="2026-07-15T12:00:00.000000Z",
        )

        def factory(spec: ExtractionSpec) -> ExtractionModel:
            return CaptureModel("user.preferred_name", "Rowan") if spec == SPEC else DatedModel()

        assert not asyncio.run(service.drain(DatedModel(), worker, model_for=factory))
        assert service.processing.definition(legacy.definition.job_id).model_dump_json() == original
        memories = engine.character(character).search(MemoryQuery()).results
        assert len(memories) == 2
        event = next(
            result.memory for result in memories if result.memory.content.kind == "episode"
        )
        assert event.temporal.valid_from == "2025-12-01T00:00:00.000000Z"
        assert event.temporal.valid_until == "2026-01-01T00:00:00.000000Z"
        assert event.temporal.observed_at == "2026-07-15T12:00:00.000000Z"
        receipts = service.database.read(
            lambda c: tuple(
                NativeAcceptanceReceipt.model_validate_json(row[0])
                for row in c.execute("SELECT payload_json FROM native_acceptance")
            )
        )
        assert any(
            receipt.policy == DATED_POLICY and receipt.outcome == "accepted" for receipt in receipts
        )
        assert not asyncio.run(service.drain(DatedModel(), worker, model_for=factory))
        assert len(engine.character(character).search(MemoryQuery()).results) == 2


def test_failed_checkpoint_is_visible_without_blocking_later_capture(tmp_path: Path) -> None:
    from ember_memory_engine.processing_contracts import ProcessingError

    class FailingModel(CaptureModel):
        async def extract(self, window: ModelWindow) -> ClaimBatch:
            if "SYNTHETIC_FAILURE" in window.text:
                raise ProcessingError("synthetic_failure")
            return await super().extract(window)

    with CanonicalEngine(tmp_path / "data") as engine, ThreadPoolExecutor(max_workers=1) as worker:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan. SYNTHETIC_FAILURE")
        append_native(engine, character, "Please call me Rowan.")
        service = CaptureService(engine, character)
        remaining = asyncio.run(service.drain(FailingModel("user.preferred_name", "Rowan"), worker))
        assert remaining == ()  # Automatic background continuation has no runnable work left.
        failed = service.pending()[0]
        assert failed.status == "failed" and failed.error_code == "synthetic_failure"
        original_job = failed.job_id
        assert len(engine.character(character).search(MemoryQuery()).results) == 1
        service.retry(failed.delivery_key, AUTHORITY)
        assert service.pending()[0].job_id == original_job
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        assert not service.pending() and len(service.processing.definitions()) == 2


@pytest.mark.parametrize("failed_stage", ["pinned_job", "acceptance"])
def test_unrecoverable_checkpoint_does_not_block_next_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_stage: str
) -> None:
    with CanonicalEngine(tmp_path / "data") as engine, ThreadPoolExecutor(max_workers=1) as worker:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        append_native(engine, character, "Please call me Rowan.")
        capture = CaptureService(engine, character)
        first, second = capture.pending()
        if failed_stage == "pinned_job":
            from uuid import uuid4

            capture._mark(first.delivery_key, "processing", uuid4())
            code = "job_not_found"
        else:
            real_accept = NativeAcceptanceService.accept
            attempts = 0

            def accept(
                service: NativeAcceptanceService, request: NativeAcceptanceRequest
            ) -> NativeAcceptanceReceipt:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RevisionConflictError()
                return real_accept(service, request)

            monkeypatch.setattr(NativeAcceptanceService, "accept", accept)
            code = "revision_conflict"

        assert not asyncio.run(capture.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        (failed,) = capture.pending()
        assert (failed.delivery_key, failed.status, failed.error_code) == (
            first.delivery_key,
            "failed",
            code,
        )
        assert failed.job_id is not None and second.delivery_key != first.delivery_key
        assert capture.summary().accepted == capture.summary().complete == 1
        (found,) = engine.character(character).search(MemoryQuery()).results
        (source,) = engine.character(character).get_sources(found.memory.memory_id)
        assert source.quote == "Please call me Rowan."
