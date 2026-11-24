"""Recognize an explicit public search request without a model or personal recall."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..inference.contracts import GatewayError
from ..public_web import WebBinding
from ..web_evidence import (
    MAX_WEB_URL,
    PRIVATE_INPUT,
    PUBLIC_CONTEXT_REFERENCE,
    PublicWebOperation,
    allowed_public_url,
)

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


PAGE_ACTION = r"(?:summari[sz]e|read|fetch|open|explain|give me (?:a )?summary of)"
_PAGE_REQUEST = re.compile(
    rf"(?:(?:hey[, ]+)?ember[, ]+|(?:sick|ok(?:ay)?)[, ]+(?:but +)?|"
    rf"(?:please|can you|could you|would you|will you|i want you to)\s+)*"
    rf"(?P<action>{PAGE_ACTION}|what(?:['’]?s|\s+is)\s+on)\b\s+(?P<target>.+)",
    re.I,
)
_DOMAIN = r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63}"
_LINK = rf"(?:(?:[a-zA-Z][a-zA-Z0-9+.-]*://)[^\s<>\[\]]+|{_DOMAIN}(?:[/@?#:%][^\s<>\[\]]*)?)"
_MARKDOWN_LINK = re.compile(r"\[[^\]\r\n]{0,512}\]\((?P<url>[^\s<>]+)\)[?.!]?$")
_PLAIN_LINK = re.compile(rf"(?:<(?P<wrapped>{_LINK})>[?.!]?|(?P<bare>{_LINK}))$")
_LOOKS_LINK = re.compile(rf"://|(?<![\w.-]){_DOMAIN}(?![\w.-])", re.I)
_PAGE_OBJECT = re.compile(
    r"(?:(?:the (?:data|contents) (?:from|of) )?(?:this|the)\s+(?:page|article|link|url)"
    r"(?: at)?|(?:this|the) (?:[\w'’-]+ ){1,6}(?:profile|page|music))?"
    r"(?:\s+and\s+(?:tell me what you think|give me your thoughts|summari[sz]e it))?"
    r"[ :]*",
    re.I,
)
_OPINION = re.compile(
    r"\s+(?:and\s+)?(?:tell me what you think|give me your thoughts|summari[sz]e it)[?.!]*$",
    re.I,
)
_PROFILE = re.compile(
    r"(?:my|the) (?P<subject>[\w.-]+(?: [\w.-]+){0,4}) profile(?:[,?.! ].*)?$", re.I
)
_PAGE_UNSAFE = re.compile(
    r"\b(?:if|unless|whether|until|before|after|without|instead|not|never|or|"
    r"don['’]?t|how|why|would|could|should|pretend|example|instructions?|process|request)\b",
    re.I,
)
# ponytail: bare filenames need a page label or scheme before they can grant web access.
_LOCAL_SUFFIX = re.compile(r"[\w.-]+\.(?:txt|md|py|toml|json|yaml|yml|ts|js|html|css)", re.I)
_FROM_HERE = re.compile(r"^from here[\s!,:]+", re.I)

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


@dataclass(frozen=True)
class PageRequest:
    url: str | None = None
    provider: str | None = None
    subject: str | None = None
    invalid: bool = False


def page_link(text: str) -> str | None:
    """Accept one standalone link, optionally answering a request for its location."""
    target = _FROM_HERE.sub("", text.strip(), count=1)
    start, url = _terminal_link(target)
    return url if start == 0 else None


def _terminal_link(text: str) -> tuple[int, str | None]:
    match = _MARKDOWN_LINK.search(text) or _PLAIN_LINK.search(text)
    if match is None:
        return len(text), None
    candidate = next(value for value in match.groups() if value is not None)
    if len(candidate) > MAX_WEB_URL:
        return match.start(), None
    if "://" not in candidate:
        candidate = "https://" + candidate
    return match.start(), candidate if allowed_public_url(candidate) else None


def page_request(text: str, binding: WebBinding | None = None) -> PageRequest | None:
    """Validate affirmative page targets independently of a model's proposal."""
    value = text.strip()
    if not 0 < len(value.encode()) <= 1800 or len(value.splitlines()) != 1:
        return None
    if value.casefold().startswith("use "):
        providers = _LEGACY_PROVIDERS
        if binding is not None:
            providers |= {_provider_name(binding.label), binding.provider}
        for provider in sorted(providers, key=len, reverse=True):
            named = re.match(rf"use\s+{re.escape(provider)}\s+(?P<target>.+)", value, re.I)
            if named is not None:
                target = _OPINION.sub("", named["target"])
                if re.match(r"search\b", target, re.I):
                    return None
                start, url = _terminal_link(target)
                if start == 0 and url is not None:
                    return PageRequest(url, provider)
                return PageRequest(provider=provider, invalid=True)
        return None
    match = _PAGE_REQUEST.fullmatch(value)
    if match is None:
        return None
    target = match["target"]
    if _LOCAL_SUFFIX.fullmatch(target):
        return None
    profile = _PROFILE.fullmatch(target)
    if profile and not (
        _PAGE_UNSAFE.search(target) or PRIVATE_INPUT.search(target) or "://" in target
    ):
        return PageRequest(subject=profile["subject"])
    if not _LOOKS_LINK.search(target):
        if _PAGE_OBJECT.fullmatch(target.rstrip("?.!")):
            return PageRequest()
        return None
    target = _OPINION.sub("", target)
    start, url = _terminal_link(target)
    label = " ".join(target[:start].split())
    earlier_link = _LOOKS_LINK.search(label)
    if _PAGE_UNSAFE.search(label, 0, earlier_link.start() if earlier_link else len(label)):
        return None
    if (
        _PAGE_OBJECT.fullmatch(label) is None
        or PRIVATE_INPUT.search(label)
        or PUBLIC_CONTEXT_REFERENCE.search(label)
    ):
        return PageRequest(invalid=True)
    return PageRequest(url, invalid=url is None)


def search_has_link(text: str) -> bool:
    """Keep URL targets out of public search."""
    if _LOOKS_LINK.search(text) is None:
        return False
    return bool(
        _REQUEST.fullmatch(text)
        or re.match(r"\s*use\s+(?:exa(?:[ _]mcp)?|parallel)\s+search\b", text, re.I)
    )


def profile_links(subject: str, quote: str) -> set[str]:
    """Select exact public URLs whose host matches the requested profile platform."""
    result: set[str] = set()
    platform = subject.casefold().split(".", 1)[0].replace(" ", "")
    if not re.search(r"\bprofile\b", quote, re.I):
        return result
    for match in re.finditer(r"https?://[^\s<>\[\]\"”]+", quote):
        url = match[0]
        # A Markdown or prose wrapper can supply punctuation outside the URL.
        url = url.rstrip(".,;")
        if url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        if allowed_public_url(url):
            host = urlsplit(url).hostname or ""
            if host.removeprefix("www.").split(".", 1)[0] == platform:
                result.add(url)
    return result
