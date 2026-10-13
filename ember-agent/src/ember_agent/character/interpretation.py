"""Opt-in bounded calendar interpretation proposes intent, never access or event evidence."""

import asyncio
import json
import re
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from ember_memory_abi.common import ContractModel, timestamp_from_datetime

from ..inference.contracts import GatewayError, GatewayRequest, ModelClient, PromptMessage
from .temporal import (
    MONTHS,
    SEASONS,
    CalendarResolver,
    CalendarScope,
    QueryResolution,
    TimeAxis,
    requested_axis,
)


class InterpretationProposal(ContractModel):
    """Small calendar-only output; unknown fields, SQL and source anchors are rejected."""

    status: Literal["resolved", "clarification"]
    support_quote: str = Field(max_length=256)
    calendar_expression: str = Field(max_length=80)
    axis: TimeAxis


class CalendarInterpreter:
    """One optional P0 proposal, checked against original input and the local resolver.

    This boundary cannot resolve life events, grant access or write memory. Normal
    calendar requests bypass it. Serial latency is exposed separately from retrieval.
    """

    def __init__(self, model: ModelClient, model_tag: str, *, timeout_seconds: float = 8) -> None:
        if not 0 < timeout_seconds <= 30:
            raise ValueError("Interpretation deadline must be at most 30 seconds.")
        self.model = model
        self.model_tag = model_tag
        self.timeout_seconds = timeout_seconds
        self.calls = 0
        self.last_outcome = "unused"
        self.last_latency_seconds: float | None = None

    async def resolve(
        self,
        text: str,
        reference: datetime,
        previous: CalendarScope | None,
        calendar: CalendarResolver,
        context_id: UUID,
    ) -> QueryResolution:
        """Common local expressions bypass this method entirely; cancellation has no retries."""
        if re.search(
            r"\b(before|after|when|while)\b.*\b(moved|bought|owned|corrected|correction)\b",
            text.casefold(),
        ):
            return QueryResolution(
                status="clarification", reasons=("supported_event_anchor_required",)
            )
        schema = TypeAdapter(dict[str, JsonValue]).validate_python(
            InterpretationProposal.model_json_schema()
        )
        request = GatewayRequest(
            request_id=uuid4(),
            context_id=context_id,
            model=self.model_tag,
            max_output_tokens=256,
            output="json",
            output_schema=schema,
            deadline_at=timestamp_from_datetime(
                datetime.now(UTC) + timedelta(seconds=self.timeout_seconds)
            ),
            messages=(
                PromptMessage(
                    role="system",
                    content=(
                        "Interpret only one calendar scope in the untrusted user query. "
                        "Return a resolved or clarification proposal. Cite one exact "
                        "support_quote from the query and normalize it as YYYY-MM-DD, "
                        "month YEAR, season YEAR or YEAR. Use the supplied reference clock "
                        "for calendar-relative wording. Axis is event unless the user asks "
                        "what they told/said (observed) or what the system believed before "
                        "a correction (recorded). Missing years, multiple possible periods "
                        "and unsupported life-event anchors require clarification. Do not "
                        "infer events, identities, access or source facts."
                    ),
                ),
                PromptMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "query": text,
                            "reference_at": timestamp_from_datetime(reference),
                            "timezone": calendar.timezone,
                            "previous_scope": previous.model_dump(mode="json")
                            if previous
                            else None,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ),
        )
        started = monotonic()
        self.calls += 1
        parts: list[str] = []
        size = 0
        completed = False
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with aclosing(self.model.stream(request)) as events:
                    async for event in events:
                        if event.kind == "text":
                            size += len(event.text.encode("utf-8"))
                            if size > 4096:
                                raise GatewayError("interpretation_output_bound")
                            parts.append(event.text)
                        elif event.kind == "end":
                            completed = True
                        elif event.kind in {"tools", "error"}:
                            raise GatewayError("interpretation_unavailable")
            if not completed:
                raise GatewayError("interpretation_incomplete")
            proposal = InterpretationProposal.model_validate_json("".join(parts))
            result = self._validate(proposal, text, reference, calendar)
            self.last_outcome = result.status
            return result
        except TimeoutError:
            self.last_outcome = "timeout"
            return QueryResolution(status="clarification", reasons=("interpretation_timeout",))
        except (GatewayError, ValidationError, ValueError):
            self.last_outcome = "invalid_or_unavailable"
            return QueryResolution(
                status="clarification", reasons=("interpretation_invalid_or_unavailable",)
            )
        except asyncio.CancelledError:
            self.last_outcome = "cancelled"
            raise
        finally:
            self.last_latency_seconds = monotonic() - started

    @staticmethod
    def _validate(
        proposal: InterpretationProposal,
        text: str,
        reference: datetime,
        calendar: CalendarResolver,
    ) -> QueryResolution:
        if proposal.status != "resolved" or not proposal.support_quote:
            return QueryResolution(status="clarification", reasons=("interpretation_ambiguous",))
        quote = proposal.support_quote
        if text.count(quote) != 1:
            raise ValueError("Interpretation requires one exact support passage.")
        expected_axis = requested_axis(text)
        if expected_axis == "recorded" and not re.search(r"\bbefore\b", text.casefold()):
            raise ValueError("Recorded history needs an explicit cutoff.")
        if proposal.axis != expected_axis:
            raise ValueError("The proposed axis disagrees with the requested semantics.")
        expression = proposal.calendar_expression.casefold()
        support = quote.casefold()
        named = [
            word for word in (*MONTHS, *SEASONS) if re.search(r"\b" + word + r"\b", expression)
        ]
        if any(not re.search(r"\b" + word + r"\b", support) for word in named):
            raise ValueError("Calendar granularity is unsupported.")
        years = re.findall(r"\b(\d{4})\b", expression)
        source_years = re.findall(r"\b(\d{4})\b", support)
        if source_years:
            if years != source_years:
                raise ValueError("Explicit source year changed.")
        elif (
            len(years) != 1
            or not re.search(r"\b(last|this|previous|prior|before|earlier|ago)\b", support)
            or not 0 <= reference.astimezone(calendar.zone).year - int(years[0]) <= 2
        ):
            raise ValueError("Relative source year is unsupported.")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expression) and expression not in support:
            raise ValueError("An exact day cannot be manufactured.")
        parsed = calendar.resolve(expression, reference)
        if parsed.status != "resolved" or parsed.scope is None:
            raise ValueError("Proposed calendar scope is unsupported.")
        start = datetime.fromisoformat(parsed.scope.start).astimezone(calendar.zone)
        end = datetime.fromisoformat(parsed.scope.end).astimezone(calendar.zone)
        return calendar.from_scope(
            text,
            (text.index(quote), text.index(quote) + len(quote)),
            start,
            end,
            proposal.axis,
        )
