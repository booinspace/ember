"""Resolve public retrieval configuration once, outside Conversation and Tasks."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from ..inference.contracts import GatewayError
from ..public_web import PublicWeb, WebBinding
from ..web_evidence import (
    MAX_WEB_SOURCES,
    PublicWebOperation,
    WebEvidence,
    WebSource,
    allowed_public_url,
    web_failure,
)
from .config import DeploymentConfig
from .hosted_web import HostedWeb
from .native_search import NativeSearch, SearXNGSearch
from .web_mcp import EXA_READ, EXA_SEARCH, MCPWeb
from .web_mcp_settings import MCPWebBinding, binding_revision

_OBJECT = TypeAdapter(dict[str, JsonValue])


def _bound(binding: MCPWebBinding, operation: PublicWebOperation) -> WebBinding:
    web = MCPWeb(binding, operation)
    return WebBinding(binding.provider, binding.label, web.revision, web, lambda: web.configured)


def _snippet_retrieval(
    provider: str,
    search: Callable[[str], Awaitable[str]],
) -> Callable[[str], Awaitable[WebEvidence]]:
    async def retrieve(query: str) -> WebEvidence:
        try:
            raw = await search(query)
            if len(raw.encode()) > 2048:
                return web_failure(provider, "search", "response_bound")
            rows = _OBJECT.validate_json(raw).get("results")
            if not isinstance(rows, list):
                return web_failure(provider, "search", "invalid_response")
            sources: dict[str, WebSource] = {}
            clipped = len(rows) > MAX_WEB_SOURCES
            for row in rows[:MAX_WEB_SOURCES]:
                if not isinstance(row, dict):
                    continue
                url, title, text = row.get("url"), row.get("title"), row.get("snippet")
                if (
                    not isinstance(url, str)
                    or not allowed_public_url(url)
                    or url in sources
                    or not isinstance(title, str)
                    or not isinstance(text, str)
                    or not text.strip()
                ):
                    continue
                encoded = text.encode()
                short = len(encoded) > 256 or len(title) > 160
                sources[url] = WebSource(
                    url=url,
                    title=title[:160],
                    passages=(encoded[:256].decode(errors="ignore"),),
                    truncated=short,
                )
                clipped |= short
            return WebEvidence(
                provider=provider,
                operation="search",
                retrieved_at=datetime.now(UTC),
                coverage="partial" if sources else "empty",
                sources=tuple(sources.values()),
                truncated=clipped,
                snippets=True,
            )
        except GatewayError:
            return web_failure(provider, "search", "unavailable")
        except ValueError:
            return web_failure(provider, "search", "invalid_response")

    return retrieve


async def resolve_public_web(config: DeploymentConfig) -> PublicWeb:
    """Build exact per-operation routes, with no network probes or fallback."""
    custom = config.public_web_mcp
    search = _bound(custom.search, "search") if custom and custom.search else None
    read = _bound(custom.read, "read") if custom and custom.read else None
    legacy = config.public_search
    if legacy is None:
        return PublicWeb(search, read)
    provider = legacy.provider
    if search or (read and provider in {"exa", "exa_mcp", "parallel"}):
        raise ValueError("Public retrieval bindings overlap")
    if provider == "exa_mcp":
        return PublicWeb(_bound(EXA_SEARCH, "search"), _bound(EXA_READ, "read"))
    if provider in ("exa", "parallel"):
        assert legacy.api_key_env is not None
        hosted = HostedWeb(provider, legacy.api_key_env)
        label = "Exa" if provider == "exa" else "Parallel"
        revision = binding_revision("rest-web-v1", provider, legacy.api_key_env)

        def ready() -> bool:
            return hosted.configured

        return PublicWeb(
            WebBinding(provider, label, revision, hosted.search, ready),
            WebBinding(provider, label, revision, hosted.read, ready),
        )
    revision = binding_revision("snippet-search-v1", legacy.model_dump(mode="json"))
    if legacy.searxng_url:
        return PublicWeb(
            WebBinding(
                provider,
                "SearXNG",
                revision,
                _snippet_retrieval(provider, SearXNGSearch(legacy.searxng_url)),
                snippets=True,
            ),
            read,
        )
    available = await asyncio.to_thread(Path(legacy.command[0]).is_file)
    native = NativeSearch(legacy.command, config.runtime_root / "public-search-homes")
    return PublicWeb(
        WebBinding(
            provider,
            "Installed search",
            revision,
            _snippet_retrieval(provider, native),
            lambda: available,
            snippets=True,
        ),
        read,
    )
