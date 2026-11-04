"""Operator-owned direct MCP configuration and bounded discovery snapshots."""

import re
from pathlib import Path, PureWindowsPath
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class MCPModel(BaseModel):
    """Keep provider metadata and operator authority explicit and immutable."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class MCPToolGrant(MCPModel):
    """An operator classification; server annotations never grant invocation authority."""

    name: Annotated[str, Field(min_length=1, max_length=128)]
    effect: Literal["read", "write"]
    input_schema_sha256: Annotated[str | None, Field(pattern=r"^[a-f0-9]{64}$")] = None


class MCPServerSettings(MCPModel):
    """One explicitly installed trusted service, with references rather than credential values."""

    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,23}$")]
    transport: Literal["stdio", "http"]
    enabled: bool = True
    command: Annotated[str, Field(max_length=4096)] = ""
    arguments: tuple[Annotated[str, Field(max_length=4096)], ...] = Field(default=(), max_length=32)
    working_directory: str = ""
    environment: dict[str, str] = Field(default_factory=dict, repr=False, max_length=16)
    url: Annotated[str, Field(max_length=2048)] = ""
    bearer_token_environment: str = ""
    grants: tuple[MCPToolGrant, ...] = Field(default=(), max_length=64)
    timeout_seconds: Annotated[float, Field(gt=0, le=5, allow_inf_nan=False)] = 5.0

    @model_validator(mode="after")
    def coherent_service(self) -> Self:
        """Reject shell launchers, insecure credential origins and ambiguous grant declarations."""
        if len({grant.name for grant in self.grants}) != len(self.grants):
            raise ValueError("Duplicate MCP tool grant")
        references = (*self.environment.keys(), *self.environment.values())
        if self.bearer_token_environment:
            references += (self.bearer_token_environment,)
        if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key) is None for key in references):
            raise ValueError("Invalid MCP environment reference")
        if self.transport == "stdio":
            if not (
                Path(self.command).is_absolute() or PureWindowsPath(self.command).is_absolute()
            ):
                raise ValueError("MCP requires an absolute installed executable")
            executable = PureWindowsPath(self.command).name.casefold()
            if executable in {"sh", "bash", "zsh", "cmd.exe", "powershell.exe", "pwsh.exe"}:
                raise ValueError("MCP shell launchers are unsupported")
            if self.url or self.bearer_token_environment:
                raise ValueError("HTTP settings on a stdio MCP service")
        else:
            origin = urlsplit(self.url)
            if (
                origin.scheme not in {"https", "http"}
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.fragment
                or origin.query
                or (
                    origin.scheme == "http"
                    and origin.hostname not in {"localhost", "127.0.0.1", "::1"}
                )
            ):
                raise ValueError("MCP HTTP requires HTTPS or a literal loopback origin")
            if self.command or self.arguments or self.working_directory or self.environment:
                raise ValueError("Stdio settings on an HTTP MCP service")
        return self


class MCPToolDefinition(MCPModel):
    """Bounded metadata with a policy/config/schema-bound stable invocation identity."""

    id: Annotated[str, Field(max_length=64)]
    server_id: str
    name: str
    description: Annotated[str, Field(max_length=2048)] = ""
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] | None = None
    annotations: dict[str, JsonValue] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)
    timeout_seconds: float = 5.0
    idempotency: Literal["never_retry"] = "never_retry"
    platforms: tuple[str, ...] = ()
    requirements: tuple[str, ...] = ()
    effect: Literal["read", "write", "denied"]
    available: bool
    metadata_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    reason: str | None = None


class MCPServerStatus(MCPModel):
    """Content-free service readiness; failure of one service does not disable another."""

    id: str
    status: Literal["ready", "disabled", "unavailable", "missing_credentials", "invalid_metadata"]
    tools: int = Field(default=0, ge=0)
    requirements: tuple[str, ...] = ()


class MCPDiscovery(MCPModel):
    """Operator-visible discovery includes denied metadata without treating it as a grant."""

    servers: tuple[MCPServerStatus, ...]
    tools: tuple[MCPToolDefinition, ...]
