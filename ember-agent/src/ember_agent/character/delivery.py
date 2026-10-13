"""Optional transport acknowledgements distinguish generated output from audible delivery."""

from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class ObservedDialogueSink(Protocol):
    """Native sources use stable request identity, observation time and separate delivery writes."""

    async def begin_input(
        self, context_id: UUID, request_id: UUID, observed_at: str, user: str
    ) -> None:
        """Persist original user input even when generation or output later fails."""
        ...

    async def commit_delivery(
        self,
        context_id: UUID,
        request_id: UUID,
        observed_at: str,
        delivered: str,
        complete: bool,
    ) -> None:
        """Record only acknowledged output; retry the identical request and receipt."""
        ...
