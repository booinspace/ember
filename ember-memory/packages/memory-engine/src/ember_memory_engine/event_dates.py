"""Source-supported event dates resolve locally against the original observation clock."""

import calendar
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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


def resolve_date(support: DateSupport, observed_at: str | None, timezone: str) -> TemporalBounds:
    """No model timestamp is accepted; unknown or unsupported expressions stay undated."""
    if support.expression not in support.quote:
        raise ProcessingError("date_expression_not_in_quote")
    expression = support.expression.strip().casefold()
    zone = ZoneInfo(timezone)
    reference = datetime.fromisoformat(observed_at).astimezone(zone) if observed_at else None
    start: datetime | None = None
    end: datetime | None = None
    precision = "unknown"
    basis = "event_date_unresolved"
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expression):
            start = datetime.fromisoformat(expression).replace(tzinfo=zone)
            end = start + timedelta(days=1)
            precision, basis = "day", "explicit_calendar_date"
        else:
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
