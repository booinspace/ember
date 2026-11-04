"""Bound the installed SearXNG plugin's HTTP seam without replacing its search implementation."""

from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from pydantic import JsonValue, TypeAdapter

_OBJECT = TypeAdapter(dict[str, JsonValue])


def search_transport(
    endpoint: str, *, authorize: Callable[[str], None] | None = None
) -> Callable[..., tuple[dict[str, JsonValue], None]]:
    """Allow only the installed search origin, without redirects, proxy or credentials."""
    origin = endpoint.rstrip("/")
    address = urlsplit(origin)
    if (
        address.scheme not in {"http", "https"}
        or not address.hostname
        or address.username is not None
        or address.password is not None
        or address.path
        or address.query
        or address.fragment
    ):
        raise ValueError("An explicit search service origin is required.")

    def get_json(label: str, url: str, **options: object) -> tuple[dict[str, JsonValue], None]:
        params = _OBJECT.validate_python(options.get("params"))
        query = params.get("q")
        if (
            label != "SearXNG"
            or url != origin + "/search"
            or set(params) != {"q", "format", "pageno"}
            or not isinstance(query, str)
            or not 1 <= len(query) <= 400
            or params.get("format") != "json"
            or type(params.get("pageno")) is not int
            or params["pageno"] != 1
        ):
            raise ValueError("The native search request exceeds its installed scope.")
        if authorize is not None:
            authorize(query)
        raw = bytearray()
        with (
            httpx.Client(trust_env=False, follow_redirects=False, timeout=4.0) as client,
            client.stream(
                "GET",
                url,
                params={"q": query, "format": "json", "pageno": 1},
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as response,
        ):
            if (
                response.status_code != 200
                or "application/json" not in response.headers.get("content-type", "")
                or response.headers.get("content-encoding", "identity") != "identity"
            ):
                raise ValueError("The installed search service is unavailable.")
            for part in response.iter_raw():
                raw.extend(part)
                if len(raw) > 131072:
                    raise ValueError("The native search response exceeds its wire budget.")
        document = _OBJECT.validate_json(raw)
        if not isinstance(document.get("results"), list):
            raise ValueError("The installed search service returned an invalid result.")
        return document, None

    return get_json
