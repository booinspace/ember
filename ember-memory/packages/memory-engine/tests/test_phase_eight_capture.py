"""Native capture uses exact delivered sources, durable jobs and idempotent acceptance."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.capture import CaptureService, CaptureSettings
from ember_memory_engine.processing_contracts import native_specification

from .test_phase_seven import AUTHORITY, NativeModel, append_native

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
        ClaimBatch,
        DatedClaimBatch,
        DatedFactClaim,
        FactClaim,
        ModelWindow,
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
    from ember_memory_engine.consolidation import DATED_POLICY, NativeAcceptanceReceipt
    from ember_memory_engine.processing_contracts import (
        DatedClaimBatch,
        DatedOtherClaim,
        DateSupport,
        ExtractionModel,
        ExtractionSpec,
        JobRequest,
        ModelWindow,
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
    from ember_memory_engine.processing_contracts import ClaimBatch, ModelWindow, ProcessingError

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
