"""Bounded public search through an explicit installed Hermes provider command."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import JsonValue, TypeAdapter

from ..inference.contracts import GatewayError
from .searx_tools import plain, public_url
from .task_store import TaskConflict
from .worker_process import owned_stdio

_OBJECT = TypeAdapter(dict[str, JsonValue])


class NativeSearch:
    """Reuse the native plugin; Ember retains process ownership and copied-context bounds."""

    def __init__(self, command: tuple[str, ...], root: Path) -> None:
        self.command = command
        self.root = root

    async def __call__(self, query: str) -> str:
        if not query.strip() or len(query) > 400:
            raise GatewayError("public_search_query_bound")
        self.root.mkdir(parents=True, exist_ok=True)
        try:
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
    copied: list[JsonValue] = []
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
        item: JsonValue = {
            "url": url,
            "title": plain(title, 128),
            "snippet": plain(description, 256),
        }
        candidate = json.dumps({"results": [*copied, item]}, ensure_ascii=False)
        if len(candidate.encode()) <= 2048:
            copied.append(item)
    return json.dumps({"results": copied}, ensure_ascii=False)
