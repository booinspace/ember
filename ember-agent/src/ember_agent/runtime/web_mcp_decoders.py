"""Trusted public MCP result formats, independent of transport and credentials."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from types import MappingProxyType
from typing import Annotated

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ..web_evidence import (
    MAX_WEB_SOURCES,
    PublicWebOperation,
    WebEvidence,
    WebSource,
    allowed_public_url,
    web_failure,
)
from .exa_web_text import exa_allows_input, exa_evidence

_Field = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")]


class StructuredResult(BaseModel):
    """Select source rows and their fields from structuredContent, never arbitrary text."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)

    rows: tuple[_Field, ...] = Field(default=(), max_length=4)
    url: _Field = "url"
    title: _Field = "title"
    passages: _Field = "excerpts"
    truncated: _Field | None = None


@dataclass(frozen=True, slots=True)
class ResultDecoder:
    """Decode bounded tool results without I/O or credentials.

    An optional guard only narrows valid inputs; decoders must match read evidence
    to the requested URL and treat excerpt text as untrusted data.
    """

    decode: Callable[[str, PublicWebOperation, str, dict[str, JsonValue]], WebEvidence]
    allows_input: Callable[[PublicWebOperation, str], bool] | None = None


_NAMED_DECODERS: Mapping[str, ResultDecoder] = MappingProxyType(
    {"exa_text": ResultDecoder(exa_evidence, allows_input=exa_allows_input)}
)


def resolve_decoder(result: StructuredResult | str) -> ResultDecoder:
    """Resolve a structured mapping or an installed decoder name; reject unknown names."""
    if isinstance(result, StructuredResult):
        return ResultDecoder(partial(_structured, mapping=result))
    decoder = _NAMED_DECODERS.get(result)
    if decoder is None:
        raise ValueError("Unknown public MCP result decoder")
    return decoder


def _structured(
    provider: str,
    operation: PublicWebOperation,
    value: str,
    result: dict[str, JsonValue],
    *,
    mapping: StructuredResult,
) -> WebEvidence:
    def invalid() -> WebEvidence:
        return web_failure(provider, operation, "invalid_response")

    data = result.get("structuredContent")
    for key in mapping.rows:
        data = data.get(key) if isinstance(data, dict) else None
    if not isinstance(data, list):
        return invalid()
    limit = 1 if operation == "read" else MAX_WEB_SOURCES
    if operation == "read" and len(data) > 1:
        return invalid()
    sources: dict[str, WebSource] = {}
    truncated = len(data) > limit
    for row in data[:limit]:
        if not isinstance(row, dict):
            return invalid()
        url, title, text = (
            row.get(mapping.url),
            row.get(mapping.title, ""),
            row.get(mapping.passages),
        )
        clipped = row.get(mapping.truncated, False) if mapping.truncated else False
        if (
            not isinstance(url, str)
            or not allowed_public_url(url)
            or url in sources
            or (operation == "read" and httpx.URL(url) != httpx.URL(value))
            or not isinstance(title, str)
            or type(clipped) is not bool
        ):
            return invalid()
        excerpts = [text] if isinstance(text, str) else text
        if not isinstance(excerpts, list) or not excerpts:
            return invalid()
        passages: list[str] = []
        budget = 2600 if operation == "read" else 900
        clipped |= len(title) > 160 or len(excerpts) > 3
        for item in excerpts[:3]:
            if not isinstance(item, str) or not item.strip():
                return invalid()
            encoded = item.encode()
            excerpt = encoded[:budget].decode(errors="ignore").strip()
            if excerpt:
                passages.append(excerpt)
            clipped |= len(encoded) >= budget
            budget = max(0, budget - len(encoded))
        sources[url] = WebSource(
            url=url, title=title[:160], passages=tuple(passages), truncated=clipped
        )
        truncated |= clipped
    return WebEvidence(
        provider=provider,
        operation=operation,
        retrieved_at=datetime.now(UTC),
        coverage="partial" if sources else "empty",
        sources=tuple(sources.values()),
        truncated=truncated,
    )
