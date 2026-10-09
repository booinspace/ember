"""New event extraction retains exact date evidence without changing legacy jobs."""

import asyncio
from pathlib import Path

import pytest

from ember_memory_engine import CanonicalEngine
from ember_memory_engine.event_dates import resolve_date
from ember_memory_engine.processing import ProcessingService
from ember_memory_engine.processing_contracts import (
    DatedClaimBatch,
    DatedOtherClaim,
    DateSupport,
    JobRequest,
    ModelWindow,
    temporal_specification,
)
from ember_memory_engine.processing_review import ReviewService

from .test_phase_seven import append_native

SPEC = temporal_specification("synthetic", "a" * 64)


class EventModel:
    specification = SPEC

    async def extract(self, window: ModelWindow) -> DatedClaimBatch:
        return DatedClaimBatch(
            claims=(
                DatedOtherClaim(
                    kind="episode",
                    statement="A synthetic trip to Berlin",
                    quote=window.text,
                    quote_start=None,
                    modality="FACTUAL_ASSERTION",
                    predicate=None,
                    value=None,
                    entity_name=None,
                    confidence=0.95,
                    importance=0.7,
                    verdict="supported",
                    date=DateSupport(
                        expression="last December",
                        quote=window.text,
                        status="completed",
                    ),
                ),
            )
        )


def test_july_observation_resolves_supported_previous_december(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        append_native(
            engine,
            character,
            "I took a synthetic trip to Berlin last December.",
            observed="2026-07-15T12:00:00.000000Z",
        )
        processing = ProcessingService(engine, character)
        job = processing.create(
            JobRequest(
                owner_store_id=character,
                idempotency_key="dated",
                specification=SPEC,
                source_kind="archive",
            )
        )
        done = asyncio.run(processing.run(job.definition.job_id, EventModel()))
        assert done.status == "complete"
        entry = ReviewService(processing).export(job.definition.job_id).entries[0]
        temporal = entry.candidate.draft.temporal
        assert temporal.valid_from == "2025-12-01T00:00:00.000000Z"
        assert temporal.valid_until == "2026-01-01T00:00:00.000000Z"
        assert temporal.observed_at == "2026-07-15T12:00:00.000000Z"
        assert temporal.precision == "month" and temporal.basis == "source_relative_calendar"
        assert len(entry.candidate.draft.content.evidence) == 2
        assert all("last December" in quote for quote in entry.quotes)


@pytest.mark.parametrize("observed", (None, "2026-07-15T12:00:00.000000Z"))
def test_unknown_date_does_not_acquire_import_time(observed: str | None) -> None:
    support = DateSupport(
        expression="back then", quote="It happened back then.", status="completed"
    )
    resolved = resolve_date(support, observed, "Europe/London")
    assert resolved.valid_from is None and resolved.valid_until is None
    assert resolved.basis == "event_date_unresolved"


def test_hypothetical_cannot_be_dated_completed_episode(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        append_native(
            engine,
            character,
            "If I took a trip last December, it would be nice.",
            observed="2026-07-15T12:00:00.000000Z",
        )
        processing = ProcessingService(engine, character)
        job = processing.create(
            JobRequest(
                owner_store_id=character,
                idempotency_key="hypothetical",
                specification=SPEC,
                source_kind="archive",
            )
        )
        done = asyncio.run(processing.run(job.definition.job_id, EventModel()))
        assert done.status == "failed" and done.candidates == 0
        assert done.failures[0].code == "unsupported_completed_event"
