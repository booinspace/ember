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


_PAGE_REQUEST = re.compile(
    r"^\s*(?:(?:please|can you|could you|would you|will you)\s+)*"
    r"(?:summari[sz]e|read|explain|give me (?:a )?summary of)\b",
    re.IGNORECASE,
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
