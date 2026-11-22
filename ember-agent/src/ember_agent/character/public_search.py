"""Recognize an explicit public search request without a model or personal recall."""

import re

from ..inference.contracts import GatewayError

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

_HOSTED_REQUEST = re.compile(
    r"^\s*(exa[ \t]+mcp|exa|parallel)[ \t]+(search|read):[ \t]*(.*)$", re.I | re.S
)


def hosted_web_request(text: str) -> tuple[str, str, str] | None:
    """Require a provider-named action and a literal, standalone input."""
    match = _HOSTED_REQUEST.fullmatch(text)
    if match is None:
        return None
    return "_".join(match[1].lower().split()), match[2].lower(), match[3].strip(" \t")


def public_page_requested(text: str) -> bool:
    """Recognize read attempts even when the explicit URL is ambiguous or unsupported."""
    return (
        _PAGE_REQUEST.match(text) is not None
        and re.search(r"\b[a-z][a-z0-9+.-]*://", text, re.IGNORECASE) is not None
    )


def public_page_url(text: str) -> str | None:
    """Fetch only one literal URL in an explicit read request, never recalled/model-derived text."""
    if not _PAGE_REQUEST.match(text):
        return None
    # A Markdown link's label is display text; only its literal destination is requested.
    destinations = re.sub(r"\[[^\]\n]*\]\(\s*(?=<?https?://)", "", text)
    urls = re.findall(r"https?://[^\s<>]+", destinations)
    if len(urls) != 1:
        return None
    url = urls[0].rstrip(".,!?;")
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1]
    return url
