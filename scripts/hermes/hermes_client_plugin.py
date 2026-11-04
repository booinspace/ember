"""Native Hermes client extension: bounded request copies and required conversation identities."""

import hashlib
import hmac
import importlib
import json
import os
import re
from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import Protocol, cast

from pydantic import JsonValue, TypeAdapter

from .hermes_client_transport import search_transport

_MESSAGES = TypeAdapter(list[dict[str, JsonValue]])
_OBJECT = TypeAdapter(dict[str, JsonValue])
_FRAME = "Ember supplies the character prompt and bounded history at its authenticated gateway."
_RESULTS: dict[tuple[str, str, str], tuple[float, str | None]] = {}
_RESULT_LOCK = Lock()


class SearchGrants:
    """Only server-issued preflights can open the native provider's HTTP seam once."""

    def __init__(self, token: str) -> None:
        if not 32 <= len(token) <= 256:
            raise ValueError("The paired Ember client token is unavailable.")
        self.token = token
        self.used: set[str] = set()
        self.pending: dict[str, str] = {}
        self.lock = Lock()

    def before_tool(
        self,
        *,
        tool_name: object = None,
        args: object = None,
        session_id: object = None,
        turn_id: object = None,
        tool_call_id: object = None,
        **context: object,
    ) -> dict[str, str] | None:
        denied = {"action": "block", "message": "Ember did not issue this public search."}
        try:
            arguments = _OBJECT.validate_python(args)
        except ValueError:
            return denied
        if (
            tool_name != "web_search"
            or set(arguments) != {"query", "limit"}
            or type(arguments.get("limit")) is not int
            or arguments["limit"] != 3
            or not isinstance(query := arguments.get("query"), str)
            or not 1 <= len(query) <= 400
            or not isinstance(session_id, str)
            or not 1 <= len(session_id) <= 1024
            or not session_id.isascii()
            or not isinstance(turn_id, str)
            or not 1 <= len(turn_id) <= 1024
            or not turn_id.isascii()
            or not isinstance(tool_call_id, str)
            or re.fullmatch(r"ember-search-[0-9a-f]{64}", tool_call_id) is None
        ):
            return denied
        alias = hashlib.sha256(("X-Ember-Session:" + session_id).encode()).hexdigest()
        identity = hashlib.sha256((self.token + ":" + alias).encode()).hexdigest()
        turn = hashlib.sha256(("X-Ember-Turn:" + turn_id).encode()).hexdigest()
        expected = (
            "ember-search-"
            + hmac.new(
                self.token.encode(),
                json.dumps(
                    [identity, turn, query], ensure_ascii=False, separators=(",", ":")
                ).encode(),
                "sha256",
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, tool_call_id):
            return denied
        with self.lock:
            if tool_call_id in self.used or len(self.used) >= 256 or len(self.pending) >= 32:
                return denied
            self.used.add(tool_call_id)
            self.pending[tool_call_id] = query
        return None

    def consume(self, query: str) -> None:
        with self.lock:
            key = next((key for key, value in self.pending.items() if value == query), None)
            if key is None:
                raise ValueError("The native search has no issued preflight grant.")
            self.pending.pop(key)


def _public_result(result: object) -> str:
    """Copy the original native result before the SDK wraps it for display/persistence."""
    result = (
        _OBJECT.validate_json(result)
        if isinstance(result, str)
        else _OBJECT.validate_python(result)
    )
    data = result.get("data")
    rows = data.get("web") if isinstance(data, dict) else None
    if result.get("success") is not True or not isinstance(rows, list):
        raise ValueError("The native public search result is unavailable.")
    kept: list[JsonValue] = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        url, title, description = row.get("url"), row.get("title"), row.get("description", "")
        if not all(isinstance(value, str) for value in (url, title, description)):
            continue
        assert isinstance(url, str) and isinstance(title, str) and isinstance(description, str)
        if len(url) > 512:
            continue
        item: JsonValue = {"url": url, "title": title[:128], "description": description[:256]}
        projected = {"success": True, "data": {"web": [*kept, item]}}
        if len(json.dumps(projected, ensure_ascii=False).encode()) <= 2048:
            kept.append(item)
    return json.dumps({"success": True, "data": {"web": kept}}, ensure_ascii=False)


def _key(session: object, turn: object, call: object) -> tuple[str, str, str]:
    if not all(
        isinstance(value, str) and 0 < len(value) <= 1024 for value in (session, turn, call)
    ):
        raise ValueError("The native tool provenance is unavailable.")
    assert isinstance(session, str) and isinstance(turn, str) and isinstance(call, str)
    return session, turn, call


def remember_tool_result(
    *,
    tool_name: object = None,
    result: object = None,
    session_id: object = None,
    turn_id: object = None,
    tool_call_id: object = None,
    **context: object,
) -> None:
    """Retain bounded public data under the SDK's exact owning tool identity."""
    if tool_name != "web_search":
        return
    key = _key(session_id, turn_id, tool_call_id)
    now = monotonic()
    value = _public_result(result)
    with _RESULT_LOCK:
        for old, (created, _) in tuple(_RESULTS.items()):
            if now - created > 180:
                _RESULTS.pop(old)
        if key in _RESULTS and _RESULTS[key][1] != value:
            _RESULTS[key] = (now, None)
            raise ValueError("A native tool identity cannot change its result.")
        if key not in _RESULTS and len(_RESULTS) >= 32:
            raise ValueError("The native tool-result cache is full.")
        _RESULTS[key] = (now, value)


class PluginContext(Protocol):
    """The pinned SDK's supported extension registration surface."""

    def register_context_engine(self, engine: object) -> object: ...
    def register_middleware(self, name: str, callback: Callable[..., object]) -> object: ...
    def register_hook(self, name: str, callback: Callable[..., object]) -> object: ...


def select_messages(messages: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    """Keep the current tool turn and delivery echo without mutating the transcript."""
    source = messages
    index = next(
        (i for i in range(len(source) - 1, -1, -1) if source[i].get("role") == "user"), None
    )
    if index is None or not isinstance(source[index].get("content"), str):
        raise ValueError("The Ember client supports text input only.")
    selected: list[dict[str, JsonValue]] = [{"role": "system", "content": _FRAME}]
    echo = next(
        (
            message
            for message in reversed(source[:index])
            if message.get("role") == "assistant" and not message.get("tool_calls")
        ),
        None,
    )
    if echo is not None:
        selected.append(echo)
    for message in source[index:]:
        selected.append({**message, "content": ""} if message.get("role") == "tool" else message)
    selected = _MESSAGES.validate_python(selected)
    if len(selected) > 5 or len(json.dumps(selected, ensure_ascii=False).encode()) > 24576:
        raise ValueError("The current Ember tool turn exceeds its request budget.")
    return selected


def shape_request(
    *,
    request: dict[str, JsonValue],
    session_id: object = "",
    turn_id: object = "",
    api_request_id: object = "",
    **context: object,
) -> dict[str, JsonValue]:
    """Bind normal SDK turns; auxiliary calls without these identities fail at ingress."""
    updated = dict(request)
    messages = _MESSAGES.validate_python(updated.get("messages"))
    if not messages or messages[0] != {"role": "system", "content": _FRAME}:
        raise ValueError("The qualified request-only context engine did not run.")
    headers = updated.get("extra_headers", {})
    if not isinstance(headers, dict) or any(
        not isinstance(value, str) for value in headers.values()
    ):
        raise ValueError("Unsupported native model headers.")
    headers = dict(headers)
    for name, value in (
        ("X-Ember-Session", session_id),
        ("X-Ember-Turn", turn_id),
        ("X-Ember-Request", api_request_id),
    ):
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ValueError("A normal native conversation identity is required.")
        for old in tuple(headers):
            if old.casefold() == name.casefold():
                headers.pop(old)
        headers[name] = hashlib.sha256((name + ":" + value).encode()).hexdigest()
    updated["extra_headers"] = headers
    selected = select_messages(messages)
    for message in selected:
        if message.get("role") == "tool":
            key = _key(session_id, turn_id, message.get("tool_call_id"))
            with _RESULT_LOCK:
                found = _RESULTS.get(key)
            if found is None or found[1] is None or monotonic() - found[0] > 180:
                raise ValueError("The native original tool result is unavailable.")
            message["content"] = found[1]
    updated["messages"] = list[JsonValue](selected)
    updated["max_tokens"] = 512
    updated.pop("max_completion_tokens", None)
    updated["temperature"] = 0.2
    updated["parallel_tool_calls"] = False
    updated["reasoning_effort"] = "none"
    return {"request": updated, "source": "ember-client"}


def _select(
    self: object,
    request_messages: list[dict[str, JsonValue]],
    *,
    incoming_message: dict[str, JsonValue] | None = None,
    **context: object,
) -> list[dict[str, JsonValue]]:
    original = _OBJECT.validate_python(incoming_message)
    if original.get("role") != "user" or not isinstance(original.get("content"), str):
        raise ValueError("The native original user-input provenance is unavailable.")
    messages = list(request_messages)
    index = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"),
        None,
    )
    if index is None:
        raise ValueError("The native user-input position is unavailable.")
    messages[index] = {"role": "user", "content": original["content"]}
    return select_messages(messages)


def _usage(self: object, usage: dict[str, JsonValue]) -> None:
    for attribute, key in (
        ("last_prompt_tokens", "prompt_tokens"),
        ("last_completion_tokens", "completion_tokens"),
        ("last_total_tokens", "total_tokens"),
    ):
        value = usage.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("The native provider did not report valid token usage.")
        setattr(self, attribute, value)


def _compress(
    self: object, messages: list[dict[str, JsonValue]], *args: object, **kwargs: object
) -> list[dict[str, JsonValue]]:
    return _MESSAGES.validate_python(messages)


def _should_compress(self: object, prompt_tokens: int | None = None) -> bool:
    return False


def _has_content(self: object, messages: list[dict[str, JsonValue]]) -> bool:
    return False


def register(context: PluginContext) -> None:
    """Install supported hooks last, after the qualified native search transport is available."""
    provider = importlib.import_module("plugins.web.searxng.provider")
    env = cast(Callable[[str], str], provider.provider_env)
    if not callable(getattr(provider, "http_get_json", None)):
        raise ValueError("The native SearXNG provider transport changed.")
    endpoint, token = env("SEARXNG_URL"), os.environ.get("EMBER_CLIENT_TOKEN", "")
    key = hashlib.sha256((token + "\n" + endpoint).encode()).hexdigest()
    # Provider modules can be evicted while registered instances survive; anchor on the SDK owner.
    owner = importlib.import_module("hermes_cli.plugins")
    installed: object = vars(owner).get("_ember_client_hooks_v1")
    if installed is None:
        grants = SearchGrants(token)
        installed = vars(owner).setdefault(
            "_ember_client_hooks_v1",
            (key, grants.before_tool, grants.consume, remember_tool_result, shape_request),
        )
    if not isinstance(installed, tuple):
        raise ValueError("The native search grant owner changed.")
    hooks = cast(tuple[object, ...], installed)
    if len(hooks) != 5 or hooks[0] != key or not all(callable(hook) for hook in hooks[1:]):
        raise ValueError("The native search grant owner changed.")
    before = cast(Callable[..., object], hooks[1])
    consume = cast(Callable[[str], None], hooks[2])
    vars(provider)["http_get_json"] = search_transport(endpoint, authorize=consume)
    base = cast(type[object], importlib.import_module("agent.context_engine").ContextEngine)
    engine = type(
        "EmberRequestContext",
        (base,),
        {
            "name": "ember-client",
            "select_context": _select,
            "update_from_response": _usage,
            "compress": _compress,
            "should_compress": _should_compress,
            "has_content_to_compress": _has_content,
        },
    )()
    context.register_context_engine(engine)
    context.register_hook("pre_tool_call", before)
    context.register_hook("post_tool_call", cast(Callable[..., object], hooks[3]))
    context.register_middleware("llm_request", cast(Callable[..., object], hooks[4]))
