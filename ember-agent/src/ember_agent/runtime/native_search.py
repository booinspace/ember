"""Bounded public search through an installed command or SearXNG service."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from pydantic import JsonValue, TypeAdapter

from ..inference.contracts import GatewayError
from .searx_tools import SearchQuery, SearXNGTools, plain, public_url
from .task_store import TaskConflict
from .worker_process import owned_stdio

_OBJECT = TypeAdapter(dict[str, JsonValue])


def _validate_query(query: str) -> None:
    if not query.strip() or len(query) > 400:
        raise GatewayError("public_search_query_bound")


class SearXNGSearch:
    """Send only an explicit query to a configured origin, without fetching results' URLs."""

    def __init__(self, endpoint: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.endpoint = endpoint
        self.transport = transport

    async def __call__(self, query: str) -> str:
        _validate_query(query)
        try:
            async with (
                asyncio.timeout(4),
                httpx.AsyncClient(
                    timeout=4.0, trust_env=False, follow_redirects=False, transport=self.transport
                ) as client,
            ):
                rows = await SearXNGTools(self.endpoint, client=client).search(
                    SearchQuery(query=query)
                )
        except (httpx.HTTPError, ValueError, TimeoutError, TaskConflict) as error:
            raise GatewayError("public_search_unavailable") from error
        return _bounded_context(rows)


class NativeSearch:
    """Run an installed search command with owned lifetime and copied-context bounds."""

    def __init__(self, command: tuple[str, ...], root: Path) -> None:
        self.command = command
        self.root = root

    async def __call__(self, query: str) -> str:
        _validate_query(query)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="search-", dir=self.root) as home:
                async with asyncio.timeout(18):
                    async with owned_stdio((*self.command, "--home", home)) as process:
                        assert process.stdin is not None and process.stdout is not None
                        process.stdin.write(
                            json.dumps({"query": query}, ensure_ascii=False).encode() + b"\n"
                        )
                        await process.stdin.drain()
                        process.stdin.close()
                        raw = await process.stdout.readline()
                        if len(raw) > 131072 or await process.stdout.read(1):
                            raise GatewayError("public_search_result_bound")
                        if await process.wait() != 0:
                            raise GatewayError("public_search_unavailable")
                        return copy_results(_OBJECT.validate_json(raw))
        except (OSError, ValueError, TimeoutError, TaskConflict) as error:
            raise GatewayError("public_search_unavailable") from error


def copy_results(value: dict[str, JsonValue]) -> str:
    """Copy only whole public reference rows from the native provider's result DTO."""
    data = value.get("data")
    rows = data.get("web") if isinstance(data, dict) else None
    if value.get("success") is not True or not isinstance(rows, list):
        raise GatewayError("public_search_unavailable")
    normalized: list[JsonValue] = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        url, title, description = row.get("url"), row.get("title"), row.get("description", "")
        if (
            not isinstance(url, str)
            or not public_url(url)
            or not isinstance(title, str)
            or not isinstance(description, str)
        ):
            continue
        normalized.append(
            {"url": url, "title": plain(title, 128), "snippet": plain(description, 256)}
        )
    return _bounded_context(normalized)


def _bounded_context(rows: list[JsonValue]) -> str:
    copied: list[JsonValue] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item: JsonValue = {name: row[name] for name in ("url", "title", "snippet")}
        candidate = json.dumps({"results": [*copied, item]}, ensure_ascii=False)
        if len(candidate.encode()) <= 2048:
            copied.append(item)
    return json.dumps({"results": copied}, ensure_ascii=False)
