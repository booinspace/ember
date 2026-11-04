"""Socket-free OpenAI SDK transport for the pinned, isolated Hermes worker."""

import importlib
import json
from collections.abc import Callable
from typing import Protocol, cast

import httpx
from pydantic import JsonValue, TypeAdapter

ENDPOINT = "http://ember.invalid/v1"
LIMIT = 262144
_OBJECT = TypeAdapter(dict[str, JsonValue])


class ModelRPC(Protocol):
    """One parent-owned request lane; the worker never receives gateway credentials."""

    def request(self, kind: str, **values: JsonValue) -> JsonValue:
        """Send one bounded model request and consume its correlated parent reply."""
        ...


class Agent(Protocol):
    """The pinned harness surface used by the external worker composition."""

    tools: list[dict[str, JsonValue]]
    _persist_disabled: bool

    def run_conversation(self, user_message: str, system_message: str) -> object: ...
    def interrupt(self, *, tool_reason: str) -> bool: ...
    def close(self) -> None: ...


def completion_sse(value: JsonValue, *, include_usage: bool = True) -> bytes:
    """Convert a verified complete P2 reply to explicitly buffered SDK event framing."""
    if not isinstance(value, dict) or not isinstance(choices := value.get("choices"), list):
        raise ValueError("hermes_completion_invalid")
    if len(choices) != 1 or not isinstance(choice := choices[0], dict):
        raise ValueError("hermes_completion_invalid")
    if not isinstance(message := choice.get("message"), dict):
        raise ValueError("hermes_completion_invalid")
    content = message.get("content")
    if content is not None and not isinstance(content, str):
        raise ValueError("hermes_completion_invalid")
    delta: dict[str, JsonValue] = {"role": "assistant", "content": content}
    calls = message.get("tool_calls")
    if calls is not None:
        if not isinstance(calls, list) or len(calls) > 16:
            raise ValueError("hermes_completion_invalid")
        normalized: list[JsonValue] = []
        for index, call in enumerate(calls):
            if not isinstance(call, dict) or not isinstance(function := call.get("function"), dict):
                raise ValueError("hermes_completion_invalid")
            if not all(
                isinstance(item, str)
                for item in (call.get("id"), function.get("name"), function.get("arguments"))
            ):
                raise ValueError("hermes_completion_invalid")
            normalized.append(
                {
                    "index": index,
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": function["name"],
                        "arguments": function["arguments"],
                    },
                }
            )
        delta["tool_calls"] = normalized
    chunk: dict[str, JsonValue] = {
        "id": value.get("id"),
        "object": "chat.completion.chunk",
        "model": value.get("model"),
        "created": value.get("created"),
        "choices": [{"index": 0, "delta": delta, "finish_reason": choice.get("finish_reason")}],
    }
    frames = [json.dumps(chunk, allow_nan=False)]
    if include_usage:
        chunk["choices"] = []
        chunk["usage"] = value.get("usage")
        frames.append(json.dumps(chunk, allow_nan=False))
    result = "".join("data: " + frame + "\n\n" for frame in frames).encode() + b"data: [DONE]\n\n"
    if len(result) > LIMIT:
        raise ValueError("hermes_completion_too_large")
    return result


class StdioTransport(httpx.BaseTransport):
    """Map only model metadata and chat requests to stdio; no socket transport is installed."""

    def __init__(self, rpc: ModelRPC) -> None:
        self.rpc = rpc

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Bound outgoing bytes and normalize SDK responses without forwarding HTTP credentials."""
        if (
            request.url.scheme != "http"
            or request.url.host != "ember.invalid"
            or request.url.port is not None
            or request.url.query
            or (request.method, request.url.path)
            not in {
                ("GET", "/v1/models"),
                ("POST", "/v1/chat/completions"),
            }
        ):
            return httpx.Response(
                404,
                json={
                    "error": {
                        "message": "hermes_model_route_denied",
                        "type": "invalid_request_error",
                    }
                },
            )
        try:
            raw = bytearray()
            stream = request.stream
            if not isinstance(stream, httpx.SyncByteStream):
                raise ValueError("hermes_request_stream_invalid")
            for part in stream:
                if len(raw) + len(part) > LIMIT:
                    raise ValueError("hermes_request_too_large")
                raw.extend(part)
            body = _OBJECT.validate_json(raw) if raw else None
            result = self.rpc.request(
                "model", method=request.method, path=request.url.path, body=body
            )
            streaming = body is not None and body.get("stream") is True
            options = body.get("stream_options") if body else None
            content = (
                completion_sse(
                    result,
                    include_usage=isinstance(options, dict)
                    and options.get("include_usage") is True,
                )
                if streaming
                else json.dumps(result, allow_nan=False).encode()
            )
            if len(content) > LIMIT:
                raise ValueError("hermes_response_too_large")
            return httpx.Response(
                200,
                content=content,
                headers={
                    "Content-Type": "text/event-stream" if streaming else "application/json",
                },
            )
        except (ValueError, OSError, TimeoutError):
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "hermes_model_request_failed",
                        "type": "invalid_request_error",
                    }
                },
            )


def create_agent_factory(rpc: ModelRPC) -> Callable[..., Agent]:
    """Qualify one pinned virtual client-factory hook while retaining Hermes safe-worker mode."""
    base: type[object] = cast(type[object], importlib.import_module("run_agent").AIAgent)

    def client(base_url: str = "", *, verify: object = True) -> httpx.Client:
        return httpx.Client(transport=StdioTransport(rpc), trust_env=False, timeout=180)

    # The public constructor has no client injection; every pinned primary/rebuild path calls this.
    specialized = type(
        "EmberStdioAgent", (base,), {"_build_keepalive_http_client": staticmethod(client)}
    )
    return cast(Callable[..., Agent], specialized)
