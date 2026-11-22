import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from ember_agent.character.public_search import public_search_query
from ember_agent.inference.contracts import GatewayError
from ember_agent.runtime.native_search import NativeSearch, SearXNGSearch, copy_results


@pytest.mark.parametrize(
    ("utterance", "query"),
    [
        ("Can you search Google for the SearXNG search API?", "the SearXNG search API?"),
        ("please search the web for lunar science", "lunar science"),
        ("look up public transit times", "public transit times"),
        ("google public documentation", "public documentation"),
        ("Tell me about search engines", None),
    ],
)
def test_only_explicit_user_search_terms_are_selected(utterance: str, query: str | None) -> None:
    assert public_search_query(utterance) == query
    if query is not None:
        assert query in utterance


def test_public_context_copies_whole_public_rows_within_utf8_bound() -> None:
    rows = [
        {"url": "http://127.0.0.1/private", "title": "Denied", "description": "secret"},
        {"url": "https://public.example/one", "title": "<b>One</b>", "description": "🦊" * 500},
        {"url": "https://public.example/two", "title": "Two", "description": "🦊" * 500},
    ]
    copied = copy_results({"success": True, "data": {"web": rows}})
    assert len(copied.encode()) <= 2048
    value = json.loads(copied)
    assert len(value["results"]) == 1
    assert value["results"][0]["url"] == "https://public.example/one"
    assert value["results"][0]["title"] == "One"
    assert "secret" not in copied


def test_owned_native_provider_handles_maximum_unicode_query_and_removes_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "provider.py"
    script.write_text(
        "import argparse,json,os,sys\n"
        "p=argparse.ArgumentParser();p.add_argument('--home');a=p.parse_args()\n"
        "assert os.path.isdir(a.home)\n"
        "assert 'EMBER_TEST_SECRET' not in os.environ\n"
        "r=json.loads(sys.stdin.buffer.readline(2049));assert r['query']=='🦊'*400\n"
        "print(json.dumps({'success':True,'data':{'web':[{'url':'https://public.example/',"
        "'title':'Public','description':'Example'}]}}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EMBER_TEST_SECRET", "must-not-reach-provider")
    homes = tmp_path / "homes"
    provider = NativeSearch((sys.executable, str(script)), homes)
    value = asyncio.run(provider("🦊" * 400))
    assert json.loads(value)["results"][0]["title"] == "Public"
    assert list(homes.iterdir()) == []


def test_invalid_native_provider_result_is_not_search_success() -> None:
    with pytest.raises(GatewayError, match="public_search_unavailable"):
        copy_results({"success": False, "data": {"web": []}})


def test_direct_searxng_only_sends_query_and_copies_safe_bounded_results() -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=httpx.ByteStream(
                json.dumps(
                    {
                        "results": [
                            {"url": "http://127.0.0.1/private", "title": "Denied"},
                            {
                                "url": "https://public.example/a",
                                "title": "<b>A</b>",
                                "content": "🦊" * 300,
                            },
                            {
                                "url": "https://public.example/b",
                                "title": "B",
                                "content": "🦊" * 300,
                            },
                            {"url": "https://public.example/c", "title": "C", "content": "Later"},
                        ]
                    }
                ).encode()
            ),
        )

    provider = SearXNGSearch("http://nas.example:8095/", transport=httpx.MockTransport(serve))
    raw = asyncio.run(provider("a & b"))
    assert len(raw.encode()) <= 2048
    assert [item["title"] for item in json.loads(raw)["results"]] == ["A", "C"]
    (request,) = requests
    assert request.method == "GET" and request.url.host == "nas.example"
    assert request.url.path == "/search" and dict(request.url.params) == {
        "q": "a & b",
        "format": "json",
    }
    assert "authorization" not in request.headers and "cookie" not in request.headers


@pytest.mark.parametrize("status,body", [(302, b"{}"), (200, b"bad json"), (200, b"x" * 131073)])
def test_direct_searxng_fails_closed_without_following_redirects(status: int, body: bytes) -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Content-Type": "application/json", "Location": "https://elsewhere.example/"},
            stream=httpx.ByteStream(body),
        )

    provider = SearXNGSearch("http://nas.example", transport=httpx.MockTransport(serve))
    with pytest.raises(GatewayError, match="public_search_query_bound"):
        asyncio.run(provider("🦊" * 401))
    assert requests == []
    with pytest.raises(GatewayError, match="public_search_unavailable"):
        asyncio.run(provider("synthetic query"))
    assert len(requests) == 1


def test_search_query_is_rejected_before_execution_when_too_large() -> None:
    with pytest.raises(GatewayError, match="public_search_query_bound"):
        public_search_query("search " + "a" * 401)
    with pytest.raises(GatewayError, match="public_search_query_bound"):
        public_search_query("search x" + " " * 15000 + "y")
    assert public_search_query("search public API   ") == "public API"
