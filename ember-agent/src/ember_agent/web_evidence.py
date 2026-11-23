"""Bounded public web evidence, without conversation or task authority."""

import re
from datetime import UTC, datetime
from typing import Literal, Protocol, Self
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

PublicWebOperation = Literal["search", "read"]
WebFailure = Literal[
    "input_rejected",
    "timeout",
    "unavailable",
    "response_bound",
    "invalid_response",
    "access_denied",
    "rate_limited",
    "tool_error",
]
MAX_WEB_SOURCES = 3
MAX_WEB_QUERY = 400
MAX_WEB_URL = 512
MAX_WEB_PASSAGE = 3072

_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", re.ASCII)
_PATH = re.compile(r"[a-zA-Z0-9/._~!$&'()*+,-]*", re.ASCII)
_SECRET_PATH = re.compile(
    r"(?:^|[/._~-])(?:access[_-]?token|api[_-]?key|auth|invite|login|oauth|private|"
    r"password|reset|secret|session|share|signed|token)(?:$|[/._~=-])|"
    r"[a-z0-9_-]{40,}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}",
    re.IGNORECASE,
)
_QUERY_LINK = re.compile(
    r"[a-z][a-z0-9+.-]*:/{1,2}|(?<!\w)//|\b[\w.-]+\.[a-z]{2,63}[/@?#]|"
    r"(?<!\w)(?:~|\.{1,2})?/[\w.-]|[\\%]",
    re.IGNORECASE,
)

# Shared with the local turn planner: a provider must not accept forms it would decline.
PUBLIC_CONTEXT_REFERENCE = re.compile(
    r"\b(?:we discussed|we talked about|from (?:earlier|above|my notes|memory)|"
    r"from (?:our|my) (?:chat|history)|(?:based on|using)\s+(?:this|that|it|what you|what we))\b",
    re.I,
)
PRIVATE_INPUT = re.compile(
    r"(?<![a-z])(?:private|internal|confidential|secret|pass(?:word|code)|api[ _-]?key|"
    r"token|credentials?)(?![a-z])|\b(?:my|our) (?:files?|notes?|chat|history|account|address)\b|"
    r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|[a-z0-9_-]{40,}|[\x00-\x1f\x7f]",
    re.I,
)
_AMBIGUOUS_QUERY = re.compile(
    r"\b(?:do not|don['’]t|never|instead|without searching|this info|this information|"
    r"that info|the above|what (?:I|we|you) (?:said|found|know))\b|"
    r"\b(?:and then|then please|and (?:tell|show|send|give|include) me)\b|"
    r"[;\"“”<>`]|\b\d{1,3}(?:\.\d{1,3}){3}\b|"
    r"\b[\w.-]+\.(?:localhost|local|internal|home|lan|nas|corp|intranet)\b",
    re.I,
)


def allowed_public_url(value: str) -> bool:
    """Only credential-free public-shaped HTTP(S) URLs; do not resolve or fetch locally.

    Query strings, fragments, IPs, private names/ports, encoded and credential-like
    paths are deliberately unsupported. Syntax cannot prove a URL is public.
    """
    if not 1 <= len(value) <= MAX_WEB_URL or not value.isascii():
        return False
    if any(not 33 <= ord(char) <= 126 or char in "?#\\%" for char in value):
        return False
    try:
        address = urlsplit(value)
        host = address.hostname or ""
        return (
            address.scheme in {"http", "https"}
            and address.netloc.casefold() == host
            and len(host) <= 253
            and _HOST.fullmatch(host) is not None
            and not host.endswith(
                (
                    ".localhost",
                    ".local",
                    ".internal",
                    ".home",
                    ".lan",
                    ".nas",
                    ".corp",
                    ".intranet",
                    ".onion",
                    ".arpa",
                    ".invalid",
                    ".test",
                    ".example",
                )
            )
            and _PATH.fullmatch(address.path) is not None
            and not _SECRET_PATH.search(address.path)
        )
    except ValueError:
        return False


def allowed_public_query(value: str) -> bool:
    """Permit one bounded literal search line, not URLs or attachments."""
    return (
        1 <= len(value) <= MAX_WEB_QUERY
        and bool(value.strip())
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
        and len(value.splitlines()) == 1
        and not _QUERY_LINK.search(value)
    )


def permitted_public_query(value: str) -> bool:
    """Reject known private and referential forms; syntax cannot certify public data."""
    return (
        len(value.encode()) <= MAX_WEB_QUERY
        and allowed_public_query(value)
        and not (
            PRIVATE_INPUT.search(value)
            or PUBLIC_CONTEXT_REFERENCE.search(value)
            or _AMBIGUOUS_QUERY.search(value)
        )
    )


class WebSource(BaseModel):
    """Provider-returned URL with extracted passages, not a provider-generated answer."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    url: str = Field(max_length=MAX_WEB_URL)
    title: str = Field(max_length=160, repr=False)
    passages: tuple[str, ...] = Field(min_length=1, max_length=3, repr=False)
    truncated: bool = False

    @field_validator("url")
    @classmethod
    def _public_url(cls, value: str) -> str:
        if not allowed_public_url(value):
            raise ValueError("Unsupported public URL.")
        return value

    @field_validator("passages")
    @classmethod
    def _passage_bound(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not passage.strip() or len(passage.encode()) > MAX_WEB_PASSAGE for passage in value):
            raise ValueError("Invalid public passage.")
        return value


class WebEvidence(BaseModel):
    """Retrieval receipt. Partial always means excerpts, never complete page coverage."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    operation: PublicWebOperation
    retrieved_at: AwareDatetime
    coverage: Literal["partial", "empty", "unavailable"]
    sources: tuple[WebSource, ...] = Field(default=(), max_length=MAX_WEB_SOURCES, repr=False)
    truncated: bool = False
    snippets: bool = False
    failure: WebFailure | None = None

    @model_validator(mode="after")
    def _consistent_coverage(self) -> Self:
        if (
            bool(self.sources) != (self.coverage == "partial")
            or (self.failure is not None) != (self.coverage == "unavailable")
            or (self.operation == "read" and len(self.sources) > 1)
            or len({source.url for source in self.sources}) != len(self.sources)
            or (any(source.truncated for source in self.sources) and not self.truncated)
        ):
            raise ValueError("Inconsistent public evidence coverage.")
        return self


class PublicWebProvider(Protocol):
    """One selected retrieval adapter; caller authorizes each public input explicitly."""

    async def search(self, query: str) -> WebEvidence:
        """Retrieve bounded source passages for one literal query."""
        ...

    async def read(self, url: str) -> WebEvidence:
        """Retrieve bounded passages for one explicitly supplied public URL."""
        ...


def web_failure(provider: str, operation: PublicWebOperation, failure: WebFailure) -> WebEvidence:
    """Build a fixed diagnostic without including query, URL or upstream body."""
    return WebEvidence(
        provider=provider,
        operation=operation,
        retrieved_at=datetime.now(UTC),
        coverage="unavailable",
        failure=failure,
    )
