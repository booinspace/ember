"""Synthetic contract checks for hosted web transport and Conversation synthesis."""

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest

from ember_agent.character.conversation import CompletedTurn
from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayEvent, GatewayRequest, GenerationUsage
from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_agent.public_web import PublicWeb, WebBinding
from ember_agent.runtime.hosted_web import HostedWeb, HostedWebName
from ember_memory_abi.interfaces import ContextRequest, RetrievalContext
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

_KEY_ENV = "TEST_EMBER_PUBLIC_WEB_KEY"
_KEY = "synthetic-credential-canary"
_URL = "https://www.example.com/article"
_QUERY = "synthetic public information"


def reply(value: dict[str, object], *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        stream=httpx.ByteStream(json.dumps(value).encode()),
        headers={"Content-Type": "application/json", "Location": "https://unused.example"},
    )


@pytest.mark.parametrize("provider", ["exa", "parallel"])
def test_exact_hosted_requests_and_passage_provenance(
    provider: HostedWebName,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_KEY_ENV, _KEY)
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path in {"/search", "/v1/search"}:
            field = "highlights" if provider == "exa" else "excerpts"
            content = {field: ["Public excerpt " + _KEY, "📖" * 900]}
        elif provider == "exa":
            content = {"text": "Extracted text."}
        else:
            content = {"excerpts": ["Extracted text."]}
        return reply(
            {
                "results": [{"title": "Public article", "url": _URL, **content}],
                "answer": "Untrusted provider answer",
            }
        )

    adapter = HostedWeb(provider, _KEY_ENV, transport=httpx.MockTransport(serve))
    search = asyncio.run(adapter.search(_QUERY))
    read = asyncio.run(adapter.read(_URL))
    assert search.provider == read.provider == provider
    assert search.operation == "search" and read.operation == "read"
    assert search.coverage == read.coverage == "partial"
    assert search.truncated and search.sources[0].truncated and not read.truncated
    assert read.sources[0].url == _URL and read.sources[0].passages == ("Extracted text.",)
    assert search.sources[0].passages[0] == "Public excerpt [redacted]"
    assert _KEY not in search.model_dump_json()
    assert "Untrusted provider answer" not in search.model_dump_json()
    search_request, read_request = requests
    for request in requests:
        assert request.method == "POST" and request.url.scheme == "https"
        assert request.url.host == f"api.{provider}.ai"
        assert request.headers["x-api-key"] == _KEY and "cookie" not in request.headers
        assert request.headers["accept-encoding"] == "identity"
        assert _KEY.encode() not in request.content
    if provider == "exa":
        assert search_request.url.path == "/search" and read_request.url.path == "/contents"
        assert json.loads(search_request.content) == {
            "query": _QUERY,
            "numResults": 3,
            "type": "auto",
            "contents": {"highlights": {"maxCharacters": 900}},
        }
        assert json.loads(read_request.content) == {
            "ids": [_URL],
            "text": {"maxCharacters": 2600},
        }
    else:
        assert search_request.url.path == "/v1/search" and read_request.url.path == "/v1/extract"
        assert json.loads(search_request.content) == {
            "search_queries": [_QUERY],
            "mode": "fast",
            "max_chars_total": 2700,
            "advanced_settings": {"max_results": 3},
        }
        assert json.loads(read_request.content) == {
            "urls": [_URL],
            "max_chars_total": 2600,
            "advanced_settings": {
                "excerpt_settings": {"max_chars_per_result": 2600},
                "full_content": False,
            },
        }


def test_no_network_for_missing_credentials_or_unsafe_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_: httpx.Request) -> httpx.Response:
        raise AssertionError("Unexpected request")

    adapter = HostedWeb("exa", _KEY_ENV, transport=httpx.MockTransport(forbidden))
    monkeypatch.delenv(_KEY_ENV, raising=False)
    assert not adapter.configured
    assert asyncio.run(adapter.search(_QUERY)).failure == "unavailable"
    monkeypatch.setenv(_KEY_ENV, _KEY)
    for url in (
        "file:///private/data",
        "http://[::1]/",
        "http://127.0.0.1/path",
        "http://localhost.example.internal/",
        "https://ember.example.test/memory-graph",
        "https://user:password@example.com/article",
        "https://example.com/article?signature=secret",
        "https://example.com/article#secret",
        "https://example.com:8080/article",
        "https://example.com/%74oken/key",
        "https://example.com/private-filename",
        "https://example.com/article/" + "a" * 48,
        "https://example.com/article/" + _KEY,
    ):
        assert asyncio.run(adapter.read(url)).failure == "input_rejected", url
    for query in (
        "Read https://example.com/?secret=123",
        "example.com/?secret=123",
        "a\nprivate context",
        "//example.com/?secret=123",
        "file:///Users/example",
        "x" * 401,
        _KEY,
    ):
        assert asyncio.run(adapter.search(query)).failure == "input_rejected", query


