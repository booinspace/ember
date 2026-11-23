"""Offline, declarative bindings for public MCP retrieval (not direct MCP grants)."""

import hashlib
import json
import re
from typing import Annotated, Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .web_mcp_decoders import StructuredResult, resolve_decoder

ProviderId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
BindingRevision = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_Field = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")]
_StaticText = Annotated[str, Field(pattern=r"^[\x20-\x7e]{1,128}$")]


def binding_revision(*parts: object) -> str:
    """Stable revision over non-secret configuration; never hash credential values here."""
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class _Settings(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)


class MCPWebBinding(_Settings):
    """An operator grants one exact HTTPS endpoint and tool for one public capability."""

    provider: ProviderId
    label: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,47}$")]
    endpoint: Annotated[str, Field(min_length=1, max_length=2048, repr=False)]
    tool: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]
    bearer_token_env: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")] | None = None
    ipv4: bool = False
    input_field: _Field
    input_as_list: bool = False
    fixed: dict[_Field, _StaticText | int | bool] = Field(default_factory=dict, max_length=12)
    result: StructuredResult | str

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        url = urlsplit(self.endpoint)
        host = url.hostname or ""
        # Query options (e.g. Exa tool selection) belong to reviewed config, not runtime inputs.
        if (
            not self.endpoint.startswith("https://")
            or not re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", host)
            or host.endswith((".localhost", ".local", ".internal", ".lan", ".home", ".nas"))
            or url.netloc.lower() != host
            or not url.path.startswith("/")
            or any(ord(char) < 33 or ord(char) > 126 or char in "#\\" for char in self.endpoint)
        ):
            raise ValueError("Public MCP requires an explicit HTTPS hostname and endpoint path")
        try:
            canonical = str(httpx.URL(self.endpoint))
        except httpx.InvalidURL:
            raise ValueError("Invalid public MCP endpoint") from None
        if self.endpoint != canonical:
            raise ValueError("Public MCP endpoint must match its HTTPX canonical URL")
        if self.input_field in self.fixed or "_meta" in (self.input_field, *self.fixed):
            raise ValueError("Public MCP argument mapping overlaps a fixed or reserved field")
        resolve_decoder(self.result)
        return self


class PublicMCPSettings(_Settings):
    """At most one binding per operation; opting in does not expose other MCP tools."""

    search: MCPWebBinding | None = None
    read: MCPWebBinding | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.search is None and self.read is None:
            raise ValueError("Public MCP requires a search or read binding")
        if self.search and self.read:
            if (self.search.provider == self.read.provider) != (
                self.search.label.casefold() == self.read.label.casefold()
            ):
                raise ValueError("Public provider IDs and labels must agree")
        return self
