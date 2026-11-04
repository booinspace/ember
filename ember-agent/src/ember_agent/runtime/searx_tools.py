"""One installed SearXNG origin, bounded public search results and native tool receipts."""

import html
import re
import socket
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx
from pydantic import Field, JsonValue, TypeAdapter

from .config import RuntimeModel
from .task_router import ToolContext, ToolReceipt
from .task_store import TaskConflict

JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class SearchQuery(RuntimeModel):
    """A query cannot choose the endpoint, credentials or response budget."""

    query: str = Field(min_length=1, max_length=512)


def public_url(value: str) -> bool:
    """Accept public HTTP references without credentials or local resource authority."""
    try:
        address = urlsplit(value)
        hostname = (address.hostname or "").rstrip(".").lower()
        if (
            len(value) > 512
            or address.scheme not in {"http", "https"}
            or address.username is not None
            or address.password is not None
            or not hostname
            or address.port is not None
            and not 1 <= address.port <= 65535
            or any(ord(char) < 33 for char in value)
        ):
            return False
        try:
            return ip_address(hostname).is_global
        except ValueError:
            try:
                # inet_aton recognizes historical short/octal/hex IPv4 spellings too.
                return ip_address(socket.inet_aton(hostname)).is_global
            except OSError:
                return (
                    "." in hostname
                    and hostname != "localhost"
                    and not hostname.endswith((".localhost", ".local", ".internal"))
                    and re.fullmatch(r"[a-z0-9.-]+", hostname) is not None
                )
    except ValueError:
        return False


def plain(value: str, limit: int) -> str:
    """Search markup remains data in both the model context and plain-text UI."""
    return html.unescape(re.sub(r"<[^>]*>", "", value[: limit * 4]))[:limit]


class SearXNGTools:
    """Read-only installed service; no result URL is fetched and no shell is exposed."""

    capabilities = frozenset({"searx_search"})

    def __init__(self, endpoint: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.client = client or httpx.AsyncClient(
            timeout=4.0, trust_env=False, follow_redirects=False
        )

    async def invoke(
        self, name: str, arguments: dict[str, JsonValue], context: ToolContext
    ) -> ToolReceipt:
        """Bound wire, schema and returned passages before issuing an actor-bound receipt."""
        if name not in self.capabilities:
            raise TaskConflict("tool_unavailable")
        query = SearchQuery.model_validate(arguments)
        payload = bytearray()
        async with self.client.stream(
            "GET",
            self.endpoint + "/search",
            params={"q": query.query, "format": "json"},
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as response:
            if (
                response.status_code != 200
                or "application/json" not in response.headers.get("content-type", "")
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise TaskConflict("search_unavailable")
            async for chunk in response.aiter_raw():
                payload.extend(chunk)
                if len(payload) > 131072:
                    raise TaskConflict("search_wire_limit")
        document = JSON_OBJECT.validate_json(payload)
        raw = document.get("results")
        if not isinstance(raw, list):
            raise TaskConflict("search_invalid_results")
        results: list[JsonValue] = []
        for item in raw[:50]:
            if not isinstance(item, dict):
                continue
            url, title, content = item.get("url"), item.get("title"), item.get("content", "")
            if (
                isinstance(url, str)
                and public_url(url)
                and isinstance(title, str)
                and isinstance(content, str)
            ):
                results.append(
                    {
                        "id": "s" + str(len(results) + 1),
                        "url": url,
                        "title": plain(title, 128),
                        "snippet": plain(content, 256),
                    }
                )
            if len(results) == 3:
                break
        return ToolReceipt(context=context, tool=name, data={"results": results})

    async def aclose(self) -> None:
        """Close only the transport this provider owns."""
        await self.client.aclose()
