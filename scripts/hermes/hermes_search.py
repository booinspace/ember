"""Invoke the installed Hermes SearXNG provider without loading its agent or model client."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from pydantic import JsonValue


class SearchProvider(Protocol):
    """The installed plugin's search-only interface."""

    def search(self, query: str, limit: int = 5) -> dict[str, JsonValue]: ...


def main() -> None:
    """Use explicit installation roots and an owned temporary home; emit one JSON result."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    packages = args.packages.resolve(strict=True)
    if not (source / "plugins/web/searxng/provider.py").is_file():
        raise ValueError("Install the qualified native Hermes SearXNG plugin first.")
    os.environ.update(
        HERMES_HOME=str(args.home.resolve(strict=True)),
        SEARXNG_URL=args.endpoint,
        PYTHONDONTWRITEBYTECODE="1",
    )
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(source), str(packages)]
    from pydantic import JsonValue, TypeAdapter

    raw = sys.stdin.buffer.readline(2049)
    request = TypeAdapter(dict[str, JsonValue]).validate_json(raw)
    query = request.get("query")
    if set(request) != {"query"} or not isinstance(query, str) or not 1 <= len(query) <= 400:
        raise ValueError("The search query exceeds its explicit scope.")
    logging.disable(logging.CRITICAL)
    module = importlib.import_module("plugins.web.searxng.provider")
    from hermes_client_transport import search_transport

    if not callable(getattr(module, "http_get_json", None)):
        raise ValueError("The installed native search transport is unsupported.")
    vars(module)["http_get_json"] = search_transport(args.endpoint)
    factory = cast(type[SearchProvider], module.SearXNGWebSearchProvider)
    result = factory().search(query, limit=3)
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > 131072:
        raise ValueError("The search result exceeds its bound.")
    sys.stdout.buffer.write(payload + b"\n")
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
