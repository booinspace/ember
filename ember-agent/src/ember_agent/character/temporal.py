"""Local calendar interpretation shared by text, transcripts and explicit investigations."""

import calendar
import re
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from ember_memory_abi.common import ContractModel, timestamp_from_datetime
from ember_memory_abi.interfaces import MemoryQuery, RequestOptions, TemporalQuery

type TimeAxis = Literal["event", "observed", "recorded"]
type BeliefScope = Literal["current", "history"]

CURRENT_BELIEF = "ember.current_belief_v1"
HISTORICAL_IDENTITY = "ember.identity_recall_v1"
_BELIEF_PREFIX = re.compile(r"^\s*(?:(?:/recall|recall deeply)\s+)?(current|history):\s*", re.I)


def belief_intent(text: str) -> tuple[BeliefScope, str] | None:
    """Recognize explicit typed recall, including existing recall command prefixes."""
    match = _BELIEF_PREFIX.match(text)
    if match is None:
        return None
    return ("current" if match[1].lower() == "current" else "history", text[match.end() :])


class CalendarScope(ContractModel):
    """Resolved calendar boundaries retain their axis and local timezone."""

    axis: TimeAxis
    start: str
    end: str
    timezone: str


class QueryResolution(ContractModel):
    """Clarification is explicit; unresolved dates cannot become unfiltered searches."""

    status: Literal["resolved", "ordinary", "clarification"]
    query: MemoryQuery = Field(default_factory=MemoryQuery)
    scope: CalendarScope | None = None
    reasons: tuple[str, ...] = ()


MONTHS = {name.casefold(): number for number, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.casefold(): number for number, name in enumerate(calendar.month_abbr) if name})
SEASONS = {
    "spring": (3, 6),
    "summer": (6, 9),
    "autumn": (9, 12),
    "fall": (9, 12),
    "winter": (12, 3),
}
CALENDAR = re.compile(
    r"\b(?:(?P<date>\d{4}-\d{2}-\d{2})|"
    r"(?P<date_day>\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<date_month>"
    + "|".join(MONTHS)
    + r")\s+(?P<date_year>\d{4})|(?P<month_first>"
    + "|".join(MONTHS)
    + r")\s+(?P<month_day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<month_year>\d{4})|"
    r"(?P<period>spring|summer|autumn|fall|winter|"
    + "|".join(MONTHS)
    + r")\s+(?:of\s+)?(?P<year>\d{4}|\d{2})|(?P<relative>last|this)\s+"
    r"(?P<unit>year|month|week)|(?P<relative_season>last|this)\s+"
    r"(?P<season>spring|summer|autumn|fall|winter)|"
    r"(?P<day>today|yesterday)|(?P<whole_year>(?:18|19|20|21)\d{2}))\b",
    re.IGNORECASE,
)


def requested_axis(text: str) -> TimeAxis:
    """Distinguish the system's recorded belief from the person's historical state."""
    lower = text.casefold()
    if re.search(
        r"\b(?:(?:you|ember|system)\s+(?:believe|believed)|was\s+believed|"
        r"(?:your|system(?:'s)?)\s+belief)\b",
        lower,
    ):
        return "recorded"
    if re.search(r"\b(tell|told|said|mentioned)\b", lower):
        return "observed"
    return "event"


