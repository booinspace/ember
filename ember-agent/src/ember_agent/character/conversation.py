"""Bounded native session reads, separate from approved long-term memory."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from ember_memory_abi.related import ContextDependencies

from .source_evidence import SourceAnchor


@dataclass(frozen=True)
class PageFollowup:
    context_id: UUID
    original_request: str
    provider: str
    revision: str
    offered_url: str | None = None


@dataclass(frozen=True)
class CompletedTurn:
    user: str
    assistant: str
    dependencies: tuple[tuple[UUID, int], ...]
    certificate: ContextDependencies | None = None
    request_id: UUID | None = None
    complete: bool = True
    clipped: bool = False
    public_web_reply: bool = False
    source_anchor: SourceAnchor | None = None
    page_followup: PageFollowup | None = None


@runtime_checkable
class ConversationSink(Protocol):
    """Read only this runtime's character-local native conversation."""

    async def recent(self, context_id: UUID, request_id: UUID) -> list[CompletedTurn]: ...

    async def lookup(self, context_id: UUID, request_id: UUID, query: str) -> str | None: ...

    async def commit_context(
        self,
        context_id: UUID,
        request_id: UUID,
        observed_at: str,
        delivered: str,
        complete: bool,
        dependencies: tuple[tuple[UUID, int], ...],
        certificate: ContextDependencies | None,
        *,
        public_web_reply: bool = False,
    ) -> None: ...
