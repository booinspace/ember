"""Calendar recall uses local civil dates and never silently broadens ambiguous requests."""

from datetime import UTC, datetime

import pytest

from ember_agent.character.temporal import CalendarResolver

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "start", "end"),
    (
        ("memories from summer 2026", "2026-05-31T23:00:00.000000Z", "2026-08-31T23:00:00.000000Z"),
        ("memories from March 2026", "2026-03-01T00:00:00.000000Z", "2026-03-31T23:00:00.000000Z"),
        ("memories from 2024-02-29", "2024-02-29T00:00:00.000000Z", "2024-03-01T00:00:00.000000Z"),
        ("memories last year", "2025-01-01T00:00:00.000000Z", "2026-01-01T00:00:00.000000Z"),
        ("memories winter 2025", "2025-12-01T00:00:00.000000Z", "2026-03-01T00:00:00.000000Z"),
    ),
)
def test_calendar_bounds(text: str, start: str, end: str) -> None:
    result = CalendarResolver().resolve(text, NOW)
    assert result.status == "resolved"
    assert result.query.temporal.valid_from == start
    assert result.query.temporal.valid_until == end
    assert result.query.mode == "structured" and not result.query.text


def test_axes_topic_and_followups() -> None:
    parser = CalendarResolver()
    result = parser.resolve("NAS problems from summer 2026", NOW)
    assert result.query.text == "nas problems"
    assert result.query.mode == "hybrid"
    observed = parser.resolve("What did I tell you in summer 2026?", NOW)
    assert observed.query.temporal.observed_from == result.query.temporal.valid_from
    recorded = parser.resolve("What did you believe before June 2026?", NOW)
    assert recorded.query.temporal.recorded_before == "2026-05-31T23:00:00.000000Z"
    same = parser.resolve("during that same period", NOW, observed.scope)
    assert same.scope == observed.scope
    previous = parser.resolve("the year before", NOW, result.scope)
    assert previous.query.temporal.valid_from == "2025-05-31T23:00:00.000000Z"


@pytest.mark.parametrize(
    "text",
    ("memories from September", "summer 26", "2026-02-29", "2025 and 2026", "the year before"),
)
def test_ambiguity_requires_clarification(text: str) -> None:
    assert CalendarResolver().resolve(text, NOW).status == "clarification"


def test_user_beliefs_names_and_system_cutoffs_are_distinct() -> None:
    parser = CalendarResolver()
    assert parser.resolve("What did I believe in summer 2026?", NOW).scope.axis == "event"
    assert parser.resolve("Do you remember May?", NOW).status == "ordinary"
    assert parser.resolve("What did you believe in summer 2026?", NOW).reasons == (
        "recorded_cutoff_required",
    )
    earlier = parser.resolve("NAS problems summer 2026", NOW)
    changed = parser.resolve("What did I tell you during that same period?", NOW, earlier.scope)
    assert changed.scope.axis == "observed" and changed.scope.start == earlier.scope.start


@pytest.mark.parametrize(
    ("text", "end"),
    (
        ("NAS from June 2026 through August 2026", "2026-08-31T23:00:00.000000Z"),
        ("NAS from June 2026 until September 2026", "2026-08-31T23:00:00.000000Z"),
        ("NAS between June 2026 and August 2026", "2026-08-31T23:00:00.000000Z"),
    ),
)
def test_explicit_calendar_ranges(text: str, end: str) -> None:
    result = CalendarResolver().resolve(text, NOW)
    assert result.status == "resolved" and result.query.text == "nas"
    assert result.query.temporal.valid_from == "2026-05-31T23:00:00.000000Z"
    assert result.query.temporal.valid_until == end


@pytest.mark.parametrize(
    "question",
    [
        "memories summer 2026",
        "What did I tell you summer 2026?",
        "What did you believe before summer 2026?",
    ],
)
def test_temporal_deep_framing_fits_and_qualifies_subset(question: str) -> None:
    from ember_agent.character.prompt_budget import message_bytes, recall_frame

    result = CalendarResolver().resolve(question, NOW)
    for outcome in (None, "partial", "complete_within_budget"):
        frame = recall_frame(result.scope, outcome)
        assert frame is not None
        assert message_bytes(frame) <= 216
        assert "past subset" in frame.content


@pytest.mark.parametrize(
    ("question", "start", "end"),
    (
        ("NAS on 15 July 2026", "2026-07-14T23:00:00.000000Z", "2026-07-15T23:00:00.000000Z"),
        ("NAS on July 15th, 2026", "2026-07-14T23:00:00.000000Z", "2026-07-15T23:00:00.000000Z"),
        ("NAS on 29th February 2024", "2024-02-29T00:00:00.000000Z", "2024-03-01T00:00:00.000000Z"),
        ("NAS on 29 March 2026", "2026-03-29T00:00:00.000000Z", "2026-03-29T23:00:00.000000Z"),
        ("NAS on 25 October 2026", "2026-10-24T23:00:00.000000Z", "2026-10-26T00:00:00.000000Z"),
    ),
)
def test_spoken_explicit_dates_do_not_degrade_to_a_month(
    question: str, start: str, end: str
) -> None:
    result = CalendarResolver().resolve(question, NOW)
    assert result.status == "resolved" and result.query.text == "nas"
    assert result.query.temporal.valid_from == start
    assert result.query.temporal.valid_until == end


@pytest.mark.parametrize(
    "question",
    ("NAS on 31 June 2026", "NAS on 29 February 2026", "NAS on 06/07/2026", "NAS on 6.7.26"),
)
def test_invalid_or_ambiguous_dates_do_not_broaden_to_years(question: str) -> None:
    assert CalendarResolver().resolve(question, NOW).status == "clarification"


def test_calendar_offsets_survive_unicode_expansion_and_spoken_ordinals() -> None:
    parser = CalendarResolver()
    result = parser.resolve("Straße on 15th of July 2026", NOW)
    assert result.status == "resolved" and result.query.text == "strasse"
    assert result.query.temporal.valid_from == "2026-07-14T23:00:00.000000Z"
    followup = parser.resolve("Straße during that SAME period", NOW, result.scope)
    assert followup.status == "resolved" and followup.query.text == "strasse"
    assert followup.scope == result.scope
    span = parser.resolve("Straße from June 2026 until September 2026", NOW)
    assert span.status == "resolved" and span.query.text == "strasse"


def test_technical_model_year_is_not_a_second_calendar_scope() -> None:
    parser = CalendarResolver()
    result = parser.resolve("RTX 2080 memories from summer 2026", NOW)
    assert result.status == "resolved" and result.query.text == "rtx 2080"
    assert result.scope.start == "2026-05-31T23:00:00.000000Z"
    assert parser.resolve("RTX 2080", NOW).status == "ordinary"


def test_calendar_years_include_older_personal_history() -> None:
    result = CalendarResolver().resolve("memories from 1996", NOW)
    assert result.status == "resolved" and result.query.mode == "structured"
    assert result.scope.start == "1996-01-01T00:00:00.000000Z"
    assert result.scope.end == "1997-01-01T00:00:00.000000Z"
