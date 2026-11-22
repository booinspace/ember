"""Bounded task-local OpenAI mapping; every generation uses the existing P2 gateway client."""

import asyncio
import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from time import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, SchemaError
from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from ..observability import notify
from ..runtime.config import DeploymentConfig, RuntimeModel
from .contracts import (
    GatewayError,
    GatewayRequest,
    GenerationUsage,
    ModelClient,
    PromptCallId,
    PromptMessage,
    PromptToolCall,
    ToolCall,
    ToolDefinition,
    ToolFunction,
)

_OBJECT = TypeAdapter(dict[str, JsonValue])
_LIMIT = 1024 * 1024


def sse(value: dict[str, JsonValue]) -> bytes:
    """Encode one OpenAI SSE data frame."""
    return b"data: " + json.dumps(value, allow_nan=False).encode() + b"\n\n"


def openai_error(code: str) -> dict[str, JsonValue]:
    """Return only fixed, content-free gateway diagnostics."""
    return {"error": {"message": code, "type": "gateway_error", "code": code}}


def openai_tool(call: ToolCall) -> dict[str, JsonValue]:
    """Encode a complete gateway-validated tool call."""
    return {
        "id": str(call.call_id),
        "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments, allow_nan=False)},
    }


def openai_usage(usage: GenerationUsage) -> dict[str, JsonValue]:
    """Encode terminal token counts."""
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.output_tokens,
        "total_tokens": usage.prompt_tokens + usage.output_tokens,
    }


def validate_model_schema(schema: dict[str, JsonValue], *, literal_prefix: bool = False) -> None:
    """Allow bounded schemas without references or general regex."""
    if len(json.dumps(schema, allow_nan=False).encode()) > 16384:
        raise GatewayError("unsupported_model_schema")
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

    def check(value: dict[str, JsonValue], depth: int) -> None:
        nonlocal nodes
        nodes += 1
        extra = set(value) - allowed
        pattern = value.get("pattern")
        # ponytail: Accept literal prefixes (e.g. OpenCode session IDs), not arbitrary regex.
        if (
            literal_prefix
            and isinstance(pattern, str)
            and re.fullmatch(r"\^[A-Za-z0-9_-]{1,64}", pattern)
        ):
            extra.discard("pattern")
        if depth > 12 or nodes > 256 or extra:
            raise GatewayError("unsupported_model_schema")
        properties = value.get("properties", {})
        required = value.get("required", [])
        if not isinstance(properties, dict) or len(properties) > 64:
            raise GatewayError("unsupported_model_schema")
        if (
            not isinstance(required, list)
            or len(required) > 64
            or any(not isinstance(name, str) or len(name) > 128 for name in required)
        ):
            raise GatewayError("unsupported_model_schema")
        for name, child in properties.items():
            if len(name) > 128 or not isinstance(child, dict):
                raise GatewayError("unsupported_model_schema")
            check(child, depth + 1)
        for key in ("items", "additionalProperties"):
            child = value.get(key)
            if isinstance(child, dict):
                check(child, depth + 1)
        enum = value.get("enum")
        if isinstance(enum, list) and len(enum) > 32:
            raise GatewayError("unsupported_model_schema")

    check(schema, 0)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise GatewayError("unsupported_model_schema") from error


class _Function(RuntimeModel):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
    arguments: Annotated[str, Field(max_length=65536)] = Field(repr=False)


class _Call(RuntimeModel):
    id: PromptCallId
    type: Literal["function"] = "function"
    function: _Function


class _TextPart(RuntimeModel):
    type: Literal["text"]
    text: Annotated[str, Field(max_length=100000)] = Field(repr=False)