@pytest.mark.parametrize(
    "case,expected",
    [
        ("redirect", "unavailable"),
        ("http_error", "unavailable"),
        ("json", "invalid_response"),
        ("encoding", "invalid_response"),
        ("oversize", "response_bound"),
        ("timeout", "timeout"),
        ("empty", None),
        ("read_error", "unavailable"),
        ("wrong_url", "invalid_response"),
        ("unsafe_result", "invalid_response"),
    ],
)
def test_hosted_fails_closed_without_retry(
    case: str,
    expected: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_KEY_ENV, _KEY)
    calls = 0

    def serve(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if case == "redirect":
            return reply({}, status=302)
        if case == "http_error":
            return reply({"error": _KEY}, status=429)
        if case == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if case == "read_error":
            return reply({"results": [], "errors": [{"error": _KEY}]})
        if case in {"wrong_url", "unsafe_result"}:
            url = (
                "https://elsewhere.example/article" if case == "wrong_url" else _URL + "?key=value"
            )
            return reply({"results": [{"url": url, "excerpts": ["Synthetic excerpt."]}]})
        if case == "empty":
            return reply({"results": []})
        return httpx.Response(
            200,
            stream=httpx.ByteStream(b"x" * (131_073 if case == "oversize" else 1)),
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip" if case == "encoding" else "identity",
            },
        )

    adapter = HostedWeb("parallel", _KEY_ENV, transport=httpx.MockTransport(serve))
    result = asyncio.run(
        adapter.read(_URL) if case in {"read_error", "wrong_url"} else adapter.search(_QUERY)
    )
    assert calls == 1
    assert result.failure == expected and not result.sources
    assert result.coverage == ("empty" if expected is None else "unavailable")
    assert _KEY not in result.model_dump_json()


@pytest.mark.parametrize(
    "metadata,accepted",
    [
        ({}, True),
        ({"statuses": [{"id": _URL, "status": "success"}]}, True),
        ({"statuses": [{"id": _URL + "/other", "status": "success"}]}, False),
        ({"statuses": [{"status": "success"}]}, False),
        ({"statuses": [{"id": 1, "status": "success"}]}, False),
        ({"statuses": [{"id": _URL, "status": "error"}]}, False),
        ({"statuses": None}, False),
    ],
)
def test_exa_read_binds_optional_status_to_requested_id(
    metadata: dict[str, object], accepted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_ENV, _KEY)
    adapter = HostedWeb(
        "exa",
        _KEY_ENV,
        transport=httpx.MockTransport(
            lambda _: reply({"results": [{"url": _URL, "text": "Public excerpt."}], **metadata})
        ),
    )
    result = asyncio.run(adapter.read(_URL))
    assert bool(result.sources) == accepted
    assert result.coverage == ("partial" if accepted else "unavailable")
    assert result.failure == (None if accepted else "unavailable")


def exa_routes(hosted: HostedWeb) -> PublicWeb:
    return PublicWeb(
        WebBinding("exa", "Exa", "fixture", hosted.search),
        WebBinding("exa", "Exa", "fixture", hosted.read),
    )


class SummaryModel:
    def __init__(self, response: str, *, truncated: bool = False) -> None:
        self.response = response
        self.truncated = truncated
        self.requests: list[GatewayRequest] = []

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        self.requests.append(request)
        event = GatewayEvent(
            request_id=request.request_id, context_id=request.context_id, sequence=0, kind="start"
        )
        yield event
        yield event.model_copy(update={"kind": "text", "sequence": 1, "text": self.response})
        yield event.model_copy(
            update={
                "kind": "end",
                "sequence": 2,
                "truncated": self.truncated,
                "usage": GenerationUsage(
                    prompt_tokens=20,
                    output_tokens=10,
                    total_seconds=1,
                    first_visible_seconds=0.5,
                    tokens_per_second=10,
                ),
            }
        )


