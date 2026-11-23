"""Offline tests for registered text formats and pinned public MCP endpoints."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import httpx
import pytest
from pydantic import JsonValue

from ember_agent.runtime import web_mcp_decoders
from ember_agent.runtime.config import ConfigurationError, load_config
from ember_agent.runtime.web_mcp import EXA_READ, EXA_SEARCH, MCPWeb
from ember_agent.runtime.web_mcp_decoders import ResultDecoder
from ember_agent.web_evidence import PublicWebOperation, WebEvidence, WebSource, web_failure

from .test_config import config_path as config_path
from .test_exa_mcp import _rpc

_CONFIG = """
[public_web_mcp.search]
provider = "text_fixture"
label = "Text Fixture"
endpoint = "https://mcp.example.com/tools?a=search,read"
tool = "find"
input_field = "query"
result = "line_fixture"
"""


def _decode_line(
    provider: str, operation: PublicWebOperation, value: str, result: dict[str, JsonValue]
) -> WebEvidence:
    assert operation == "search" and value == "public transit"
    if result.get("content") != [{"type": "text", "text": "TRANSIT|Public map guide."}]:
        return web_failure(provider, operation, "invalid_response")
    return WebEvidence(
        provider=provider,
        operation=operation,
        retrieved_at=datetime.now(UTC),
        coverage="partial",
        sources=(
            WebSource(
                url="https://example.org/transit", title="Transit", passages=("Public map guide.",)
            ),
        ),
    )


def test_registered_text_decoder_and_its_guard_need_no_dispatch_changes(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        web_mcp_decoders,
        "_NAMED_DECODERS",
        MappingProxyType(
            {
                "line_fixture": ResultDecoder(
                    _decode_line, allows_input=lambda _, value: not value.startswith("mode:")
                )
            }
        ),
    )
    config_path.write_text(config_path.read_text() + _CONFIG)
    settings = load_config(config_path).public_web_mcp
    assert settings is not None and settings.search is not None
    payloads: list[dict[str, JsonValue]] = []

    def serve(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://mcp.example.com/tools?a=search,read"
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return _rpc(
                1,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "text-fixture", "version": "1"},
                },
            )
        if len(payloads) == 2:
            return httpx.Response(202)
        assert len(payloads) == 3
        return _rpc(2, {"content": [{"type": "text", "text": "TRANSIT|Public map guide."}]})

    retrieve = MCPWeb(settings.search, "search", transport=httpx.MockTransport(serve))
    rejected = asyncio.run(retrieve("mode: public transit"))
    assert rejected.failure == "input_rejected" and not payloads
    evidence = asyncio.run(retrieve("public transit"))
    assert evidence.provider == "text_fixture" and evidence.coverage == "partial"
    assert evidence.sources[0].passages == ("Public map guide.",)
    assert [message["method"] for message in payloads] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert payloads[-1]["params"] == {"name": "find", "arguments": {"query": "public transit"}}


def test_unknown_decoder_fails_at_config_load(config_path: Path) -> None:
    config_path.write_text(config_path.read_text() + _CONFIG)
    with pytest.raises(ConfigurationError, match=r"Invalid \[public_web_mcp\] binding"):
        load_config(config_path)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://MCP.example.com/tools",
        "https://mcp.example.com/path/../tools",
        "https://mcp.example.com/path/./tools",
        "https://mcp.example.com/tools?selector=<search>",
    ],
)
def test_endpoint_normalized_by_httpx_fails_at_config_load(
    config_path: Path, endpoint: str
) -> None:
    assert str(httpx.URL(endpoint)) != endpoint
    section = _CONFIG.replace('result = "line_fixture"', 'result = "exa_text"').replace(
        "https://mcp.example.com/tools?a=search,read", endpoint
    )
    config_path.write_text(config_path.read_text() + section)
    with pytest.raises(ConfigurationError, match=r"Invalid \[public_web_mcp\] binding"):
        load_config(config_path)


def test_exact_legacy_exa_endpoint_remains_valid(config_path: Path) -> None:
    endpoint = "https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa"
    section = _CONFIG.replace('result = "line_fixture"', 'result = "exa_text"').replace(
        "https://mcp.example.com/tools?a=search,read", endpoint
    )
    config_path.write_text(config_path.read_text() + section)
    settings = load_config(config_path).public_web_mcp
    assert settings is not None and settings.search is not None
    assert settings.search.endpoint == EXA_SEARCH.endpoint == EXA_READ.endpoint == endpoint