class _Message(RuntimeModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Annotated[str, Field(max_length=100000)] | tuple[_TextPart, ...] | None = Field(
        default=None, repr=False
    )
    tool_calls: Annotated[tuple[_Call, ...], Field(max_length=16)] | None = None
    tool_call_id: PromptCallId | None = None


class _Schema(RuntimeModel):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    schema_value: dict[str, JsonValue] = Field(alias="schema")
    strict: bool | None = None


class _Format(RuntimeModel):
    type: Literal["text", "json_object", "json_schema"]
    json_schema: _Schema | None = None


class _StreamOptions(RuntimeModel):
    include_usage: bool = False


class _ToolFunction(ToolFunction):
    strict: Literal[False] | None = None


class _ToolDefinition(RuntimeModel):
    type: Literal["function"] = "function"
    function: _ToolFunction


class _Request(RuntimeModel):
    model: str
    messages: Annotated[tuple[_Message, ...], Field(min_length=1, max_length=64)]
    tools: Annotated[tuple[_ToolDefinition, ...], Field(max_length=16)] | None = None
    tool_choice: Literal["auto", "none"] | None = None
    parallel_tool_calls: bool | None = None
    max_tokens: Annotated[int, Field(gt=0, le=8192)] | None = None
    max_completion_tokens: Annotated[int, Field(gt=0, le=8192)] | None = None
    temperature: Annotated[float, Field(ge=0, le=2, allow_inf_nan=False)] | None = None
    response_format: _Format | None = None
    stream: bool = False
    stream_options: _StreamOptions | None = None


class OpenAIModelBridge:
    """One provider task owns this mapper; it does not own a listener, runner, or credentials."""

    def __init__(
        self,
        client: ModelClient,
        config: DeploymentConfig,
        *,
        context_id: UUID,
        deadline_at: str,
        observe_request: Callable[[GatewayRequest], None] | None = None,
        coding: bool = False,
    ) -> None:
        self.client = client
        self.config = config
        self.coding = coding
        self.output_limit = min(config.output_reserve_tokens, 8192 if coding else 1192)
        self.input_limit = min(
            config.max_input_tokens,
            config.context_tokens - max(1192, config.output_reserve_tokens if coding else 1192),
            config.context_tokens if coding else 16384,
        )
        self.context_id = context_id
        self.deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
        if self.deadline.tzinfo is None:
            raise ValueError("The model bridge requires an explicit task deadline.")
        self.calls = 0
        self.running = False
        self.observe_request = observe_request
        self.observation_failures = 0
        self.last_input_units = 0
        self.last_output_units = 0

    def models(self) -> dict[str, JsonValue]:
        """Report actual runner capacity separately from this task's stricter prompt budget."""
        return {
            "object": "list",
            "data": [
                {
                    "id": self.config.model_tag,
                    "object": "model",
                    "created": 0,
                    "owned_by": "ember",
                    "context_length": self.config.context_tokens,
                    "max_input_tokens": self.input_limit,
                    "max_output_tokens": self.output_limit,
                }
            ],
        }

    def _request(self, body: bytes | dict[str, JsonValue]) -> tuple[_Request, GatewayRequest]:
        """Validate OpenAI input and bind it to the configured model and workload."""
        try:
            raw = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode()
            if len(raw) > _LIMIT:
                raise GatewayError("request_too_large")
            value = _Request.model_validate_json(raw)
            if value.model != self.config.model_tag:
                raise GatewayError("model_not_allowlisted")
            if value.temperature is not None and value.temperature != 0.2:
                raise GatewayError("unsupported_model_options")
            if value.max_tokens is not None and value.max_completion_tokens is not None:
                raise GatewayError("conflicting_output_budget")
            tool_names = [tool.function.name for tool in value.tools or ()]
            if len(tool_names) != len(set(tool_names)):
                raise GatewayError("invalid_openai_request")
            tools: list[ToolDefinition] = []
            for tool in value.tools or ():
                function = tool.function
                if function.parameters.get("type") != "object":
                    raise GatewayError("unsupported_model_schema")
                validate_model_schema(function.parameters, literal_prefix=self.coding)
                tools.append(
                    ToolDefinition(
                        function=ToolFunction(
                            name=function.name,
                            description=function.description,
                            parameters=function.parameters,
                        )
                    )
                )
            maximum = value.max_completion_tokens or value.max_tokens or self.output_limit
            if maximum > self.output_limit:
                raise GatewayError("output_budget_exceeded")
            messages: list[PromptMessage] = []
            for item in value.messages:
                if item.role != "assistant" and item.content is None:
                    raise GatewayError("invalid_tool_transcript")
                if item.role == "tool" and item.tool_call_id is None:
                    raise GatewayError("invalid_tool_transcript")
                messages.append(
                    PromptMessage(
                        role=item.role,
                        content=(
                            "\n".join(part.text for part in item.content)
                            if isinstance(item.content, tuple)
                            else item.content or ""
                        ),
                        tool_call_id=item.tool_call_id,
                        tool_calls=tuple(
                            PromptToolCall(
                                call_id=call.id,
                                name=call.function.name,
                                arguments=_OBJECT.validate_json(call.function.arguments),
                            )
                            for call in item.tool_calls or ()
                        ),
                    )
                )
            output: Literal["text", "json"] = "text"
            schema: dict[str, JsonValue] | None = None
            if value.response_format is not None:
                format_ = value.response_format
                if format_.type == "json_schema":
                    if format_.json_schema is None:
                        raise GatewayError("invalid_output_schema")
                    schema = format_.json_schema.schema_value
                elif format_.json_schema is not None:
                    raise GatewayError("invalid_output_schema")
                elif format_.type == "json_object":
                    schema = {"type": "object"}
                output = "text" if format_.type == "text" else "json"
            if schema is not None:
                validate_model_schema(schema)
            deadline = min(self.deadline, datetime.now(UTC) + timedelta(seconds=180))
            request = GatewayRequest(
                version="0.3.0",
                request_id=uuid4(),
                context_id=self.context_id,
                model=self.config.model_tag,
                workload="P5" if self.coding else "P2",
                messages=tuple(messages),
                max_output_tokens=maximum,
                output=output,
                output_schema=schema,
                tools=() if value.tool_choice == "none" else tuple(tools),
                deadline_at=deadline.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            )
            serialized = json.dumps(
                {
                    "messages": [message.model_dump(mode="json") for message in request.messages],
                    "tools": [tool.model_dump(mode="json") for tool in request.tools],
                },
                ensure_ascii=False,
                allow_nan=False,
            )
            cost = len(serialized.encode()) + 512 + 64 * len(request.messages)
            if cost > self.input_limit:
                raise GatewayError("prompt_overflow")
            self.last_input_units = cost
            return value, request
        except (ValidationError, ValueError) as error:
            raise GatewayError("invalid_openai_request") from error

    def response(
        self, body: bytes
    ) -> Awaitable[dict[str, JsonValue]] | AsyncGenerator[bytes, None]:
        """Select OpenAI JSON or SSE after validating transport flags."""
        value, request = self._request(body)
        if not value.stream:
            return self.complete(body)
        return self._stream(value, request)

    def _stream(self, value: _Request, request: GatewayRequest) -> AsyncGenerator[bytes, None]:
        """Emit only gateway-validated tools."""
        envelope: dict[str, JsonValue] = {
            "id": "chatcmpl-" + str(request.request_id),
            "object": "chat.completion.chunk",
            "created": int(time()),
            "model": self.config.model_tag,
        }

        def chunk(delta: dict[str, JsonValue], finish: str | None = None) -> bytes:
            return sse(
                {**envelope, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            )

        async def events() -> AsyncGenerator[bytes, None]:
            calls: tuple[ToolCall, ...] = ()
            try:
                async with aclosing(self.client.stream(request)) as source:
                    async for event in source:
                        if event.kind == "start":
                            yield chunk({"role": "assistant"})
                        elif event.kind == "progress":
                            yield b": queued\n\n"
                        elif event.kind == "text":
                            yield chunk({"content": event.text})
                        elif event.kind == "tools":
                            calls = event.calls
                        elif event.kind == "end" and event.usage is not None:
                            if value.parallel_tool_calls is False and len(calls) > 1:
                                raise GatewayError("unexpected_parallel_tool_calls")
                            for index, call in enumerate(calls):
                                yield chunk({"tool_calls": [{"index": index, **openai_tool(call)}]})
                            reason = (
                                "length" if event.truncated else "tool_calls" if calls else "stop"
                            )
                            yield chunk({}, reason)
                            if value.stream_options and value.stream_options.include_usage:
                                yield sse(
                                    {**envelope, "choices": [], "usage": openai_usage(event.usage)}
                                )
                            yield b"data: [DONE]\n\n"
                            return
                        elif event.kind == "error":
                            raise GatewayError(event.code or "generation_failed")
                raise GatewayError("gateway_stream_incomplete")
            except GatewayError as error:
                yield sse(openai_error(error.code))

        return events()

    async def complete(
        self,
        body: bytes | dict[str, JsonValue],
        *,
        tool_policy: Callable[[tuple[ToolDefinition, ...]], tuple[ToolDefinition, ...]]
        | None = None,
    ) -> dict[str, JsonValue]:
        """Return one complete response; cancellation detaches the existing owned gateway."""
        value, request = self._request(body)
        original_units = self.last_input_units
        if tool_policy is not None:
            original = request
            selected = tool_policy(request.tools)
            names = [tool.function.name for tool in selected]
            advertised = {tool.function.name for tool in request.tools}
            if len(names) != len(set(names)) or not set(names) <= advertised:
                raise GatewayError("invalid_host_tool_policy")
            projected = value.model_copy(update={"tools": selected})
            value, request = self._request(projected.model_dump_json(by_alias=True).encode())
            request = request.model_copy(
                update={"request_id": original.request_id, "deadline_at": original.deadline_at}
            )
        if self.running:
            raise GatewayError("model_bridge_busy")
        if self.calls >= 12:
            raise GatewayError("model_call_limit")
        remaining = (self.deadline - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            raise GatewayError("deadline_exceeded")
        self.calls += 1
        self.last_input_units = max(original_units, self.last_input_units)
        self.running = True
        self.observation_failures += not notify(self.observe_request, request)
        stream = self.client.stream(request)
        content: list[str] = []
        size = 0
        calls: tuple[ToolCall, ...] = ()
        usage: GenerationUsage | None = None
        truncated = False
        sequence = 0
        try:
            async with asyncio.timeout(min(remaining, 180)):
                async for event in stream:
                    if (
                        event.request_id != request.request_id
                        or event.context_id != self.context_id
                    ):
                        raise GatewayError("gateway_stream_identity")
                    if event.sequence != sequence or (sequence == 0 and event.kind != "start"):
                        raise GatewayError("gateway_stream_order")
                    if sequence > 0 and event.kind == "start":
                        raise GatewayError("gateway_stream_order")
                    sequence += 1
                    if event.kind == "error":
                        raise GatewayError(event.code or "generation_failed")
                    if event.kind == "thinking":
                        raise GatewayError("unexpected_model_reasoning")
                    if event.kind == "text":
                        size += len(event.text.encode())
                        if size > _LIMIT:
                            raise GatewayError("gateway_frame_too_large")
                        content.append(event.text)
                    if event.kind == "tools":
                        if calls or len(event.calls) > 16:
                            raise GatewayError("invalid_tool_calls")
                        calls = event.calls
                    if event.kind == "end":
                        usage = event.usage
                        truncated = event.truncated
                        break
            if usage is None:
                raise GatewayError("gateway_stream_incomplete")
            if value.parallel_tool_calls is False and len(calls) > 1:
                raise GatewayError("unexpected_parallel_tool_calls")
            definitions = {tool.function.name for tool in request.tools}
            if any(call.name not in definitions for call in calls):
                raise GatewayError("unrequested_tool")
            message: dict[str, JsonValue] = {"role": "assistant", "content": "".join(content)}
            if calls:
                message["tool_calls"] = [openai_tool(call) for call in calls]
            self.last_output_units = len(json.dumps(message, ensure_ascii=False).encode()) + 64
            return {
                "id": "chatcmpl-" + str(request.request_id),
                "object": "chat.completion",
                "created": int(time()),
                "model": self.config.model_tag,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "length"
                        if truncated
                        else "tool_calls"
                        if calls
                        else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.output_tokens,
                    "total_tokens": usage.prompt_tokens + usage.output_tokens,
                },
            }
        except TimeoutError as error:
            raise GatewayError("deadline_exceeded") from error
        finally:
            try:
                await stream.aclose()
            finally:
                self.running = False
