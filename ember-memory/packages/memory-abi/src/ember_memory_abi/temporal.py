"""Distinct valid, observed, and recorded time axes with source ambiguity retained."""

from typing import Self

from pydantic import model_validator

from .common import ContractModel, UtcTimestamp, WireString


class SourceTimestamp(ContractModel):
    """Unresolved source clock; recorded/import time never fills a missing value."""

    value: UtcTimestamp | None = None
    raw_value: str | int | float | None = None
    units: WireString | None = None
    precision: WireString = "unknown"
    basis: WireString = "unknown"
    timezone_assumption: str | None = None
    ambiguity_reasons: tuple[WireString, ...] = ()


class TemporalBounds(ContractModel):
    """Optional half-open validity with explicit observation/confirmation metadata."""

    valid_from: UtcTimestamp | None = None
    valid_until: UtcTimestamp | None = None
    observed_at: UtcTimestamp | None = None
    last_confirmed_at: UtcTimestamp | None = None
    precision: WireString = "unknown"
    basis: WireString = "unknown"

    @model_validator(mode="after")
    def _nonempty_interval(self) -> Self:
        if self.valid_from is not None and self.valid_until is not None:
            if self.valid_until <= self.valid_from:
                raise ValueError("Known validity bounds must define a nonempty half-open interval.")
        return self


class TemporalMetadata(TemporalBounds):
    """A stored interpretation's recorded clock, independent of historical validity."""

    recorded_at: UtcTimestamp


class TemporalQuery(ContractModel):
    """Filter world time and recorded interpretation history independently."""

    valid_at: UtcTimestamp | None = None
    recorded_before: UtcTimestamp | None = None
    observed_from: UtcTimestamp | None = None
    observed_until: UtcTimestamp | None = None
    valid_from: UtcTimestamp | None = None
    valid_until: UtcTimestamp | None = None

    @model_validator(mode="after")
    def intervals(self) -> Self:
        """Reject empty or reversed intervals instead of returning misleading results."""
        for lower, upper in (
            (self.observed_from, self.observed_until),
            (self.valid_from, self.valid_until),
        ):
            if lower is not None and upper is not None and lower >= upper:
                raise ValueError("Temporal ranges must be nonempty half-open intervals.")
        return self
