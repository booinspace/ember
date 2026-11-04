import asyncio
import copy
import hashlib
import hmac
import json
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import ModuleType, SimpleNamespace

import httpx
import pytest
from pydantic import JsonValue
from scripts.hermes import hermes_client_plugin as plugin
from scripts.hermes import hermes_client_transport as transport

from .test_hermes_chat import TOKEN, TOOL, application, body


class Chunks(httpx.SyncByteStream):
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def __iter__(self) -> Iterator[bytes]:
        for start in range(0, len(self.raw), 4096):
            yield self.raw[start : start + 4096]


@pytest.mark.parametrize(
    ("status", "headers", "raw", "valid"),
    [
        (200, {"Content-Type": "application/json"}, b'{"results":[]}', True),
        (200, {"Content-Type": "application/json"}, b" " * 131073, False),
        (
            200,
            {"Content-Type": "application/json", "Content-Encoding": "gzip"},
            b'{"results":[]}',
            False,
        ),
        (302, {"Location": "http://foreign.invalid/"}, b"", False),
    ],
    ids=["valid", "oversized", "compressed", "redirect"],
)
def test_native_search_transport_bounds_raw_response(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    headers: dict[str, str],
    raw: bytes,
    valid: bool,
) -> None:
    client = httpx.Client
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "nas.invalid"
        assert request.url.params["q"] == "public API"
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(status, headers=headers, stream=Chunks(raw))

    def factory(**options: object) -> httpx.Client:
        assert options["trust_env"] is False
        assert options["follow_redirects"] is False
        return client(transport=httpx.MockTransport(respond), **options)

    monkeypatch.setattr(transport.httpx, "Client", factory)
    search = transport.search_transport("http://nas.invalid:8095")
    params = {"q": "public API", "format": "json", "pageno": 1}
    if valid:
        assert search("SearXNG", "http://nas.invalid:8095/search", params=params) == (
            {"results": []},
            None,
        )
    else:
        with pytest.raises(ValueError):
            search("SearXNG", "http://nas.invalid:8095/search", params=params)
    assert len(requests) == 1
    with pytest.raises(ValueError, match="scope"):
        search("SearXNG", "http://foreign.invalid/search", params=params)
    assert len(requests) == 1


def test_native_original_input_and_bounded_history_do_not_rewrite_saved_chat() -> None:
    saved = [
        {"role": "system", "content": "Desktop workspace scaffold"},
        {"role": "user", "content": "Old input"},
        {"role": "assistant", "content": "Prior reply receipt"},
        {"role": "user", "content": "Desktop scaffold plus original input"},
    ]
    before = copy.deepcopy(saved)
    selected = plugin._select(
        object(), saved, incoming_message={"role": "user", "content": "Original input"}
    )
    assert saved == before
    assert [row["content"] for row in selected] == [
        plugin._FRAME,
        "Prior reply receipt",
        "Original input",
    ]
    request = {"messages": selected, "max_completion_tokens": 9000}
    shaped = plugin.shape_request(
        request=request, session_id="chat-a", turn_id="turn-a", api_request_id="request-a"
    )["request"]
    assert shaped["max_tokens"] == 512 and "max_completion_tokens" not in shaped
    assert shaped["reasoning_effort"] == "none"
    headers = shaped["extra_headers"]
    assert all(len(value) == 64 for value in headers.values())
    with pytest.raises(ValueError, match="context engine"):
        plugin.shape_request(
            request={"messages": saved},
            session_id="chat-a",
            turn_id="turn-a",
            api_request_id="request-a",
        )
    with pytest.raises(ValueError, match="identity"):
        plugin.shape_request(request=request, session_id="chat-a", turn_id="turn-a")


