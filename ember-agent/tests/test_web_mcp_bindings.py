"""Config-only public provider fixture; no live provider qualification."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import JsonValue

from ember_agent.realtime.contracts import RoomBinding
from ember_agent.runtime import harness, livekit_runtime, research, web_mcp
from ember_agent.runtime.capabilities import capability_catalog
from ember_agent.runtime.composition import LocalRuntime
from ember_agent.runtime.config import ConfigurationError, HarnessSettings, load_config
from ember_agent.runtime.public_web_runtime import resolve_public_web
from ember_agent.runtime.task_store import TaskConflict

from .test_config import config_path as config_path
from .test_exa_mcp import _rpc
from .test_hosted_web import SummaryModel

_URL = "https://example.org/manual"
_TOKEN = "synthetic-provider-auth"
_CONFIG = """
[public_web_mcp.read]
provider = "structured_fixture"
label = "Structured Fixture"
endpoint = "https://mcp.example.com/mcp"
tool = "web_fetch"
bearer_token_env = "FIXTURE_PUBLIC_TOKEN"
input_field = "urls"
input_as_list = true
fixed = { limit = 1, mode = "extract" }
result = { rows = ["data", "pages"], url = "address", title = "name", passages = "body" }
"""


@pytest.mark.parametrize("shape", ["valid", "wrong_url", "missing_rows", "non_text"])
def test_structured_reader_resolves_and_runs_with_config_only(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shape: str,
) -> None:
    config_path.write_text(config_path.read_text() + _CONFIG)
    monkeypatch.delenv("FIXTURE_PUBLIC_TOKEN", raising=False)
    monkeypatch.setenv("UNRELATED_DIRECT_MCP_KEY", "do-not-export")
    payloads: list[dict[str, JsonValue]] = []
    public_call = web_mcp.MCPClient.public_call

    def serve(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and str(request.url) == "https://mcp.example.com/mcp"
        assert request.headers["authorization"] == "Bearer " + _TOKEN
        assert "cookie" not in request.headers
        assert _TOKEN.encode() not in request.content and b"do-not-export" not in request.content
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return _rpc(
                1,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "structured", "version": "1"},
                },
            )
        if len(payloads) == 2:
            return httpx.Response(202)
        assert len(payloads) == 3
        row = {
            "address": "https://example.org/other" if shape == "wrong_url" else _URL,
            "name": "Fixture",
            "body": [123]
            if shape == "non_text"
            else ["📖" * 900, "https://example.net/unverified"],
        }
        data = {} if shape == "missing_rows" else {"pages": [row]}
        return _rpc(2, {"content": [], "structuredContent": {"data": data}})

    async def fixture_call(
        self: web_mcp.MCPClient,
        endpoint: str,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        bearer: str,
        ipv4: bool,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None,
    ) -> dict[str, JsonValue]:
        assert transport is None
        return await public_call(
            self,
            endpoint,
            name,
            arguments,
            bearer=bearer,
            ipv4=ipv4,
            timeout=timeout,
            transport=httpx.MockTransport(serve),
        )

    # Replace only the wire in tests; provider selection and decoding come from TOML.
    monkeypatch.setattr(web_mcp.MCPClient, "public_call", fixture_call)

    async def run() -> None:
        config = load_config(config_path)
        runtime = LocalRuntime(config, gateway_available=False)
        try:
            await runtime.start_public_search()
            core = runtime.core
            caps = {cap.id: cap for cap in capability_catalog(core, None)}
            assert caps["public_search"].status == "unconfigured"
            assert caps["read_page"].status == "unavailable"
            assert caps["read_page"].provider_label == "Structured Fixture"
            assert not payloads
            monkeypatch.setenv("FIXTURE_PUBLIC_TOKEN", _TOKEN)
            core.model = SummaryModel('{"answer":"Public evidence.","sources":[1]}')
            turn = await core.prepare(f"Structured Fixture read: {_URL}")
            assert payloads[-1] == {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "web_fetch",
                    "arguments": {"urls": [_URL], "limit": 1, "mode": "extract"},
                },
            }
            assert len(payloads) == 3  # No fallback or retry, including malformed evidence.
            if shape != "valid":
                assert turn.web_evidence is None and "unavailable" in (turn.local_reply or "")
            else:
                assert turn.web_evidence is not None and turn.web_evidence.truncated
                assert len(turn.web_evidence.sources[0].passages[0].encode()) == 2600
                reply = "".join([event.text async for event in core.stream(turn)])
                assert _URL in reply and "omitted or shortened" in reply
                assert "Freshness was not verified" in reply and "unverified" not in reply
        finally:
            await runtime.aclose()
            runtime.close_workers()

    asyncio.run(run())


def test_distinct_search_and_read_authority(config_path: Path) -> None:
    search = (
        _CONFIG.replace(".read]", ".search]")
        .replace("structured_fixture", "search_fixture")
        .replace("Structured Fixture", "Search Fixture")
        .replace("mcp.example.com", "search.example.org")
    )
    config_path.write_text(config_path.read_text() + search + _CONFIG)
    web = asyncio.run(resolve_public_web(load_config(config_path)))
    assert web.search_binding and web.search_binding.provider == "search_fixture"
    assert web.read_binding and web.read_binding.provider == "structured_fixture"
    assert web.search_binding.revision != web.read_binding.revision


def test_snippet_search_and_custom_reader_never_enable_research(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path.write_text(
        config_path.read_text() + _CONFIG + '\n[public_search]\nprovider = "searxng"\n'
        'searxng_url = "http://search.example.org:8095"\n'
    )
    monkeypatch.setenv("FIXTURE_PUBLIC_TOKEN", _TOKEN)

    @asynccontextmanager
    async def unavailable_inspect(*_: object) -> AsyncIterator[None]:
        raise TaskConflict("fixture_no_workspace")
        yield  # pragma: no cover

    def forbidden(*_: object, **__: object) -> None:
        pytest.fail("Split bindings must not start a Research worker")

    monkeypatch.setattr(harness, "open_harness", unavailable_inspect)
    monkeypatch.setattr(research, "open_research", forbidden)

    async def run() -> None:
        settings = HarnessSettings(
            provider="opencode",
            command=(str(tmp_path / "fixture"),),
            revision="2.3.2",
            profile_sha256="0" * 64,
            workspace=tmp_path / "workspace",
            research=True,
        )
        config = load_config(config_path).model_copy(update={"harness": settings})
        runtime = LocalRuntime(config, gateway_available=False)
        instance = uuid4()
        binding = RoomBinding(
            instance_id=instance,
            character_id=runtime.memory.character_id,
            room=f"ember-{instance.hex}",
            endpoint="ws://127.0.0.1:7880",
        )
        try:
            (config.runtime_root / "gateway-agent.token").write_text("f" * 32)
            await runtime.start_public_search()
            web = runtime.core.public_web
            assert web.search_binding and web.search_binding.snippets
            assert web.read_binding and web.read_binding.provider == "structured_fixture"
            async with AsyncExitStack() as cleanup:
                router = await livekit_runtime._start_tasks(runtime, binding, cleanup)
                assert router and not router.research_available
                assert router.research_unavailable == "research_hosted_retrieval_unavailable"
        finally:
            await runtime.aclose()
            runtime.close_workers()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        ('"https://mcp.example.com/mcp"', '"https://other.example.org/mcp"'),
        ('"https://mcp.example.com/mcp"', '"https://mcp.example.com/other"'),
        ('"web_fetch"', '"fetch"'),
        ("input_as_list = true", "input_as_list = false"),
        ("limit = 1", "limit = 2"),
        ('"FIXTURE_PUBLIC_TOKEN"', '"OTHER_PUBLIC_TOKEN"'),
        ('passages = "body"', 'passages = "excerpt"'),
    ],
)
def test_binding_revision_pins_nonsecret_authority(
    config_path: Path, change: tuple[str, str]
) -> None:
    base = config_path.read_text()

    async def revision(section: str) -> str:
        config_path.write_text(base + section)
        web = await resolve_public_web(load_config(config_path))
        assert web.read_binding is not None
        return web.read_binding.revision

    first = asyncio.run(revision(_CONFIG))
    assert first == asyncio.run(revision(_CONFIG))
    assert first != asyncio.run(revision(_CONFIG.replace(*change)))


@pytest.mark.parametrize(
    "section",
    [
        _CONFIG.replace("https://mcp.example.com/", "http://mcp.example.com/"),
        _CONFIG.replace("mcp.example.com", "127.0.0.1"),
        _CONFIG.replace("mcp.example.com", "host.internal"),
        _CONFIG.replace("mcp.example.com", "secret@mcp.example.com"),
        _CONFIG.replace("limit = 1", 'urls = "overlap"'),
        _CONFIG + '\n[public_search]\nprovider = "exa_mcp"',
    ],
)
def test_public_web_mcp_config_rejects_ambiguous_authority(config_path: Path, section: str) -> None:
    config_path.write_text(config_path.read_text() + section)
    with pytest.raises(
        ConfigurationError, match="Public retrieval bindings overlap|public_web_mcp"
    ):
        load_config(config_path)
