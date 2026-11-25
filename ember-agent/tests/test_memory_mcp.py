"""Check legacy MCP over real loopback with synthetic canonical memory."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.runtime.config import BrowserSettings, ConfigurationError, load_config
from ember_agent.runtime.memory_mcp import (
    DeepMCPResult,
    RecallClient,
    RecallResult,
    serve_memory_mcp,
)
from ember_agent.runtime.network import network_info
from ember_memory_abi.interfaces import ForgetRequest
from ember_memory_abi.related import RelatedResponse
from ember_memory_abi.selection import RecallSelection
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.read_budget import ReadBudget

EXAMPLE = Path(__file__).parents[1] / "config/deployment.example.toml"
TOKEN = "synthetic-test-token_01234567890123456789012345"


def test_memory_mcp_config_is_opt_in_and_loopback_only(tmp_path: Path) -> None:
    assert load_config(EXAMPLE).memory_mcp is None
    deployment = tmp_path / "deployment.toml"
    example = EXAMPLE.read_text(encoding="utf-8")
    deployment.write_text(example + "\n[memory_mcp]\nport = 8791\n", encoding="utf-8")
    config = load_config(deployment)
    assert config.memory_mcp is not None and config.memory_mcp.port == 8791
    assert not config.memory_mcp.write_enabled and not config.memory_mcp.lan_bind
    deployment.write_text(example + "\n[memory_mcp]\nwrite_enabled = true\n", encoding="utf-8")
    enabled = load_config(deployment).memory_mcp
    assert enabled is not None and enabled.write_enabled
    deployment.write_text(example + '\n[memory_mcp]\nhost = "0.0.0.0"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(deployment)


def test_memory_mcp_lan_requires_https_and_adds_proxy_scoped_firewall_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = tmp_path / "deployment.toml"
    lan = (
        EXAMPLE.read_text(encoding="utf-8")
        .replace(
            'endpoint = "ws://127.0.0.1:7880"\nmode = "attach"',
            'endpoint = "ws://127.0.0.1:7880"\nmode = "managed"',
        )
        .replace("[browser]\nenabled = false", "[browser]\nenabled = true")
        .replace('lan_address = ""', 'lan_address = "10.254.254.11"')
        + "\n[memory_mcp]\nlan_bind = true\n"
    )
    deployment.write_text(lan, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="requires browser HTTPS ingress"):
        load_config(deployment)

    https = lan.replace(
        'public_origin = ""', 'public_origin = "https://ember.example.test"'
    ).replace('trusted_proxy = ""', 'trusted_proxy = "10.254.254.10"')
    for port in (8787, 7880, 7881):
        deployment.write_text(https + f"port = {port}\n", encoding="utf-8")
        with pytest.raises(ConfigurationError, match="distinct TCP port"):
            load_config(deployment)

    deployment.write_text(https + "port = 8791\n", encoding="utf-8")
    monkeypatch.setattr("ember_agent.runtime.network.shutil.which", lambda _: "livekit-server.exe")
    config = load_config(deployment)
    assert config.memory_mcp is not None and config.memory_mcp.lan_bind
    rules = network_info(config).rules
    assert len(rules) == 5
    proxy_rule = rules[-1]
    assert (proxy_rule.protocol, proxy_rule.port, proxy_rule.remote_address) == (
        "TCP",
        8791,
        "10.254.254.10",
    )
    assert proxy_rule.program == rules[0].program
    assert all(rule.remote_address == "LocalSubnet" for rule in rules[:-1])


@pytest.mark.integration
@pytest.mark.parametrize("proxy", ["127.0.0.1", "10.254.254.10"])
def test_memory_mcp_lan_checks_peer_scheme_bearer_host_and_origin(
    tmp_path: Path, proxy: str
) -> None:
    async def exercise() -> None:
        # Loopback stands in for the interface so the test needs no LAN configuration.
        lan = BrowserSettings(
            enabled=True,
            lan_address="127.0.0.1",
            public_origin="https://ember.example.test",
            trusted_proxy=proxy,
        )
        (tmp_path / "memory-mcp.token").write_text(TOKEN, encoding="ascii")
        auth = {
            "Authorization": f"Bearer {TOKEN}",
            "X-Forwarded-Proto": "https",
            "Accept": "application/json, text/event-stream",
        }
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        async with (
            serve_memory_mcp(
                Mock(spec=RecallClient),
                tmp_path,
                0,
                deep=Mock(spec=DeepRecallService),
                timezone="Europe/London",
                lan=lan,
            ) as port,
            httpx2.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5
            ) as client,
        ):
            if proxy != "127.0.0.1":
                spoofed = {**auth, "Host": "ember.example.test", "X-Forwarded-For": proxy}
                assert (await client.post("/mcp", headers=spoofed, json=call)).status_code == 403
                return
            for headers, status in (
                ({"X-Forwarded-Proto": "https"}, 401),
                ({**auth, "X-Forwarded-Proto": "http"}, 403),
                ({**auth, "Host": "other.example"}, 421),
                ({**auth, "Origin": "https://ember.example.test"}, 403),
            ):
                assert (await client.post("/mcp", headers=headers, json=call)).status_code == status
            for headers in (auth, {**auth, "Host": "ember.example.test"}):
                response = await client.post("/mcp", headers=headers, json=call)
                assert response.status_code == 200
                assert {tool["name"] for tool in response.json()["result"]["tools"]} == {
                    "recall",
                    "deep_recall",
                }

    asyncio.run(exercise())


@pytest.mark.integration
def test_memory_mcp_auth_read_scope_and_live_policy(tmp_path: Path) -> None:
    async def exercise() -> None:
        authority = MutationAuthority(mode="confirmed_user")
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character().store_id
            store = engine.character(character, authority=authority)
            memory = InProcessAsyncMemoryClient(store)

            def deep_client(deadline: float) -> InProcessAsyncMemoryClient:
                budget = ReadBudget(deadline=deadline)
                return InProcessAsyncMemoryClient(
                    store, deep_sources=DeepSources(engine, budget), read_budget=budget
                )

            deep = DeepRecallService(character, deep_client, validation=memory)
            author = MemoryAuthoringService(
                engine, ensure_subject(engine, character), authority=authority
            )
            note = author.create(
                NewMemoryInput(
                    idempotency_key="keep",
                    text="Cobalt aquarium is called Moonbeam.",
                    temporal=TemporalBounds(
                        valid_from="2025-07-01T00:00:00.000000Z",
                        valid_until="2025-07-02T00:00:00.000000Z",
                    ),
                )
            ).affected[0]
            forgotten = author.create(
                NewMemoryInput(idempotency_key="forget", text="Cobalt aquarium forgotten secret.")
            ).affected[0]
            store.forget(
                ForgetRequest(
                    idempotency_key="suppress",
                    memory_id=forgotten.memory_id,
                    expected_revision=forgotten.revision,
                    reason="Synthetic suppression test",
                )
            )
            other = engine.create_character().store_id
            MemoryAuthoringService(
                engine, ensure_subject(engine, other), authority=authority
            ).create(
                NewMemoryInput(
                    idempotency_key="other", text="Cobalt aquarium other character secret."
                )
            )
            sink = ArchiveDialogueSink(engine.archive(character))
            await sink.begin_input(
                uuid4(),
                uuid4(),
                "2026-11-04T12:00:00.000000Z",
                "Cobalt aquarium transcript secret.",
            )
            await sink.aclose()

            def row_counts() -> tuple[int, ...]:
                return store.database.read(
                    lambda db: tuple(
                        db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                        for table in ("memory_revisions", "source_records")
                    )
                )

            @asynccontextmanager
            async def listener() -> AsyncGenerator[int]:
                async with serve_memory_mcp(
                    memory, tmp_path, 0, deep=deep, timezone="Europe/London"
                ) as port:
                    yield port

            try:
                with pytest.raises(ConfigurationError, match="memory-mcp.token"):
                    async with listener():
                        pytest.fail("Listener started without credentials")
                (tmp_path / "memory-mcp.token").write_text(TOKEN, encoding="ascii")
                async with listener() as port:
                    url = f"http://127.0.0.1:{port}/mcp"
                    auth = {"Authorization": f"Bearer {TOKEN}"}
                    async with httpx2.AsyncClient(trust_env=False, timeout=5) as http:
                        assert (await http.get(url)).status_code == 401
                        assert (
                            await http.get(url, headers={"Authorization": "Bearer no"})
                        ).status_code == 401
                        assert (
                            await http.get(url, headers={**auth, "Host": "invalid"})
                        ).status_code == 421
                        assert (
                            await http.get(url, headers={**auth, "Origin": "null"})
                        ).status_code == 403
                        http.headers.update(auth)
                        before = row_counts()
                        async with (
                            streamable_http_client(url, http_client=http) as (reader, writer),
                            ClientSession(reader, writer) as client,
                        ):
                            initialized = await client.initialize()
                            assert (
                                initialized.instructions
                                and "untrusted data" in initialized.instructions
                            )
                            tools = (await client.list_tools()).tools
                            assert {tool.name for tool in tools} == {"recall", "deep_recall"}
                            assert all(
                                tool.annotations and tool.annotations.read_only_hint
                                for tool in tools
                            )
                            for name, contract in (
                                ("recall", RecallResult),
                                ("deep_recall", DeepMCPResult),
                            ):
                                result = await client.call_tool(name, {"query": "cobalt aquarium"})
                                assert not result.is_error
                                assert isinstance(result.content[0], TextContent)
                                assert "secret" not in result.content[0].text
                                assert "Moonbeam" in result.content[0].text
                                assert result.structured_content is not None
                                assert {
                                    "observations",
                                    "dependencies",
                                    "manifest",
                                    "context_id",
                                }.isdisjoint(result.structured_content)
                                recall = contract.model_validate_json(result.content[0].text)
                                assert [(m.memory_id, m.revision) for m in recall.memories] == [
                                    (note.memory_id, note.revision)
                                ]
                                for invalid in (" ", "x" * 513):
                                    assert (
                                        await client.call_tool(name, {"query": invalid})
                                    ).is_error
                            dated = await client.call_tool(
                                "deep_recall", {"query": "aquarium in summer 2020"}
                            )
                            assert not dated.is_error and dated.structured_content is not None
                            assert dated.structured_content["scope"]["axis"] == "event"
                            assert dated.structured_content["memories"] == []
                            matched = await client.call_tool(
                                "deep_recall", {"query": "aquarium in summer 2025"}
                            )
                            assert not matched.is_error
                            assert isinstance(matched.content[0], TextContent)
                            dated_recall = DeepMCPResult.model_validate_json(
                                matched.content[0].text
                            )
                            assert [m.memory_id for m in dated_recall.memories] == [note.memory_id]
                            with patch.object(
                                memory,
                                "lookup_related",
                                return_value=RelatedResponse(status="complete", valid=False),
                            ) as validate:
                                stale = await client.call_tool(
                                    "deep_recall", {"query": "cobalt aquarium"}
                                )
                                validate.assert_awaited_once()
                            assert stale.is_error
                            assert isinstance(stale.content[0], TextContent)
                            assert "Moonbeam" not in stale.content[0].text
                            assert row_counts() == before
                            await memory.configure_recall(RecallSelection(local_enabled=False))
                            for name in ("recall", "deep_recall"):
                                result = await client.call_tool(name, {"query": "cobalt aquarium"})
                                assert not result.is_error and result.structured_content is not None
                                assert result.structured_content["memories"] == []
                async with httpx2.AsyncClient(trust_env=False, timeout=2) as http:
                    # Windows may drop SYNs to a recently closed exclusive listener.
                    with pytest.raises((httpx2.ConnectError, httpx2.ConnectTimeout)):
                        await http.get(url, headers=auth)
            finally:
                await deep.aclose()
                await memory.aclose()

    asyncio.run(exercise())