def test_original_native_result_requires_exact_provenance_and_rejects_conflicting_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin, "_RESULTS", {})
    monkeypatch.setattr(plugin, "monotonic", lambda: 10.0)
    result = {
        "success": True,
        "data": {"web": [{"url": "https://public.example/", "title": "API"}]},
    }
    identity = {"session_id": "chat", "turn_id": "turn", "tool_call_id": "call"}
    plugin.remember_tool_result(tool_name="web_search", result=result, **identity)
    selected = plugin.select_messages(
        [
            {"role": "user", "content": "Search for API"},
            {"role": "assistant", "content": None, "tool_calls": []},
            {"role": "tool", "tool_call_id": "call", "content": "UNTRUSTED XML WRAPPER"},
        ]
    )

    def shape(session: str = "chat") -> dict[str, JsonValue]:
        shaped = plugin.shape_request(
            request={"messages": selected},
            session_id=session,
            turn_id="turn",
            api_request_id="request",
        )["request"]
        assert isinstance(shaped, dict)
        return shaped

    assert json.loads(shape()["messages"][-1]["content"])["data"]["web"][0]["title"] == "API"
    assert "UNTRUSTED" not in json.dumps(shape())
    with pytest.raises(ValueError, match="unavailable"):
        shape("foreign-chat")
    plugin.remember_tool_result(tool_name="web_search", result=result, **identity)
    changed = {"success": True, "data": {"web": []}}
    with pytest.raises(ValueError, match="cannot change"):
        plugin.remember_tool_result(tool_name="web_search", result=changed, **identity)
    with pytest.raises(ValueError, match="unavailable"):
        shape()
    with pytest.raises(ValueError, match="cannot change"):
        plugin.remember_tool_result(tool_name="web_search", result=result, **identity)


def test_original_native_tool_result_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin, "_RESULTS", {})
    monkeypatch.setattr(plugin, "monotonic", lambda: 1.0)
    plugin.remember_tool_result(
        tool_name="web_search",
        result={"success": True, "data": {"web": []}},
        session_id="chat",
        turn_id="turn",
        tool_call_id="call",
    )
    monkeypatch.setattr(plugin, "monotonic", lambda: 182.0)
    selected = plugin.select_messages(
        [
            {"role": "user", "content": "Search for API"},
            {"role": "tool", "tool_call_id": "call", "content": "SDK wrapper"},
        ]
    )
    with pytest.raises(ValueError, match="unavailable"):
        plugin.shape_request(
            request={"messages": selected},
            session_id="chat",
            turn_id="turn",
            api_request_id="request",
        )