def _month_after(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


class CalendarResolver:
    """Pure bounded parser; clocks and timezone are injected, never inferred from import time."""

    def __init__(self, timezone: str = "Europe/London") -> None:
        self.timezone = timezone
        self.zone = ZoneInfo(timezone)

    def resolve(
        self,
        text: str,
        reference: datetime,
        previous: CalendarScope | None = None,
        *,
        axis_override: TimeAxis | None = None,
    ) -> QueryResolution:
        """Resolve one supported scope; ambiguity and invalid dates require clarification."""
        if reference.tzinfo is None:
            raise ValueError("A conversation reference clock must be timezone aware.")
        if len(text) > 8192:
            return QueryResolution(status="clarification", reasons=("query_too_long",))
        lower = text.casefold()
        axis = axis_override or requested_axis(text)
        if re.search(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b", lower):
            return QueryResolution(status="clarification", reasons=("ambiguous_numeric_date",))
        followup = re.search(
            r"\b(the year before|during that same period|same period)\b", text, re.IGNORECASE
        )
        if followup:
            if previous is None:
                return QueryResolution(
                    status="clarification", reasons=("missing_temporal_antecedent",)
                )
            start, end = (
                datetime.fromisoformat(previous.start),
                datetime.fromisoformat(previous.end),
            )
            start, end = start.astimezone(self.zone), end.astimezone(self.zone)
            axis = axis if axis != "event" else previous.axis
            if followup.group().casefold() == "the year before":
                try:
                    start, end = start.replace(year=start.year - 1), end.replace(year=end.year - 1)
                except ValueError:
                    return QueryResolution(status="clarification", reasons=("ambiguous_leap_day",))
            return self.from_scope(text, followup.span(), start, end, axis)
        # Match original Unicode text: casefold can expand characters and invalidate spans.
        matches = [
            match
            for match in CALENDAR.finditer(text)
            if not (
                match["whole_year"]
                and re.search(
                    r"\b(gpu|rtx|model|port|version)\s*$", text[: match.start()].casefold()
                )
            )
        ]
        if not matches:
            if re.search(
                r"\b(before|after|when|while)\b.*\b(moved|move|owned|had|correction|corrected)\b",
                lower,
            ):
                return QueryResolution(
                    status="clarification", reasons=("supported_event_anchor_required",)
                )
            periods = "|".join((*MONTHS, *SEASONS))
            if (
                re.search(
                    r"\b(?:during|from|in|before|after|last|previous)\s+(?:" + periods + r")\b",
                    lower,
                )
                or re.search(r"\bmemories\s+(?:from\s+)?(?:" + periods + r")\b", lower)
                or lower.strip() in (*MONTHS, *SEASONS)
            ):
                return QueryResolution(status="clarification", reasons=("calendar_year_required",))
            return QueryResolution(status="ordinary", query=MemoryQuery(text=text))
        if len(matches) == 2:
            first, last_match = matches
            connector = text[first.end() : last_match.start()].strip().casefold()
            prefix = text[: first.start()].casefold()
            if (
                re.search(r"\b(from|between)\s*$", prefix)
                and connector in {"to", "through", "until", "and"}
                and axis != "recorded"
            ):
                lower_scope = self.resolve(first.group(), reference)
                upper_scope = self.resolve(last_match.group(), reference)
                if lower_scope.scope is not None and upper_scope.scope is not None:
                    start = datetime.fromisoformat(lower_scope.scope.start)
                    end = datetime.fromisoformat(
                        upper_scope.scope.start if connector == "until" else upper_scope.scope.end
                    )
                    if end > start:
                        return self.from_scope(
                            text, (first.start(), last_match.end()), start, end, axis
                        )
            return QueryResolution(status="clarification", reasons=("multiple_calendar_scopes",))
        if len(matches) != 1:
            return QueryResolution(status="clarification", reasons=("multiple_calendar_scopes",))
        if axis == "recorded" and not re.search(r"\bbefore\b", lower):
            return QueryResolution(status="clarification", reasons=("recorded_cutoff_required",))
        match = matches[0]
        local = reference.astimezone(self.zone)
        try:
            if match["date"]:
                start = datetime.fromisoformat(match["date"]).replace(tzinfo=self.zone)
                end = start + timedelta(days=1)
            elif match["date_day"] or match["month_day"]:
                name = match["date_month"] or match["month_first"]
                year = int(match["date_year"] or match["month_year"])
                day = int(match["date_day"] or match["month_day"])
                start = datetime(year, MONTHS[name.casefold()], day, tzinfo=self.zone)
                end = start + timedelta(days=1)
            elif match["period"]:
                raw_year = match["year"]
                if len(raw_year) != 4:
                    return QueryResolution(
                        status="clarification", reasons=("ambiguous_calendar_year",)
                    )
                year, period = int(raw_year), match["period"].casefold()
                if period in SEASONS:
                    first, last = SEASONS[period]
                    start = datetime(year, first, 1, tzinfo=self.zone)
                    end = datetime(year + (last < first), last, 1, tzinfo=self.zone)
                else:
                    month = MONTHS[period]
                    start = datetime(year, month, 1, tzinfo=self.zone)
                    next_year, next_month = _month_after(year, month)
                    end = datetime(next_year, next_month, 1, tzinfo=self.zone)
            elif match["relative_season"]:
                season = match["season"].casefold()
                first, last_month = SEASONS[season]
                year = local.year
                if season == "winter" and local.month < 3:
                    year -= 1
                start = datetime(year, first, 1, tzinfo=self.zone)
                end = datetime(year + (last_month < first), last_month, 1, tzinfo=self.zone)
                if match["relative_season"].casefold() == "last" and end > local:
                    start, end = start.replace(year=start.year - 1), end.replace(year=end.year - 1)
            elif match["whole_year"]:
                year = int(match["whole_year"])
                start = datetime(year, 1, 1, tzinfo=self.zone)
                end = datetime(year + 1, 1, 1, tzinfo=self.zone)
            elif match["day"]:
                start = local.replace(hour=0, minute=0, second=0, microsecond=0)
                if match["day"].casefold() == "yesterday":
                    start -= timedelta(days=1)
                end = start + timedelta(days=1)
            else:
                last = match["relative"].casefold() == "last"
                unit = match["unit"].casefold()
                if unit == "year":
                    year = local.year - last
                    start = datetime(year, 1, 1, tzinfo=self.zone)
                    end = datetime(year + 1, 1, 1, tzinfo=self.zone)
                elif unit == "month":
                    year, month = local.year, local.month
                    if last:
                        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
                    start = datetime(year, month, 1, tzinfo=self.zone)
                    next_year, next_month = _month_after(year, month)
                    end = datetime(next_year, next_month, 1, tzinfo=self.zone)
                else:
                    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
                    start -= timedelta(days=local.weekday() + 7 * last)
                    end = start + timedelta(days=7)
        except (ValueError, OverflowError):
            return QueryResolution(status="clarification", reasons=("invalid_calendar_date",))
        return self.from_scope(text, match.span(), start, end, axis)

    def resolve_belief(
        self, intent: tuple[BeliefScope, str], reference: datetime
    ) -> QueryResolution:
        """Select accepted claims only; never reinterpret missing scope as ordinary recall."""
        kind, topic = intent
        if not topic.strip() or len(topic) > 8192:
            return QueryResolution(status="clarification", reasons=("belief_topic_required",))
        if kind == "current":
            return QueryResolution(
                status="resolved",
                query=MemoryQuery(
                    text=topic,
                    mode="structured",
                    kinds=("fact",),
                    options=RequestOptions(extensions={CURRENT_BELIEF: {}}),
                ),
            )
        recorded = re.search(r"\brecorded[- ]before\b", topic, re.I)
        topic = topic.replace(recorded.group(), "before", 1) if recorded else topic
        axis = "recorded" if recorded else requested_axis(topic)
        if axis == "event" and re.search(r"\b(?:before|after)\b", topic, re.I):
            return QueryResolution(
                status="clarification", reasons=("belief_history_scope_required",)
            )
        resolved = self.resolve(topic, reference, axis_override=axis)
        if resolved.status == "clarification":
            return resolved
        if resolved.scope is None or resolved.scope.axis == "observed":
            return QueryResolution(
                status="clarification", reasons=("belief_history_scope_required",)
            )
        if not resolved.query.text:
            return QueryResolution(status="clarification", reasons=("belief_topic_required",))
        return resolved.model_copy(
            update={
                "query": resolved.query.model_copy(
                    update={
                        "mode": "structured",
                        "kinds": ("fact",),
                        "modalities": ("FACTUAL_ASSERTION", "PREFERENCE", "BELIEF"),
                        "options": RequestOptions(extensions={HISTORICAL_IDENTITY: {}}),
                    }
                )
            }
        )

    def from_scope(
        self, text: str, span: tuple[int, int], start: datetime, end: datetime, axis: TimeAxis
    ) -> QueryResolution:
        """Compose validated boundaries and an exact input span into existing query contracts."""
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or end <= start
            or not 0 <= span[0] <= span[1] <= len(text)
        ):
            raise ValueError("A bounded timezone-aware calendar scope is required.")
        start_utc, end_utc = timestamp_from_datetime(start), timestamp_from_datetime(end)
        temporal = (
            TemporalQuery(valid_from=start_utc, valid_until=end_utc)
            if axis == "event"
            else TemporalQuery(observed_from=start_utc, observed_until=end_utc)
            if axis == "observed"
            else TemporalQuery(recorded_before=start_utc)
        )
        # Calendar-only retrieval is structured. Topic words stay available to BGE/FTS.
        remaining = text[: span[0]] + " " + text[span[1] :]
        words = re.findall(r"[\w]+", remaining.casefold())
        filler = {
            "do",
            "you",
            "remember",
            "what",
            "happened",
            "in",
            "from",
            "between",
            "during",
            "memories",
            "memory",
            "of",
            "the",
            "that",
            "me",
            "my",
            "i",
            "did",
            "tell",
            "told",
            "said",
            "mentioned",
            "believe",
            "believed",
            "before",
            "about",
            "show",
            "can",
            "recall",
            "please",
            "when",
            "on",
            "was",
            "a",
            "an",
        }
        topic = " ".join(word for word in words if word not in filler)
        return QueryResolution(
            status="resolved",
            query=MemoryQuery(
                text=topic, mode="hybrid" if topic else "structured", temporal=temporal
            ),
            scope=CalendarScope(axis=axis, start=start_utc, end=end_utc, timezone=self.timezone),
            reasons=("strict_event_time_excludes_undated",) if axis == "event" else (),
        )
