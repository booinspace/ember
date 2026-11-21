"""Delegated notes use only the reserved library and normal recall policy."""

import asyncio
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.runtime.memory_mcp import NoteResult, serve_memory_mcp
from ember_memory_abi.errors import AccessDeniedError
from ember_memory_abi.interfaces import ArchiveQuery, ForgetRequest, MemoryQuery, MutationReceipt
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.errors import EngineBusyError
from ember_memory_engine.library import KnowledgeSelection, LibraryService, SeedRequest
from ember_memory_engine.read_budget import ReadBudget

from .test_memory_mcp import TOKEN


@pytest.mark.integration
def test_opencode_note_write(tmp_path: Path) -> None:
    root = tmp_path / "data"
    authority = MutationAuthority(mode="opencode_notes")
    note = "Saffron backups run every Monday."

    async def exercise() -> None:
        with CanonicalEngine(root) as engine:
            character = engine.create_character().store_id
            other = engine.create_character().store_id
            unrelated = engine.create_library("Other").store_id
            service = LibraryService(engine, authority=authority)
            library = service.ensure_opencode_library()
            assert service.ensure_opencode_library() == library != unrelated
            with pytest.raises(AccessDeniedError):
                LibraryService(engine).save_opencode_note(character, note)
            writer = engine.character(character, authority=MutationAuthority(mode="confirmed_user"))
            memory = InProcessAsyncMemoryClient(writer)

            def deep_client(deadline: float) -> InProcessAsyncMemoryClient:
                budget = ReadBudget(deadline=deadline)
                return InProcessAsyncMemoryClient(
                    writer, deep_sources=DeepSources(engine, budget), read_budget=budget
                )

            deep = DeepRecallService(character, deep_client, validation=memory)

            async def save(text: str) -> MutationReceipt:
                return await asyncio.to_thread(service.save_opencode_note, character, text)

            (tmp_path / "memory-mcp.token").write_text(TOKEN, encoding="ascii")
            try:
                async with serve_memory_mcp(
                    memory, tmp_path, 0, deep=deep, timezone="UTC", write_note=save
                ) as port:
                    url = f"http://127.0.0.1:{port}/mcp"
                    async with httpx2.AsyncClient(trust_env=False) as http:
                        assert (await http.get(url)).status_code == 401
                        http.headers["Authorization"] = f"Bearer {TOKEN}"
                        async with (
                            streamable_http_client(url, http_client=http) as (reader, output),
                            ClientSession(reader, output) as session,
                        ):
                            assert "save_memory" in (
                                (await session.initialize()).instructions or ""
                            )
                            for invalid in ("  ", "line one\nline two", "x" * 513):
                                assert (
                                    await session.call_tool("save_memory", {"note": invalid})
                                ).is_error
                            tools = (await session.list_tools()).tools
                            save_tool = next(tool for tool in tools if tool.name == "save_memory")
                            assert set(save_tool.input_schema["properties"]) == {"note"}
                            # Library commit survives a failed adoption; exact retry completes it.
                            with patch.object(service, "_seed", side_effect=EngineBusyError):
                                failed = await session.call_tool("save_memory", {"note": note})
                                assert failed.is_error
                                assert isinstance(failed.content[0], TextContent)
                                assert note not in failed.content[0].text
                                assert not writer.search(MemoryQuery()).results
                            ids = []
                            for outcome in ("applied", "already_applied"):
                                # The SDK ignores unknown arguments; they cannot route the write.
                                response = await session.call_tool(
                                    "save_memory", {"note": note, "library": "Other"}
                                )
                                assert not response.is_error
                                assert isinstance(response.content[0], TextContent)
                                assert note not in response.content[0].text
                                result = NoteResult.model_validate_json(response.content[0].text)
                                assert result.library == "OpenCode" and result.outcome == outcome
                                ids.append(result.memory_id)
                            assert ids[0] == ids[1]
                            hit = writer.get(ids[0])
                            assert (
                                hit.origin is not None
                                and hit.origin.origin_source_store_id == library
                            )
                            selection = KnowledgeSelection(
                                knowledge_id=hit.origin.origin_memory_id, revision=1
                            )
                            approved = service.knowledge(library, selection)
                            assert not approved.approval.manual_override
                            assert approved.approval.policy.name == "ember.opencode_note"
                            assert writer.get_sources(hit.memory_id)[0].quote == note
                            assert not writer.search_archive(ArchiveQuery()).results
                            assert not engine.character(other).search(MemoryQuery()).results
                            with pytest.raises(AccessDeniedError):
                                service.seed(
                                    other,
                                    SeedRequest(
                                        idempotency_key="forbidden",
                                        library_id=library,
                                        selections=(selection,),
                                    ),
                                )
                            for tool in ("recall", "deep_recall"):
                                result = await session.call_tool(tool, {"query": "saffron backups"})
                                assert not result.is_error and isinstance(
                                    result.content[0], TextContent
                                )
                                assert note in result.content[0].text
                            await memory.configure_recall(
                                RecallSelection(excluded_libraries=(library,))
                            )
                            for tool in ("recall", "deep_recall"):
                                result = await session.call_tool(tool, {"query": "saffron backups"})
                                assert not result.is_error and result.structured_content is not None
                                assert result.structured_content["memories"] == []
            finally:
                await deep.aclose()
                await memory.aclose()

        with CanonicalEngine(root) as engine:
            service = LibraryService(engine, authority=authority)
            receipt = service.save_opencode_note(character, note)
            assert receipt.outcome == "already_applied"
            assert service.ensure_opencode_library() == library
            writer = engine.character(character, authority=MutationAuthority(mode="confirmed_user"))
            writer.forget(
                ForgetRequest(
                    idempotency_key="forget",
                    memory_id=receipt.affected[0].memory_id,
                    expected_revision=1,
                    reason="Synthetic suppression test",
                )
            )
            with pytest.raises(AccessDeniedError):
                service.save_opencode_note(character, note)

    asyncio.run(exercise())
