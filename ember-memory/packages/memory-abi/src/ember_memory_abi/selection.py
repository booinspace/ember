"""Optional character-bound recall selection; settings never confer source access."""

from typing import Annotated, Literal, Protocol, Self, runtime_checkable

from pydantic import Field, model_validator

from .common import CanonicalId, ContractModel, NonnegativeInteger


class RecallSelection(ContractModel):
    """Persistent origin selection independent of capture, grants and model identity."""

    local_enabled: bool = True
    import_mode: Literal["all", "selected", "none"] = "all"
    included_libraries: Annotated[tuple[CanonicalId, ...], Field(max_length=128)] = ()
    excluded_libraries: Annotated[tuple[CanonicalId, ...], Field(max_length=128)] = ()
    revision: NonnegativeInteger = 0

    @model_validator(mode="after")
    def distinct(self) -> Self:
        """Reject duplicate or contradictory choices instead of widening recall."""
        for values in (self.included_libraries, self.excluded_libraries):
            if len(set(values)) != len(values):
                raise ValueError("Recall library selections must be distinct.")
        if set(self.included_libraries) & set(self.excluded_libraries):
            raise ValueError("A library cannot be both included and excluded.")
        return self


@runtime_checkable
class RecallSelectionClient(Protocol):
    """Optional synchronous controls bound to an existing character identity."""

    def recall_selection(self) -> RecallSelection:
        """Read current settings; no source or memory text is returned."""
        ...

    def configure_recall(self, selection: RecallSelection) -> RecallSelection:
        """Compare-and-swap settings using selection.revision; requires operator authority."""
        ...


@runtime_checkable
class AsyncRecallSelectionClient(Protocol):
    """Optional async controls using the same bounded canonical owner."""

    async def recall_selection(self) -> RecallSelection:
        """Read current selection metadata."""
        ...

    async def configure_recall(self, selection: RecallSelection) -> RecallSelection:
        """Apply an authorized compare-and-swap settings change."""
        ...
