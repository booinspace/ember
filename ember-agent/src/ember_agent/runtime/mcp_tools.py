"""Direct MCP adapter: scoped credentials, bounded wire data and no model/tool replay."""

import asyncio
import hashlib
import hmac
import json
import os
import tempfile
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from time import time
from typing import cast
from uuid import UUID

from jsonschema import Draft202012Validator
from mcp import Client
from pydantic import JsonValue, TypeAdapter

from .mcp_client import MCPClient, call_once
from .mcp_contracts import (
    MCPDiscovery,
    MCPServerSettings,
    MCPServerStatus,
    MCPToolDefinition,
)
from .task_router import ToolContext, ToolReceipt
from .task_store import TaskConflict

WIRE_BYTES = 65536
RESULT_BYTES = 3000
JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


def _safe_failure(error: BaseException) -> str | None:
    if isinstance(error, TaskConflict):
        return str(error)
    if isinstance(error, BaseExceptionGroup):
        for nested in cast(BaseExceptionGroup[BaseException], error).exceptions:
            if code := _safe_failure(nested):
                return code
    return None


def _encoded(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _schema(value: JsonValue, *, arguments: bool) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or (arguments and value.get("type") != "object"):
        raise TaskConflict("mcp_invalid_schema")
    if len(_encoded(value)) > 16384:
        raise TaskConflict("mcp_schema_too_large")

    allowed = {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "description",
        "title",
        "default",
        "$schema",
        "$id",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
    nodes = 0

    def check(item: dict[str, JsonValue], depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 12 or nodes > 256 or set(item) - allowed:
            raise TaskConflict("mcp_schema_profile_unsupported")
        properties = item.get("properties", {})
        if not isinstance(properties, dict) or len(properties) > 64:
            raise TaskConflict("mcp_schema_profile_unsupported")
        for name, child in properties.items():
            if len(name) > 128 or not isinstance(child, dict):
                raise TaskConflict("mcp_schema_profile_unsupported")
            check(child, depth + 1)
        required = item.get("required", [])
        if not isinstance(required, list) or len(required) > 64:
            raise TaskConflict("mcp_schema_profile_unsupported")
        if any(not isinstance(name, str) or len(name) > 128 for name in required):
            raise TaskConflict("mcp_schema_profile_unsupported")
        for key in ("items", "additionalProperties"):
            child = item.get(key)
            if isinstance(child, dict):
                check(child, depth + 1)
        enum = item.get("enum")
        if isinstance(enum, list) and len(enum) > 32:
            raise TaskConflict("mcp_schema_profile_unsupported")

    check(value, 0)
    Draft202012Validator.check_schema(value)
    return value


def _valid(schema: dict[str, JsonValue], value: object) -> bool:
    validator = Draft202012Validator(schema)
    # jsonschema's deprecated optional second argument is untyped in its stub.
    validate = cast(Callable[[object], bool], validator.is_valid)  # pyright: ignore[reportUnknownMemberType]
    return validate(value)


class MCPTools:
    """Configured direct tools bound to exactly one runtime character and session."""

    def __init__(
        self, settings: tuple[MCPServerSettings, ...], character_id: UUID, session_id: UUID
    ) -> None:
        if len(settings) > 8 or len({server.id for server in settings}) != len(settings):
            raise TaskConflict("mcp_server_configuration_invalid")
        self.settings, self.character_id, self.session_id = settings, character_id, session_id
        self._key = os.urandom(32)
        self._snapshot = MCPDiscovery(servers=(), tools=())
        self._closed = False
        self._calls: set[asyncio.Task[object]] = set()
        self._refresh = asyncio.Lock()

    @property
    def capabilities(self) -> frozenset[str]:
        if self._closed:
            return frozenset()
        return frozenset(tool.id for tool in self._snapshot.tools if tool.available)

    def snapshot(self) -> MCPDiscovery:
        """Return detached metadata; neither UI nor callers can mutate invocation policy."""
        return MCPDiscovery.model_validate_json(self._snapshot.model_dump_json())

    def definition(self, identity: str) -> MCPToolDefinition:
        for tool in self._snapshot.tools:
            if tool.id == identity and tool.available and not self._closed:
                return MCPToolDefinition.model_validate_json(tool.model_dump_json())
        raise TaskConflict("mcp_tool_unavailable")

    def authority_tag(self, identity: str) -> str:
        """Bind copied result access to known metadata, local grants and current credentials."""
        definition = self.definition(identity)
        server = next(server for server in self.settings if server.id == definition.server_id)
        _, _, tag = self._credentials(server)
        return hmac.new(
            self._key,
            _encoded([definition.metadata_sha256, server.model_dump(mode="json"), tag]),
            hashlib.sha256,
        ).hexdigest()

    def _credentials(self, settings: MCPServerSettings) -> tuple[dict[str, str], str | None, str]:
        environment = {
            "PATH": str(Path(settings.command).parent),
            "HOME": "",
            "USERPROFILE": "",
            "TEMP": tempfile.gettempdir(),
            "TMP": tempfile.gettempdir(),
        }
        for key in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR"):
            if key in os.environ:
                environment[key] = os.environ[key]
        for target, reference in settings.environment.items():
            value = os.environ.get(reference)
            if value is None or len(value) > 4096 or "\x00" in value:
                raise TaskConflict("mcp_missing_credentials")
            environment[target] = value
        bearer = (
            os.environ.get(settings.bearer_token_environment)
            if settings.bearer_token_environment
            else None
        )
        if settings.bearer_token_environment and (
            not bearer or len(bearer) > 4096 or "\n" in bearer or "\r" in bearer
        ):
            raise TaskConflict("mcp_missing_credentials")
        # A private keyed fingerprint revokes old IDs on rotation without exposing a password hash.
        tag = hmac.new(self._key, _encoded([environment, bearer]), hashlib.sha256).hexdigest()
        return environment, bearer, tag

    @asynccontextmanager
    async def _client(
        self, settings: MCPServerSettings
    ) -> AsyncGenerator[tuple[Client, str], None]:
        environment, bearer, tag = self._credentials(settings)
        async with MCPClient().direct(settings, environment, bearer) as client:
            yield client, tag

    async def _listing(
        self, client: Client, settings: MCPServerSettings, tag: str
    ) -> tuple[MCPToolDefinition, ...]:
        tools: list[MCPToolDefinition] = []
        cursor: str | None = None
        cursors: set[str] = set()
        names: set[str] = set()
        for _ in range(16):
            page = await client.list_tools(cursor=cursor, cache_mode="bypass")
            for native in page.tools:
                raw = JSON_OBJECT.validate_json(
                    native.model_dump_json(by_alias=True, exclude_none=True)
                )
                if len(_encoded(raw)) > 24576 or len(tools) >= 64:
                    raise TaskConflict("mcp_metadata_limit")
                name = raw.get("name")
                if not isinstance(name, str) or not name or len(name) > 128 or name in names:
                    raise TaskConflict("mcp_tool_identity_invalid")
                names.add(name)
                inputs = _schema(raw.get("inputSchema"), arguments=True)
                output = raw.get("outputSchema")
                outputs = _schema(output, arguments=False) if output is not None else None
                grant = next((grant for grant in settings.grants if grant.name == name), None)
                schema_hash = hashlib.sha256(_encoded(inputs)).hexdigest()
                allowed = grant is not None and (
                    grant.input_schema_sha256 is None or grant.input_schema_sha256 == schema_hash
                )
                description = raw.get("description", "")
                annotations = raw.get("annotations", {})
                extensions = raw.get("_meta", {})
                if (
                    not isinstance(description, str)
                    or len(description) > 2048
                    or not isinstance(annotations, dict)
                    or not isinstance(extensions, dict)
                ):
                    raise TaskConflict("mcp_metadata_invalid")
                requirements_value = extensions.get("requirements", [])
                if not isinstance(requirements_value, list) or len(requirements_value) > 16:
                    raise TaskConflict("mcp_requirements_invalid")
                requirements: list[str] = []
                for requirement in requirements_value:
                    if not isinstance(requirement, str) or len(requirement) > 128:
                        raise TaskConflict("mcp_requirements_invalid")
                    requirements.append(requirement)
                unsupported = raw.get("execution") is not None
                if unsupported:
                    allowed = False
                    requirements.append("unsupported_execution")
                if grant is None:
                    requirements.append("operator_grant")
                elif grant.effect == "write":
                    requirements.append("confirmation")
                digest = hashlib.sha256(
                    _encoded([settings.model_dump(mode="json"), raw, tag])
                ).hexdigest()
                tools.append(
                    MCPToolDefinition(
                        id="mcp." + settings.id + "." + digest[:24],
                        server_id=settings.id,
                        name=name,
                        description=description,
                        input_schema=inputs,
                        output_schema=outputs,
                        annotations=annotations,
                        extensions=extensions,
                        timeout_seconds=settings.timeout_seconds,
                        effect=grant.effect if grant else "denied",
                        available=allowed,
                        metadata_sha256=digest,
                        requirements=tuple(requirements),
                        reason=None
                        if allowed
                        else "unsupported_execution"
                        if unsupported
                        else "schema_pin_mismatch"
                        if grant
                        else "not_granted",
                    )
                )
            cursor = page.next_cursor
            if cursor is None:
                return tuple(tools)
            if cursor in cursors or len(cursor) > 1024:
                raise TaskConflict("mcp_pagination_invalid")
            cursors.add(cursor)
        raise TaskConflict("mcp_pagination_limit")

    async def start(self) -> None:
        """Discover configured services without granting their advertised capabilities."""
        await self.refresh()

    async def refresh(self) -> None:
        """Own discovery through cancellation and never publish metadata after shutdown."""
        task = asyncio.current_task()
        if task is not None:
            self._calls.add(task)
        try:
            await self._discover()
        finally:
            if task is not None:
                self._calls.discard(task)

    async def _discover(self) -> None:
        async with self._refresh:
            if self._closed:
                raise TaskConflict("mcp_provider_closed")

            async def discover(
                server: MCPServerSettings,
            ) -> tuple[MCPServerStatus, tuple[MCPToolDefinition, ...]]:
                if not server.enabled:
                    return MCPServerStatus(id=server.id, status="disabled"), ()
                try:
                    async with asyncio.timeout(server.timeout_seconds):
                        async with self._client(server) as (client, tag):
                            tools = await self._listing(client, server, tag)
                    return MCPServerStatus(id=server.id, status="ready", tools=len(tools)), tools
                except Exception as error:
                    code = _safe_failure(error)
                    invalid = bool(
                        code
                        and code.startswith(
                            (
                                "mcp_schema",
                                "mcp_metadata",
                                "mcp_pagination",
                                "mcp_requirements",
                                "mcp_tool_identity",
                                "mcp_invalid_schema",
                                "mcp_invalid_frame",
                                "mcp_message_limit",
                                "mcp_wire_limit",
                            )
                        )
                    )
                    return MCPServerStatus(
                        id=server.id,
                        status="missing_credentials"
                        if code == "mcp_missing_credentials"
                        else "invalid_metadata"
                        if invalid
                        else "unavailable",
                    ), ()

            results = await asyncio.gather(*(discover(server) for server in self.settings))
            if self._closed:
                raise TaskConflict("mcp_provider_closed")
            self._snapshot = MCPDiscovery(
                servers=tuple(status for status, _ in results),
                tools=tuple(tool for _, tools in results for tool in tools),
            )

    async def invoke(
        self, name: str, arguments: dict[str, JsonValue], context: ToolContext
    ) -> ToolReceipt:
        """Recheck exact metadata and actor before sending one non-retried tool request."""
        definition = self.definition(name)
        if context.character_id != self.character_id or context.session_id != self.session_id:
            raise TaskConflict("mcp_scope_denied")
        if len(_encoded(arguments)) > 8192 or not _valid(definition.input_schema, arguments):
            raise TaskConflict("mcp_arguments_invalid")
        server = next(server for server in self.settings if server.id == definition.server_id)
        remaining = (
            min(server.timeout_seconds, context.deadline_at - time())
            if context.deadline_at
            else server.timeout_seconds
        )
        if remaining <= 0:
            return ToolReceipt(
                context=context,
                tool=name,
                outcome="timeout",
                code="mcp_deadline_exceeded",
                data={},
                dispatched=False,
            )
        dispatched = False
        task = asyncio.current_task()
        if task is not None:
            self._calls.add(task)
        try:
            async with asyncio.timeout(remaining):
                async with self._client(server) as (client, tag):
                    current = await self._listing(client, server, tag)
                    if not any(tool.id == name and tool.available for tool in current):
                        raise TaskConflict("mcp_metadata_changed")
                    dispatched = True
                    # The high-level call_tool retries schema/input-required rounds; this does not.
                    raw = await call_once(client.session, definition.name, arguments, remaining)
                    if len(_encoded(raw)) > RESULT_BYTES or raw.get("isError") is True:
                        raise TaskConflict("mcp_tool_failed")
                    if definition.output_schema is not None and not _valid(
                        definition.output_schema, raw.get("structuredContent")
                    ):
                        raise TaskConflict("mcp_output_invalid")
                    return ToolReceipt(
                        context=context, tool=name, data={"result": raw}, dispatched=True
                    )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return ToolReceipt(
                context=context,
                tool=name,
                outcome="unknown_effect"
                if dispatched and definition.effect == "write"
                else "timeout",
                code="mcp_timeout",
                data={},
                dispatched=dispatched,
            )
        except Exception as error:
            return ToolReceipt(
                context=context,
                tool=name,
                outcome="unknown_effect"
                if dispatched and definition.effect == "write"
                else "failure",
                code=_safe_failure(error) or "mcp_invocation_failed",
                data={},
                dispatched=dispatched,
            )
        finally:
            if task is not None:
                self._calls.discard(task)

    async def aclose(self) -> None:
        """Stop owned clients/processes only; another provider or inference server is untouched."""
        self._closed = True
        self._snapshot = MCPDiscovery(
            servers=tuple(
                MCPServerStatus(id=server.id, status="disabled") for server in self.settings
            ),
            tools=tuple(
                tool.model_copy(update={"available": False, "reason": "provider_closed"})
                for tool in self._snapshot.tools
            ),
        )
        calls = tuple(task for task in self._calls if task is not asyncio.current_task())
        for task in calls:
            task.cancel()
        await asyncio.gather(*calls, return_exceptions=True)
