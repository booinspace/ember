"""Dedicated keyless Streamable HTTP client for two fixed Exa tools."""

import asyncio
import re
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import JsonValue, TypeAdapter

from ..web_evidence import (
    PublicWebOperation,
    WebEvidence,
    WebFailure,
    WebSource,
    allowed_public_url,
    permitted_public_query,
    web_failure,
)

_PROVIDER = "exa_mcp"
_ENDPOINT = "https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa"
_VERSION = "2025-06-18"
_TIMEOUT = 8.0
_WIRE_LIMIT = 131_072
_SEARCH_BYTES = 900
_READ_BYTES = 2600
_HEADERS = {"accept": "application/json, text/event-stream", "accept-encoding": "identity"}
_OBJECT = TypeAdapter(dict[str, JsonValue])
_FRAME_END = re.compile(rb"\r?\n\r?\n")
# MCP 2025-06-18 session IDs are opaque visible ASCII; cap reflected header size.
_SESSION_ID = re.compile(r"[\x21-\x7e]{1,1024}")
# Exa strips these directives from the literal query and turns them into tool filters.
_CATEGORY = re.compile(r"\bcategory:", re.I)
_SEARCH = re.compile(
    r"\ATitle: ([^\n]*)\nURL: ([^\n]+)\nPublished: [^\n]*\nAuthor: [^\n]*\n"
    r"(?:Highlights:\n|Text: )"
)
_PAGE = re.compile(r"\A# ([^\n]*)\nURL: ([^\n]+)\n(?:Published: [^\n]*\n)?(?:Author: [^\n]*\n)?\n")


class _Failed(Exception):
    def __init__(self, code: WebFailure) -> None:
        self.code: WebFailure = code
        super().__init__(code)


