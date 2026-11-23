"""Resolved public capabilities, without provider transport or invocation authority."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .web_evidence import (
    PublicWebOperation,
    WebEvidence,
    allowed_public_url,
    permitted_public_query,
    web_failure,
)

SEARCH = "search_public"
PAGE = "read_public_page"


@dataclass(frozen=True)
class WebBinding:
    provider: str
    label: str
    revision: str
    retrieve: Callable[[str], Awaitable[WebEvidence]]
    ready: Callable[[], bool] = lambda: True
    snippets: bool = False


@dataclass(frozen=True)
class PublicWeb:
    """Explicit per-operation selection. No discovery, fan-out or fallback."""

    search_binding: WebBinding | None = None
    read_binding: WebBinding | None = None

    def binding(self, operation: PublicWebOperation) -> WebBinding | None:
        return self.search_binding if operation == "search" else self.read_binding

    async def retrieve(self, operation: PublicWebOperation, value: str) -> WebEvidence:
        binding = self.binding(operation)
        if binding is None or not binding.ready():
            return web_failure(
                binding.provider if binding else "unconfigured", operation, "unavailable"
            )
        if not (
            permitted_public_query(value) if operation == "search" else allowed_public_url(value)
        ):
            return web_failure(binding.provider, operation, "input_rejected")
        evidence = await binding.retrieve(value)
        if (
            evidence.operation != operation
            or evidence.provider != binding.provider
            or (evidence.coverage == "partial" and evidence.snippets != binding.snippets)
        ):
            return web_failure(binding.provider, operation, "invalid_response")
        return evidence

    async def search(self, query: str) -> WebEvidence:
        """Retrieve evidence for one already-authorized public query."""
        return await self.retrieve("search", query)

    async def read(self, url: str) -> WebEvidence:
        """Retrieve evidence for one already-authorized public URL."""
        return await self.retrieve("read", url)
