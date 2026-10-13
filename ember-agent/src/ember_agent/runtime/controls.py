"""Application controls remain separate from model tools and backend implementation types."""

from typing import Protocol

from pydantic import UUID4, Field

from ember_memory_abi.selection import RecallSelection

from .config import RuntimeModel


class CaptureControl(RuntimeModel):
    """Capture does not change recall selection or confer import approval."""

    enabled: bool
    revision: int = Field(ge=0)


class LibraryChoice(RuntimeModel):
    """Source identity/label only; listing a library never grants or seeds knowledge."""

    library_id: UUID4
    namespace: str = Field(max_length=1000)
    available: bool


class ProcessingStatus(RuntimeModel):
    """Content-free native processing counts, separately requested by the operator."""

    counts: dict[str, int] = Field(max_length=10)
    failure_codes: tuple[str, ...] = Field(max_length=8)
    index_status: str


class RuntimeControls(Protocol):
    """Only the authenticated local operator invokes these confirmed-user mutations."""

    async def processing_status(self) -> ProcessingStatus:
        """Inspect bounded aggregate native progress and index health off-loop."""
        ...

    async def libraries(self) -> tuple[LibraryChoice, ...]:
        """List at most 128 source labels off-loop, without reading source text."""
        ...

    async def capture_settings(self) -> CaptureControl:
        """Inspect persistent capture policy without a database owner in the web server."""
        ...

    async def configure_capture(self, value: CaptureControl) -> CaptureControl:
        """Compare-and-swap capture independently of source selection."""
        ...

    async def recall_settings(self) -> RecallSelection:
        """Read origin-based recall selection, independently of grants."""
        ...

    async def configure_recall(self, value: RecallSelection) -> RecallSelection:
        """Persist a confirmed-user selection revision; no automatic grant creation."""
        ...