class ExaMCP:
    """Use one short-lived keyless MCP connection per validated retrieval; no discovery."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def search(self, query: str) -> WebEvidence:
        """Send one literal approved query, requesting a single search result."""
        if not permitted_public_query(query) or _CATEGORY.search(query):
            return web_failure(_PROVIDER, "search", "input_rejected")
        return await self._retrieve("search", query)

    async def read(self, url: str) -> WebEvidence:
        """Send one validated public HTTP(S) URL, never a list from the caller."""
        if not allowed_public_url(url):
            return web_failure(_PROVIDER, "read", "input_rejected")
        return await self._retrieve("read", url)

    async def _retrieve(self, operation: PublicWebOperation, value: str) -> WebEvidence:
        try:
            async with (
                asyncio.timeout(_TIMEOUT),
                httpx.AsyncClient(
                    transport=self._transport
                    if self._transport is not None
                    else httpx.AsyncHTTPTransport(local_address="0.0.0.0", trust_env=False),
                    follow_redirects=False,
                    trust_env=False,
                    timeout=_TIMEOUT,
                ) as client,
            ):
                text = await _call(client, operation, value)
            return _evidence(operation, value, text)
        except _Failed as error:
            return web_failure(_PROVIDER, operation, error.code)
        except (TimeoutError, httpx.TimeoutException):
            return web_failure(_PROVIDER, operation, "timeout")
        except httpx.HTTPError:
            return web_failure(_PROVIDER, operation, "unavailable")
        except ValueError:
            return web_failure(_PROVIDER, operation, "invalid_response")


async def _call(client: httpx.AsyncClient, operation: PublicWebOperation, value: str) -> str:
    async with client.stream(
        "POST",
        _ENDPOINT,
        headers=_HEADERS,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ember-public-web", "version": "1"},
            },
        },
    ) as response:
        initial = await _result(response, 1)
        session_ids = response.headers.get_list("mcp-session-id")
    capabilities = initial.get("capabilities")
    if (
        initial.get("protocolVersion") != _VERSION
        or not isinstance(capabilities, dict)
        or not isinstance(capabilities.get("tools"), dict)
        or len(session_ids) > 1
        or (session_ids and not _SESSION_ID.fullmatch(session_ids[0]))
    ):
        raise _Failed("invalid_response")
    headers = {**_HEADERS, "mcp-protocol-version": _VERSION}
    if session_ids:
        headers["mcp-session-id"] = session_ids[0]

    # Exa's hosted handler is stateless per request. No GET stream, resumption, or discovery.
    client.cookies.clear()
    async with client.stream(
        "POST",
        _ENDPOINT,
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    ) as response:
        _require_status(response, 202)
    client.cookies.clear()
    name = "web_search_exa" if operation == "search" else "web_fetch_exa"
    arguments = (
        {"query": value, "numResults": 1}
        if operation == "search"
        else {"urls": [value], "maxCharacters": _READ_BYTES}
    )
    async with client.stream(
        "POST",
        _ENDPOINT,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    ) as response:
        result = await _result(response, 2)
    if result.get("isError") is True:
        raise _Failed("tool_error")
    content = result.get("content")
    if (
        result.get("isError", False) is not False
        or not isinstance(content, list)
        or len(content) != 1
        or not isinstance(content[0], dict)
        or content[0].get("type") != "text"
        or not isinstance(text := content[0].get("text"), str)
    ):
        raise _Failed("invalid_response")
    return text


def _require_status(response: httpx.Response, expected: int) -> None:
    if response.status_code in {401, 403}:
        raise _Failed("access_denied")
    if response.status_code == 429:
        raise _Failed("rate_limited")
    if response.status_code != expected:
        raise _Failed("unavailable")


async def _result(response: httpx.Response, request_id: Literal[1, 2]) -> dict[str, JsonValue]:
    _require_status(response, 200)
    mime = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if (
        mime not in {"application/json", "text/event-stream"}
        or response.headers.get("content-encoding", "identity").lower() != "identity"
    ):
        raise _Failed("invalid_response")
    raw = bytearray()
    scanned = offset = 0
    async for chunk in response.aiter_raw():
        if len(raw) + len(chunk) > _WIRE_LIMIT:
            raise _Failed("response_bound")
        raw.extend(chunk)
        if mime == "text/event-stream":
            while match := _FRAME_END.search(raw, scanned):
                fields = raw[offset : match.start()].splitlines()
                data = [line[5:].removeprefix(b" ") for line in fields if line.startswith(b"data:")]
                if data and any(data):
                    # Stop on the one JSON-RPC response, not when the server closes its stream.
                    return _message(b"\n".join(data), request_id)
                scanned = offset = match.end()
            scanned = max(offset, len(raw) - 3)
    if mime == "application/json":
        return _message(bytes(raw), request_id)
    raise _Failed("invalid_response")


def _message(payload: bytes, request_id: int) -> dict[str, JsonValue]:
    message = _OBJECT.validate_json(payload)
    received_id = message.get("id")
    if message.get("jsonrpc") != "2.0" or type(received_id) is not int or received_id != request_id:
        raise _Failed("invalid_response")
    if "error" in message and "result" not in message:
        raise _Failed("unavailable")
    result = message.get("result")
    if "error" in message or not isinstance(result, dict):
        raise _Failed("invalid_response")
    return result


def _evidence(operation: PublicWebOperation, value: str, text: str) -> WebEvidence:
    # ponytail: Exa's pinned source returns formatted text, not structured source rows.
    # Anchor one metadata header; never split page text into additional source receipts.
    text = text.replace("\r\n", "\n")
    if operation == "search" and text.startswith("Note: "):
        notices, _, text = text.partition("\n\n")
        if not all(line.startswith("Note: ") for line in notices.splitlines()):
            raise _Failed("invalid_response")
    if text == (
        "No search results found. Please try a different query."
        if operation == "search"
        else "No content found for the provided URL(s)."
    ):
        return WebEvidence(
            provider=_PROVIDER,
            operation=operation,
            retrieved_at=datetime.now(UTC),
            coverage="empty",
        )
    header = (_SEARCH if operation == "search" else _PAGE).match(text)
    if header is None:
        raise _Failed("invalid_response")
    title, url = header.groups()
    if not allowed_public_url(url) or (operation == "read" and httpx.URL(url) != httpx.URL(value)):
        raise _Failed("invalid_response")
    passage = text[header.end() :].strip()
    if operation == "search" and "\n\n---\n\nTitle: " in passage:
        raise _Failed("invalid_response")
    if operation == "read" and re.search(r"(?:^|\n)Error fetching ", passage):
        raise _Failed("unavailable")
    if operation == "read" and re.search(r"\n\n# [^\n]*\nURL: ", passage):
        raise _Failed("invalid_response")
    if not passage:
        raise _Failed("invalid_response")
    budget = _SEARCH_BYTES if operation == "search" else _READ_BYTES
    encoded = passage.encode()
    truncated = len(title) > 160 or len(encoded) >= budget
    return WebEvidence(
        provider=_PROVIDER,
        operation=operation,
        retrieved_at=datetime.now(UTC),
        coverage="partial",
        sources=(
            WebSource(
                url=url,
                title=title[:160],
                passages=(encoded[:budget].decode(errors="ignore").strip(),),
                truncated=truncated,
            ),
        ),
        truncated=truncated,
    )
