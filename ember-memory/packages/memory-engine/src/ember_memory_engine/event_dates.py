"""Source-supported event dates resolve locally against the original observation clock."""

import calendar
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.temporal import TemporalBounds

from .processing_contracts import DateSupport, ProcessingError

MONTHS = {name.casefold(): number for number, name in enumerate(calendar.month_name) if name}


def supported_position(text: str, quote: str, hint: int | None) -> int:
    """Unique literal passages locate themselves; repeated ones require an exact offset."""
    first = text.find(quote)
    unique = first >= 0 and text.find(quote, first + 1) < 0
    position = first if unique else hint
    if position is None or position < 0 or text[position : position + len(quote)] != quote:
        raise ProcessingError("date_quote_missing_or_ambiguous")
    return position


def resolve_date(
    support: DateSupport,
    observed_at: str | None,
    timezone: str,
    *,
    observed_precision: str = "microsecond",
) -> TemporalBounds:
    """No model timestamp is accepted; unknown or unsupported expressions stay undated."""
    passages = list(
        re.finditer(r"(?<!\w)" + re.escape(support.expression) + r"(?!\w)", support.quote)
    )
    if not support.expression.strip() or not passages:
        raise ProcessingError("date_expression_not_in_quote")
    if re.fullmatch(r"\d{4}", support.expression) and all(
        re.search(
            r"\b(gpu|rtx|model|port|version)\s*$", support.quote[: passage.start()].casefold()
        )
        for passage in passages
    ):
        return TemporalBounds(observed_at=observed_at, basis="technical_identifier_not_event_date")
    expression = support.expression.strip().casefold()
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ProcessingError("temporal_timezone_unavailable") from error
    reference = datetime.fromisoformat(observed_at).astimezone(zone) if observed_at else None
    start: datetime | None = None
    end: datetime | None = None
    precision = "unknown"
    basis = "event_date_unresolved"
    if reference is not None:
        ranks = {
            "year": 1,
            "month": 2,
            "day": 3,
            "hour": 3,
            "minute": 3,
            "second": 3,
            "millisecond": 3,
            "microsecond": 3,
            "source_numeric": 3,
        }
        required = (
            3
            if expression in {"today", "yesterday"}
            else 1
            if expression == "last year"
            else 2
            if expression.startswith("last ")
            else 0
        )
        if ranks.get(observed_precision, 0) < required:
            reference = None
            basis = "source_clock_precision_insufficient"
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expression):
            start = datetime.fromisoformat(expression).replace(tzinfo=zone)
            end = start + timedelta(days=1)
            precision, basis = "day", "explicit_calendar_date"
        else:
            day_match = re.fullmatch(
                r"(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+(" + "|".join(MONTHS) + r")\s+(\d{4})",
                expression,
            )
            month_first = re.fullmatch(
                r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
                expression,
            )
            if day_match or month_first:
                if day_match:
                    day, name, year = day_match.groups()
                else:
                    assert month_first is not None
                    name, day, year = month_first.groups()
                start = datetime(int(year), MONTHS[name], int(day), tzinfo=zone)
                end = start + timedelta(days=1)
                return TemporalBounds(
                    valid_from=timestamp_from_datetime(start),
                    valid_until=timestamp_from_datetime(end),
                    observed_at=observed_at,
                    precision="day",
                    basis="explicit_calendar_date",
                )
            month_match = re.fullmatch(
                r"(?:(last)\s+)?(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?", expression
            )
            if month_match:
                prior, name, raw_year = month_match.groups()
                month = MONTHS[name]
                year = (
                    int(raw_year)
                    if raw_year
                    else (
                        reference.year - (month >= reference.month) if reference and prior else None
                    )
                )
                if year is not None:
                    start = datetime(year, month, 1, tzinfo=zone)
                    end = datetime(
                        year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=zone
                    )
                    precision = "month"
                    basis = "source_relative_calendar" if prior else "explicit_calendar_month"
            elif re.fullmatch(r"\d{4}", expression):
                year = int(expression)
                start, end = (
                    datetime(year, 1, 1, tzinfo=zone),
                    datetime(year + 1, 1, 1, tzinfo=zone),
                )
                precision, basis = "year", "explicit_calendar_year"
            elif reference and expression in {"yesterday", "today", "last year", "last month"}:
                if expression.endswith("year"):
                    start = datetime(reference.year - 1, 1, 1, tzinfo=zone)
                    end = datetime(reference.year, 1, 1, tzinfo=zone)
                    precision = "year"
                elif expression.endswith("month"):
                    year, month = (
                        (reference.year - 1, 12)
                        if reference.month == 1
                        else (reference.year, reference.month - 1)
                    )
                    start = datetime(year, month, 1, tzinfo=zone)
                    end = datetime(reference.year, reference.month, 1, tzinfo=zone)
                    precision = "month"
                else:
                    start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
                    if expression == "yesterday":
                        start -= timedelta(days=1)
                    end = start + timedelta(days=1)
                    precision = "day"
                basis = "source_relative_calendar"
    except (ValueError, OverflowError):
        start, end, precision, basis = None, None, "unknown", "event_date_unresolved"
    return TemporalBounds(
        valid_from=timestamp_from_datetime(start) if start else None,
        valid_until=timestamp_from_datetime(end) if end else None,
        observed_at=observed_at,
        precision=precision,
        basis=basis,
    )


def interpretation_date(
    support: DateSupport,
    kind: str,
    observed_at: str | None,
    timezone: str,
    *,
    observed_precision: str = "microsecond",
) -> TemporalBounds:
    """Event occurrence never silently expires a current fact or hides an existing plan."""
    resolved = resolve_date(support, observed_at, timezone, observed_precision=observed_precision)
    if kind == "episode":
        return resolved
    if (
        kind == "fact"
        and support.status == "current_state"
        and re.search(
            r"\bsince\s+" + re.escape(support.expression.casefold()), support.quote.casefold()
        )
    ):
        return resolved.model_copy(
            update={
                "valid_until": None,
                "basis": "source_supported_state_start",
            }
        )
    return TemporalBounds(
        observed_at=observed_at,
        basis="target_date_not_state_validity"
        if kind in {"goal", "plan"}
        else "event_date_not_state_validity",
    )
