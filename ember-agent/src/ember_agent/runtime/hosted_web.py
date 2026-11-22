"""Single-call, credential-isolated Exa and Parallel adapters for explicit public inputs."""

import asyncio
import os
import re
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import JsonValue, TypeAdapter

from ..web_evidence import (
    MAX_WEB_PASSAGE,
    MAX_WEB_SOURCES,
    PublicWebOperation,
    WebEvidence,
    WebSource,
    allowed_public_query,
    allowed_public_url,
    web_failure,
)

HostedWebName = Literal["exa", "parallel"]
_ORIGINS = {"exa": "https://api.exa.ai", "parallel": "https://api.parallel.ai"}
_WIRE_LIMIT = 131_072
_TIMEOUT = 8.0
_SEARCH_CHARS = 900
_READ_CHARS = 2600
_OBJECT: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


class HostedWeb:
    """Retrieve excerpts only. No provider answers, retries, redirects or local page fetches."""

    def __init__(
        self,
        provider: HostedWebName,
        api_key_env: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._provider = provider
        self._api_key_env = api_key_env
        self._transport = transport

    def _key(self) -> str:
        key = os.environ.get(self._api_key_env, "")
        return key if re.fullmatch(r"[!-~]{1,1024}", key) else ""

    @property
    def configured(self) -> bool:
        """Credential present and usable as a header, not an upstream health claim."""
        return bool(self._key())

    async def search(self, query: str) -> WebEvidence:
        """Search and return associated excerpts in one provider round trip."""
        if not allowed_public_query(query):
            return web_failure(self._provider, "search", "input_rejected")
        if self._provider == "exa":
            return await self._request(
                "search",
                "/search",
                {
                    "query": query,
                    "numResults": MAX_WEB_SOURCES,
                    "type": "auto",
                    "contents": {"highlights": {"maxCharacters": _SEARCH_CHARS}},
                },
                "highlights",
                query,
            )
        return await self._request(
            "search",
            "/v1/search",
            {
                "search_queries": [query],
                "mode": "fast",
                "max_chars_total": _SEARCH_CHARS * MAX_WEB_SOURCES,
                "advanced_settings": {"max_results": MAX_WEB_SOURCES},
            },
            "excerpts",
            query,
        )

    async def read(self, url: str) -> WebEvidence:
        """Request excerpts for one safe-shaped URL, with no extra context or subpages."""
        if not allowed_public_url(url):
            return web_failure(self._provider, "read", "input_rejected")
        if self._provider == "exa":
            return await self._request(
                "read",
                "/contents",
                {
                    "ids": [url],
                    "text": {"maxCharacters": _READ_CHARS},
                },
                "text",
                url,
            )
        return await self._request(
            "read",
            "/v1/extract",
            {
                "urls": [url],
                "max_chars_total": _READ_CHARS,
                "advanced_settings": {
                    "excerpt_settings": {"max_chars_per_result": _READ_CHARS},
                    "full_content": False,
                },
            },
            "excerpts",
            url,
        )

    async def _request(
        self,
        operation: PublicWebOperation,
        path: str,
        body: dict[str, JsonValue],
        passage_field: str,
        public_input: str,
    ) -> WebEvidence:
        key = self._key()
        if not key:
            return web_failure(self._provider, operation, "unavailable")
        if key in public_input:
            return web_failure(self._provider, operation, "input_rejected")
        headers = {"x-api-key": key, "accept": "application/json", "accept-encoding": "identity"}
        payload = bytearray()
        try:
            async with (
                asyncio.timeout(_TIMEOUT),
                httpx.AsyncClient(
                    timeout=_TIMEOUT,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client,
                client.stream(
                    "POST", _ORIGINS[self._provider] + path, headers=headers, json=body
                ) as response,
            ):
                if response.status_code != 200:
                    return web_failure(self._provider, operation, "unavailable")
                mime = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if (
                    mime != "application/json"
                    or response.headers.get("content-encoding", "identity").lower() != "identity"
                ):
                    return web_failure(self._provider, operation, "invalid_response")
                async for chunk in response.aiter_raw(chunk_size=8192):
                    if len(payload) + len(chunk) > _WIRE_LIMIT:
                        return web_failure(self._provider, operation, "response_bound")
                    payload.extend(chunk)
        except (httpx.TimeoutException, TimeoutError):
            return web_failure(self._provider, operation, "timeout")
        except httpx.HTTPError:
            return web_failure(self._provider, operation, "unavailable")
        try:
            document = _OBJECT.validate_json(payload)
            return self._evidence(operation, document, passage_field, public_input, key)
        except ValueError:
            return web_failure(self._provider, operation, "invalid_response")

    def _evidence(
        self,
        operation: PublicWebOperation,
        document: dict[str, JsonValue],
        passage_field: str,
        public_input: str,
        key: str,
    ) -> WebEvidence:
        rows = document.get("results")
        if not isinstance(rows, list):
            return web_failure(self._provider, operation, "invalid_response")
        statuses = document.get("statuses")
        if operation == "read" and (
            document.get("errors")
            or (
                (statuses is not None or (self._provider == "exa" and "statuses" in document))
                and (
                    not isinstance(statuses, list)
                    or len(statuses) != 1
                    or not isinstance(statuses[0], dict)
                    or statuses[0].get("status") != "success"
                    or (self._provider == "exa" and statuses[0].get("id") != public_input)
                )
            )
        ):
            return web_failure(self._provider, operation, "unavailable")
        sources: dict[str, WebSource] = {}
        limit = 1 if operation == "read" else MAX_WEB_SOURCES
        truncated = len(rows) > limit
        for row in rows[:limit]:
            if not isinstance(row, dict):
                truncated = True
                continue
            url = row.get("url")
            if (
                not isinstance(url, str)
                or not allowed_public_url(url)
                or key in url
                or url in sources
                or (operation == "read" and httpx.URL(url) != httpx.URL(public_input))
            ):
                truncated = True
                continue
            source = _source(url, row, passage_field, key, operation)
            if source is None:
                truncated = True
                continue
            sources[url] = source
            truncated |= source.truncated
        if not sources and rows:
            return web_failure(self._provider, operation, "invalid_response")
        return WebEvidence(
            provider=self._provider,
            operation=operation,
            retrieved_at=datetime.now(UTC),
            coverage="partial" if sources else "empty",
            sources=tuple(sources.values()),
            truncated=truncated,
        )


def _source(
    url: str,
    row: dict[str, JsonValue],
    passage_field: str,
    key: str,
    operation: PublicWebOperation,
) -> WebSource | None:
    title = row.get("title")
    title = title.replace(key, "[redacted]")[:160] if isinstance(title, str) else ""
    content = row.get(passage_field)
    excerpts = [content] if isinstance(content, str) and passage_field == "text" else content
    if not isinstance(excerpts, list):
        return None
    passages: list[str] = []
    truncated = len(excerpts) > 3
    budget = _READ_CHARS if operation == "read" else _SEARCH_CHARS
    for excerpt in excerpts[:3]:
        if not isinstance(excerpt, str) or not excerpt.strip():
            truncated = True
            continue
        text = excerpt.replace(key, "[redacted]")
        encoded = text.encode()
        take = min(budget, MAX_WEB_PASSAGE)
        truncated |= len(encoded) >= take or "(content truncated)" in text.casefold()
        clipped = encoded[:take].decode(errors="ignore").strip()
        budget -= min(len(encoded), take)
        if clipped:
            passages.append(clipped)
    return (
        WebSource(url=url, title=title, passages=tuple(passages), truncated=truncated)
        if passages
        else None
    )
