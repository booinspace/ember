"""Check legacy MCP over real loopback with synthetic canonical memory."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.runtime.config import ConfigurationError, load_config
from ember_agent.runtime.memory_mcp import DeepMCPResult, RecallResult, serve_memory_mcp
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
    deployment.write_text(example + '\n[memory_mcp]\nhost = "0.0.0.0"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(deployment)


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
