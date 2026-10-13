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


@pytest.mark.parametrize("kind", ("fact", "goal", "plan"))
def test_event_or_target_date_does_not_expire_a_current_state(kind: str) -> None:
    from ember_memory_engine.event_dates import interpretation_date

    support = DateSupport(
        expression="December 2025",
        quote="I bought this device in December 2025.",
        status="completed",
    )
    result = interpretation_date(support, kind, "2026-07-01T12:00:00.000000Z", "Europe/London")
    assert result.valid_from is None and result.valid_until is None


def test_explicit_state_start_has_an_open_end_instead_of_a_month_duration() -> None:
    from ember_memory_engine.event_dates import interpretation_date

    support = DateSupport(
        expression="December 2025",
        quote="I have used this device since December 2025.",
        status="current_state",
    )
    result = interpretation_date(support, "fact", "2026-07-01T12:00:00.000000Z", "Europe/London")
    assert result.valid_from == "2025-12-01T00:00:00.000000Z"
    assert result.valid_until is None and result.precision == "month"


@pytest.mark.parametrize("expression", ("29 March 2026", "March 29th, 2026"))
def test_exact_spoken_date_support_preserves_day_precision_and_dst(expression: str) -> None:
    support = DateSupport(
        expression=expression, quote="It happened on " + expression, status="completed"
    )
    result = resolve_date(support, None, "Europe/London")
    assert result.precision == "day" and result.basis == "explicit_calendar_date"
    assert result.valid_from == "2026-03-29T00:00:00.000000Z"
    assert result.valid_until == "2026-03-29T23:00:00.000000Z"


def test_invalid_explicit_event_day_stays_unknown_instead_of_using_the_month() -> None:
    support = DateSupport(
        expression="31 June 2026", quote="It happened on 31 June 2026", status="completed"
    )
    result = resolve_date(support, None, "Europe/London")
    assert result.precision == "unknown" and result.valid_from is None


@pytest.mark.parametrize(
    ("expression", "precision", "known"),
    (
        ("yesterday", "month", False),
        ("last January", "year", False),
        ("last month", "unknown", False),
        ("last year", "year", True),
        ("last December", "month", True),
        ("yesterday", "source_numeric", True),
        ("15 July 2026", "unknown", True),
    ),
)
def test_relative_dates_require_enough_source_clock_precision(
    expression: str, precision: str, known: bool
) -> None:
    support = DateSupport(
        expression=expression, quote="It happened " + expression, status="completed"
    )
    result = resolve_date(
        support, "2026-07-15T12:00:00.000000Z", "Europe/London", observed_precision=precision
    )
    assert (result.valid_from is not None) is known
    if not known:
        assert result.precision == "unknown" and result.valid_until is None
        assert result.basis == "source_clock_precision_insufficient"


def test_extraction_does_not_manufacture_a_relative_date_from_an_unknown_clock(
    tmp_path: Path,
) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        append_native(
            engine,
            character,
            "I visited Berlin last December.",
            observed="2026-07-15T12:00:00.000000Z",
            observed_precision="unknown",
        )
        processing = ProcessingService(engine, character)
        job = processing.create(
            JobRequest(
                owner_store_id=character,
                idempotency_key="coarse-clock",
                specification=SPEC,
                source_kind="archive",
            )
        )
        done = asyncio.run(processing.run(job.definition.job_id, EventModel()))
        assert done.status == "complete"
        candidate = ReviewService(processing).export(job.definition.job_id).entries[0].candidate
        assert candidate.draft.temporal.valid_from is None
        assert candidate.draft.temporal.basis == "source_clock_precision_insufficient"


def test_technical_identifier_cannot_become_a_supported_event_year() -> None:
    from ember_memory_engine.processing_contracts import ProcessingError

    with pytest.raises(ProcessingError, match="date_expression_not_in_quote"):
        resolve_date(
            DateSupport(expression="2000", quote="The RTX2000 failed.", status="completed"),
            None,
            "Europe/London",
        )
    port = resolve_date(
        DateSupport(expression="2000", quote="I changed port 2000.", status="completed"),
        None,
        "Europe/London",
    )
    assert port.valid_from is None and port.basis == "technical_identifier_not_event_date"
    calendar = resolve_date(
        DateSupport(expression="2000", quote="Port 2000 failed in 2000.", status="completed"),
        None,
        "Europe/London",
    )
    assert calendar.valid_from == "2000-01-01T00:00:00.000000Z"


def test_source_day_ordinal_matches_ordinary_spoken_query_semantics() -> None:
    support = DateSupport(
        expression="15th of July 2026",
        quote="It happened on 15th of July 2026.",
        status="completed",
    )
    resolved = resolve_date(support, None, "Europe/London")
    assert resolved.precision == "day"
    assert resolved.valid_from == "2026-07-14T23:00:00.000000Z"
