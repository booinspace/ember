"""Exa's provider-specific public web text format, without transport."""

import re
from datetime import UTC, datetime

import httpx
from pydantic import JsonValue

from ..web_evidence import (
    PublicWebOperation,
    WebEvidence,
    WebFailure,
    WebSource,
    allowed_public_url,
    web_failure,
)

_SEARCH_BYTES = 900
_READ_BYTES = 2600
# Exa strips these directives from the literal query and turns them into tool filters.
_CATEGORY = re.compile(r"\bcategory:", re.I)
_SEARCH = re.compile(
    r"\ATitle: ([^\n]*)\nURL: ([^\n]+)\nPublished: [^\n]*\nAuthor: [^\n]*\n"
    r"(?:Highlights:\n|Text: )"
)
_PAGE = re.compile(r"\A# ([^\n]*)\nURL: ([^\n]+)\n(?:Published: [^\n]*\n)?(?:Author: [^\n]*\n)?\n")


def exa_allows_input(operation: PublicWebOperation, value: str) -> bool:
    """Reject search directives that Exa would reinterpret rather than send literally."""
    return operation != "search" or not _CATEGORY.search(value)


def exa_evidence(
    provider: str, operation: PublicWebOperation, value: str, result: dict[str, JsonValue]
) -> WebEvidence:
    """Decode the pinned Exa text format without trusting excerpt links."""

    def failed(code: WebFailure = "invalid_response") -> WebEvidence:
        return web_failure(provider, operation, code)

    blocks = result.get("content")
    if (
        not isinstance(blocks, list)
        or len(blocks) != 1
        or not isinstance(block := blocks[0], dict)
        or block.get("type") != "text"
        or not isinstance(text := block.get("text"), str)
    ):
        return failed()
    # ponytail: Exa's pinned source returns formatted text, not structured source rows.
    # Anchor one metadata header; never split page text into additional source receipts.
    text = text.replace("\r\n", "\n")
    if operation == "search" and text.startswith("Note: "):
        notices, _, text = text.partition("\n\n")
        if not all(line.startswith("Note: ") for line in notices.splitlines()):
            return failed()
    if text == (
        "No search results found. Please try a different query."
        if operation == "search"
        else "No content found for the provided URL(s)."
    ):
        return WebEvidence(
            provider=provider,
            operation=operation,
            retrieved_at=datetime.now(UTC),
            coverage="empty",
        )
    header = (_SEARCH if operation == "search" else _PAGE).match(text)
    if header is None:
        return failed()
    title, url = header.groups()
    if not allowed_public_url(url) or (operation == "read" and httpx.URL(url) != httpx.URL(value)):
        return failed()
    passage = text[header.end() :].strip()
    if operation == "search" and "\n\n---\n\nTitle: " in passage:
        return failed()
    if operation == "read" and re.search(r"(?:^|\n)Error fetching ", passage):
        return failed("unavailable")
    if operation == "read" and re.search(r"\n\n# [^\n]*\nURL: ", passage):
        return failed()
    if not passage:
        return failed()
    budget = _SEARCH_BYTES if operation == "search" else _READ_BYTES
    encoded = passage.encode()
    truncated = len(title) > 160 or len(encoded) >= budget
    return WebEvidence(
        provider=provider,
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