@pytest.mark.parametrize(
    "response,truncated,success",
    [
        ('{"answer":"Retrieved information.","sources":[1]}', False, True),
        ('{"answer":"No citation.","sources":[2]}', False, False),
        ('{"answer":"Visit https://untrusted.example.","sources":[1]}', False, False),
        ('{"answer":"Truncated answer.","sources":[1]}', True, False),
    ],
)
def test_conversation_uses_only_literal_url_and_local_validated_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: str,
    truncated: bool,
    success: bool,
) -> None:
    monkeypatch.setenv(_KEY_ENV, _KEY)
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return reply({"results": [{"url": _URL, "text": "📖" * 900}]})

    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            memory = InProcessAsyncMemoryClient(engine.character(character.store_id))

            async def forbidden(_: ContextRequest) -> RetrievalContext:
                raise AssertionError("Hosted web entered private recall")

            monkeypatch.setattr(memory, "retrieve_context", forbidden)
            model = SummaryModel(response, truncated=truncated)
            core = CharacterCore(memory, model, model_tag="synthetic", input_ceiling=8192)
            core.history.append(CompletedTurn("PRIVATE_HISTORY", "PRIVATE_REPLY", ()))
            core.public_web = exa_routes(
                HostedWeb("exa", _KEY_ENV, transport=httpx.MockTransport(serve))
            )
            turn = await core.prepare(f"Exa read: {_URL}")
            assert turn.local_reply is None and turn.web_evidence is not None
            assert turn.web_evidence.truncated and turn.accounting
            assert turn.accounting.serialized_total <= 8192
            assert not turn.request.tools and turn.request.output == "json"
            assert "PRIVATE_" not in turn.request.model_dump_json()
            assert _KEY not in turn.request.model_dump_json()
            for invalid in (
                f"Read {_URL} with PRIVATE_CURRENT_COMMENTARY",
                f"Exa read: {_URL}?token=secret",
                f"Exa read: {_URL} with PRIVATE_CURRENT_COMMENTARY",
                f"Exa read: {_URL} and https://www.example.com/other",
                f"Parallel read: {_URL}",
                "Exa read: file:///private/file.txt",
                "Exa search: /private/file.txt",
                "Exa search: PRIVATE_CURRENT_COMMENTARY",
                "Exa search: public query\nPRIVATE_CURRENT_COMMENTARY",
            ):
                denied = await core.prepare(invalid)
                assert denied.local_reply and denied.web_evidence is None
                assert len(requests) == 1
            events = [event async for event in core.stream(turn)]
            assert len(requests) == 1
            assert json.loads(requests[0].content)["ids"] == [_URL]
            assert sum(request.output == "json" for request in model.requests) == 1
            assert [event.kind for event in events] == ["start", "text", "end"]
            visible = events[1].text
            assert _URL in visible
            if success:
                assert "Retrieved information." in visible and "omitted or shortened" in visible
            else:
                assert "Couldn't produce" in visible and "untrusted.example" not in visible
                assert "Freshness was not verified" in visible
            core.recall_enabled = False
            followup = await core.prepare("Thanks. What else can we talk about?")
            assert visible not in followup.request.model_dump_json()
            assert all(
                visible not in (message.content or "") for message in followup.request.messages
            )

    asyncio.run(run())


def test_archived_web_reply_preserves_receipt_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(_KEY_ENV, _KEY)

    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            sink = ArchiveDialogueSink(engine.archive(character))
            model = SummaryModel('{"answer":"Untrusted archive canary.","sources":[1]}')
            core = CharacterCore(
                memory, model, model_tag="fixture", sink=sink, recall_enabled=False
            )
            core.public_web = exa_routes(
                HostedWeb(
                    "exa",
                    _KEY_ENV,
                    transport=httpx.MockTransport(
                        lambda _: reply({"results": [{"url": _URL, "text": "Evidence"}]})
                    ),
                )
            )
            try:
                # Clip away the command prefix in recent; do not rely on parsing clipped input.
                turn = await core.prepare(" " * 4096 + f"Exa read: {_URL}")
                delivered = "".join([event.text async for event in core.stream(turn)])
                page = sink.sessions.read(core.context_id)
                receipt = page.turns[-1]
                assert receipt.clipped and not receipt.user.strip()
                assert receipt.context.public_web_reply and receipt.assistant == delivered
                assert "Untrusted archive canary." in delivered
                reopened = CharacterCore(
                    memory, model, model_tag="fixture", sink=sink, recall_enabled=False
                )
                reopened.context_id = core.context_id
                for prompt in ("Continue.", "What was my first message?"):
                    followup = await reopened.prepare(prompt)
                    assert "Untrusted archive canary." not in followup.request.model_dump_json()
            finally:
                await sink.aclose()
                await memory.aclose()

    asyncio.run(run())
