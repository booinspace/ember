"""The public-page reader cannot reach private addresses or execute page content."""

import asyncio
import importlib
import json
import socket
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from ember_agent.character.core import CharacterCore
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_character_public_search import NoGeneration


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts/hermes"))
    return importlib.import_module("hermes_web")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/a",
        "http://127.1/a",
        "http://[::1]/",
        "https://user:secret@public.example/",
        "http://10.0.0.2/",
        "https://public.example:8443/",
    ],
)
def test_private_credentialed_and_non_http_targets_are_denied(
    web: ModuleType, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(ValueError):
        web.public_target(url)


def test_every_dns_address_must_be_public_and_connection_is_pinned(
    web: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])
    target, host = web.public_target("https://public.example/article")
    assert str(target) == "https://8.8.8.8/article" and host == "public.example"
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("192.168.1.1", 443))],
    )
    with pytest.raises(ValueError, match="private_address"):
        web.public_target("https://public.example/")


def test_reader_pins_tls_hostname_filters_active_markup_and_labels_partial_text(
    web: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("8.8.8.8", 443))])
    requests = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "8.8.8.8" and request.headers["host"] == "public.example"
        assert request.extensions["sni_hostname"] == "public.example"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=httpx.ByteStream(
                b"<script>BAD_SCRIPT</script><nav>BAD_NAV</nav><p>Public article.</p>"
                + b"<p>More text.</p>" * 900
            ),
        )

    actual = httpx.Client
    monkeypatch.setattr(
        web.httpx, "Client", lambda **kw: actual(transport=httpx.MockTransport(serve), **kw)
    )
    result = web.read_page("https://public.example/article")
    assert result["partial"] is True and len(result["text"].encode()) <= 1536
    assert "Public article." in result["text"] and "BAD_" not in result["text"]
    assert len(requests) == 1


def test_redirect_to_private_target_is_rejected_before_second_connection(
    web: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def resolve(host: str, *args: object, **kwargs: object) -> list:
        return [(2, 1, 6, "", ("8.8.8.8" if host == "public.example" else "127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    requests = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://private.example/secret"})

    actual = httpx.Client
    monkeypatch.setattr(
        web.httpx, "Client", lambda **kw: actual(transport=httpx.MockTransport(serve), **kw)
    )
    with pytest.raises(ValueError, match="private_address"):
        web.read_page("https://public.example/")
    assert len(requests) == 1


def test_literal_page_preflight_precedes_recall_and_respects_core_guard(tmp_path: Path) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            memory = InProcessAsyncMemoryClient(engine.character(character.store_id))
            core = CharacterCore(memory, NoGeneration(), model_tag="synthetic", input_ceiling=8192)
            calls = []

            async def read(url: str) -> str:
                calls.append(url)
                return json.dumps({"url": url, "text": "Retrieved article.", "partial": True})

            core.public_page = read
            turn = await core.prepare("Summarize https://public.example/article")
            assert calls == ["https://public.example/article"]
            assert "Retrieved article." in turn.request.model_dump_json()
            assert "partial=true" in turn.request.model_dump_json()
            assert turn.accounting and turn.accounting.serialized_total <= 8192

    asyncio.run(run())


def test_missing_page_provider_reports_unavailable_without_hallucinated_fetch(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            core = CharacterCore(
                InProcessAsyncMemoryClient(engine.character(character.store_id)),
                NoGeneration(),
                model_tag="synthetic",
            )
            turn = await core.prepare("Summarize https://public.example/article")
            assert turn.local_reply and "not configured" in turn.local_reply

    asyncio.run(run())


@pytest.mark.parametrize(
    "user_text,expected",
    [
        ("Summarize https://arxiv.org/abs/1706.03762", "https://arxiv.org/abs/1706.03762"),
        (
            "Summarize [https://arxiv.org/abs/1706.03762](https://arxiv.org/abs/1706.03762)",
            "https://arxiv.org/abs/1706.03762",
        ),
        (
            'Read [paper](https://arxiv.org/abs/1706.03762 "paper title")',
            "https://arxiv.org/abs/1706.03762",
        ),
        ("Explain [paper](<https://arxiv.org/abs/1706.03762>)", "https://arxiv.org/abs/1706.03762"),
        (
            "Summarize [label](https://public.example/Path_(subject))",
            "https://public.example/Path_(subject)",
        ),
        ("Read https://public.example/Path_(subject).", "https://public.example/Path_(subject)"),
        ("Read [a](https://a.example) and [b](https://b.example)", None),
        ("Do not read https://arxiv.org/abs/1706.03762", None),
    ],
)
def test_literal_reader_handles_markdown_destinations_without_combining_urls(
    user_text: str, expected: str | None
) -> None:
    from ember_agent.character.public_search import public_page_url

    assert public_page_url(user_text) == expected


def test_page_failure_activity_keeps_only_a_safe_fixed_diagnostic(tmp_path: Path) -> None:
    from ember_agent.inference.contracts import GatewayError
    from ember_agent.observability import ActivityLog

    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            core = CharacterCore(
                InProcessAsyncMemoryClient(engine.character(character.store_id)),
                NoGeneration(),
                model_tag="synthetic",
            )
            core.activity = ActivityLog()

            async def unavailable(url: str) -> str:
                raise GatewayError("page_http_429")

            core.public_page = unavailable
            turn = await core.prepare("Read https://arxiv.org/abs/1706.03762")
            assert turn.local_reply and "unavailable" in turn.local_reply
            event = next(e for e in core.activity.since(0) if e.name == "read_page")
            assert event.code == "page_http_429" and "arxiv" not in event.model_dump_json()

    asyncio.run(run())
