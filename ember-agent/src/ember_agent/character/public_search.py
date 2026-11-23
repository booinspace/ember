"""Recognize an explicit public search request without a model or personal recall."""

import re

from ..inference.contracts import GatewayError
from ..public_web import WebBinding
from ..web_evidence import PublicWebOperation

_REQUEST = re.compile(
    r"^\s*(?:(?:please|can you|could you|would you|will you)\s+)*"
    r"(?:search(?:\s+(?:google|the web|online|the internet))?|google|look\s+up)"
    r"\s+(?:for\s+)?(?P<query>.+)$",
    re.IGNORECASE | re.DOTALL,
)


def public_search_query(text: str) -> str | None:
    """Return only a literal user-supplied query; never derive terms from private context."""
    match = _REQUEST.fullmatch(text)
    if match is None:
        return None
    query = match.group("query").rstrip()
    if not query or len(query) > 400:
        raise GatewayError("public_search_query_bound")
    return query


PAGE_ACTION = r"(?:summari[sz]e|read|explain|give me (?:a )?summary of)"
_PAGE_REQUEST = re.compile(
    rf"^\s*(?:(?:please|can you|could you|would you|will you)\s+)*{PAGE_ACTION}\b",
    re.IGNORECASE,
)

_EXPLICIT = re.compile(
    r"^\s*([a-z0-9](?:[a-z0-9 ._()\t-]*[a-z0-9._()-])?)[ \t]+(search|read):[ \t]*(.*)$",
    re.I | re.S,
)
_LEGACY_PROVIDERS = frozenset({"exa", "exa mcp", "exa_mcp", "parallel"})


def _provider_name(value: str) -> str:
    return " ".join(value.casefold().split())


def matches_web_provider(provider: str, binding: WebBinding | None) -> bool:
    """Match a normalized shortcut name against the binding's ID or label."""
    return binding is not None and provider in (binding.provider, _provider_name(binding.label))


def hosted_web_request(
    text: str, *bindings: WebBinding | None
) -> tuple[str, PublicWebOperation, str] | None:
    """Recognize configured shortcuts; without bindings, recognize only legacy names."""
    match = _EXPLICIT.fullmatch(text)
    if match is None:
        return None
    provider = _provider_name(match[1])
    if provider not in _LEGACY_PROVIDERS and not any(
        matches_web_provider(provider, binding) for binding in bindings
    ):
        return None
    return (
        provider,
        ("search" if match[2].casefold() == "search" else "read"),
        match[3].strip(" \t"),
    )


def public_page_requested(text: str) -> bool:
    """Recognize read attempts even when the explicit URL is ambiguous or unsupported."""
    return (
        _PAGE_REQUEST.match(text) is not None
        and re.search(r"\b[a-z][a-z0-9+.-]*://", text, re.IGNORECASE) is not None
    )
