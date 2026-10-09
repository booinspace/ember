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