def test_only_host_issued_preflight_can_open_native_search_once(tmp_path: Path) -> None:
    async def run() -> None:
        query = "public SearXNG documentation"
        selected = plugin.select_messages(body("Search for " + query)["messages"])
        shaped = plugin.shape_request(
            request={"model": "ember", "messages": selected, "tools": [TOOL]},
            session_id="native-chat",
            turn_id="native-turn",
            api_request_id="native-request",
        )["request"]
        assert isinstance(shaped, dict)
        headers = shaped.pop("extra_headers")
        headers["Authorization"] = "Bearer " + TOKEN
        async with application(tmp_path) as env:
            response = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers, json=shaped
            )
            assert response.status_code == 200, response.text
            call = response.json()["choices"][0]["message"]["tool_calls"][0]
        grants = plugin.SearchGrants(TOKEN)
        scope = {
            "tool_name": "web_search",
            "args": json.loads(call["function"]["arguments"]),
            "session_id": "native-chat",
            "turn_id": "native-turn",
            "tool_call_id": call["id"],
        }
        for replacement in (
            {"tool_call_id": "forged"},
            {"session_id": "foreign-chat"},
            {"turn_id": "foreign-turn"},
            {"args": {"query": "PRIVATE_MODEL_DERIVED_QUERY", "limit": 3}},
            {"args": None},
        ):
            denied = grants.before_tool(**(scope | replacement))
            assert denied is not None and denied["action"] == "block"
        with pytest.raises(ValueError, match="grant"):
            grants.consume("PRIVATE_MODEL_DERIVED_QUERY")
        assert grants.before_tool(**scope) is None
        malformed = grants.before_tool(
            **(scope | {"session_id": "foreign-chat", "tool_call_id": "秘密"})
        )
        assert malformed is not None and malformed["action"] == "block"
        with pytest.raises(ValueError, match="grant"):
            grants.consume("PRIVATE_MODEL_DERIVED_QUERY")
        grants.consume(query)
        with pytest.raises(ValueError, match="grant"):
            grants.consume(query)
        assert grants.before_tool(**scope)["action"] == "block"

    asyncio.run(run())


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("reload_provider", [False, True])
def test_repeated_sdk_managers_share_the_http_grant_owner(
    monkeypatch: pytest.MonkeyPatch, parallel: bool, reload_provider: bool
) -> None:
    provider = ModuleType("synthetic_searx_provider")
    provider.provider_env = lambda name: "http://nas.invalid"
    provider.http_get_json = lambda *args, **kwargs: None
    second_provider = ModuleType("replacement_searx_provider")
    second_provider.provider_env = provider.provider_env
    second_provider.http_get_json = provider.http_get_json
    candidates = iter((provider, second_provider))
    sdk = ModuleType("synthetic_sdk_plugin_owner")
    base = ModuleType("synthetic_context_engine")
    base.ContextEngine = type("ContextEngine", (), {})
    original_import = plugin.importlib.import_module

    def load(name: str) -> ModuleType:
        if name == "plugins.web.searxng.provider":
            return next(candidates) if reload_provider else provider
        if name == "agent.context_engine":
            return base
        if name == "hermes_cli.plugins":
            return sdk
        return original_import(name)

    monkeypatch.setattr(plugin.importlib, "import_module", load)
    monkeypatch.setenv("EMBER_CLIENT_TOKEN", TOKEN)
    if parallel:
        ready = Barrier(2)
        original_grants = plugin.SearchGrants

        class RacingGrants(original_grants):
            def __init__(self, token: str) -> None:
                ready.wait(timeout=5)
                super().__init__(token)

        monkeypatch.setattr(plugin, "SearchGrants", RacingGrants)

    def register() -> dict[str, Callable[..., object]]:
        hooks: dict[str, Callable[..., object]] = {}
        context = SimpleNamespace(
            register_context_engine=lambda engine: None,
            register_hook=lambda name, callback, target=hooks: target.update({name: callback}),
            register_middleware=lambda name, callback: None,
        )
        plugin.register(context)
        return hooks

    if parallel:
        with ThreadPoolExecutor(max_workers=2) as pool:
            operations = [pool.submit(register) for _ in range(2)]
            registered = [operation.result(timeout=10) for operation in operations]
    else:
        registered = [register(), register()]
    assert registered[0]["pre_tool_call"].__self__ is registered[1]["pre_tool_call"].__self__
    query = "public API"
    alias = hashlib.sha256(b"X-Ember-Session:native-chat").hexdigest()
    identity = hashlib.sha256((TOKEN + ":" + alias).encode()).hexdigest()
    turn = hashlib.sha256(b"X-Ember-Turn:native-turn").hexdigest()
    call = (
        "ember-search-"
        + hmac.new(
            TOKEN.encode(),
            json.dumps([identity, turn, query], separators=(",", ":")).encode(),
            "sha256",
        ).hexdigest()
    )
    assert (
        registered[0]["pre_tool_call"](
            tool_name="web_search",
            args={"query": query, "limit": 3},
            session_id="native-chat",
            turn_id="native-turn",
            tool_call_id=call,
        )
        is None
    )
    client = httpx.Client

    def factory(**options: object) -> httpx.Client:
        return client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    stream=Chunks(b'{"results":[]}'),
                )
            ),
            **options,
        )

    monkeypatch.setattr(transport.httpx, "Client", factory)
    assert provider.http_get_json(
        "SearXNG",
        "http://nas.invalid/search",
        params={"q": query, "format": "json", "pageno": 1},
    ) == ({"results": []}, None)
    with pytest.raises(ValueError, match="grant"):
        provider.http_get_json(
            "SearXNG",
            "http://nas.invalid/search",
            params={"q": query, "format": "json", "pageno": 1},
        )
