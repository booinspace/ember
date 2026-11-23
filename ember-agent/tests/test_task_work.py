"""Receipt-backed work stays in its conversation and generated files remain inert."""

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from ember_agent.character.core import CharacterCore, ConversationTask
from ember_agent.character.task_work import WorkUnavailable
from ember_agent.inference.contracts import ToolCall
from ember_agent.runtime.build_artifacts import BuildArtifacts, preview_member
from ember_agent.runtime.task_router import (
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolRouter,
)
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_character import FakeModel
from .test_mcp_tasks import terminal


def test_completion_delivered_once_and_lookup_after_dismissal(tmp_path: Path) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "canonical") as engine:
            character = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(character))
            core = CharacterCore(memory, FakeModel(), model_tag="fixture", recall_enabled=False)
            router = TaskRouter(
                tmp_path,
                character,
                uuid4(),
                TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
            )
            router.conversation_id = core.context_id
            core.lookup_work = router.conversation_work
            await router.start()
            try:
                hidden = await router.submit("operator-only", "fixture")
                visible = await router.submit("conversation", "two words", turn_id=uuid4())
                assert (await terminal(router, visible.task_id)).state == "succeeded"
                await router.acknowledge(visible.task_id)
                assert not await router.conversation_work(uuid4(), None, "")
                core.task_tools["count"] = ConversationTask("Count exact text", router.submit_turn)
                call = ToolCall(call_id=uuid4(), name="count", arguments={"text": "hello"})
                with patch(
                    "ember_agent.character.core.propose_tool", new=AsyncMock(return_value=call)
                ) as probe:
                    native = await core.prepare("Count hello")
                    assert native.request.tools and not native.work_sequence
                    assert str(visible.task_id) not in native.request.model_dump_json()
                    assert str(visible.task_id) not in probe.await_args.args[1].model_dump_json()
                with patch("ember_agent.character.core.propose_tool", side_effect=TimeoutError):
                    turn = await core.prepare("Hello")
                core.task_tools.clear()
                assert "2 words" in turn.request.model_dump_json()
                assert str(hidden.task_id) not in turn.request.model_dump_json()
                assert not turn.request.tools
                assert [event async for event in core.stream(turn, defer_delivery=True)]
                assert core.work_sequence == 0
                await core.acknowledge(
                    turn.request.request_id, "Synthetic visible reply", complete=True
                )
                assert core.work_sequence > 0
                assert (
                    str(visible.task_id)
                    not in (await core.prepare("Hello again")).request.model_dump_json()
                )
                followup = await core.prepare(
                    f"Task {str(visible.task_id)[:8]}: explain the result"
                )
                assert (
                    "2 words" in followup.request.model_dump_json() and not followup.request.tools
                )
                assert (await core.prepare(f"Task {hidden.task_id}")).local_reply
                core.lookup_work = AsyncMock(side_effect=WorkUnavailable())
                assert "temporarily unavailable" in (
                    (await core.prepare("Recent work")).local_reply or ""
                )
            finally:
                await router.aclose()
                await memory.aclose()

    asyncio.run(run())


def test_bounded_file_preview(tmp_path: Path) -> None:
    async def run() -> None:
        files = BuildArtifacts(tmp_path)
        identity = uuid4()
        content = b"<script>" + b"x" * (32767 - 8) + "é".encode()
        rows = [["src/index.html", base64.b64encode(content).decode()], ["binary", "AA=="]]
        meta = await files.save(identity, json.dumps(rows).encode())
        data = await files.read(identity, meta["sha256"], meta["size_bytes"])
        view = preview_member(data, "src/index.html")
        assert view.text and view.text.startswith("<script>") and view.truncated
        assert len(view.text) == 32767 and view.size_bytes == len(content)
        assert preview_member(data, "binary").text is None
        with pytest.raises(KeyError):
            preview_member(data, "../index.html")
        with pytest.raises(ValueError):
            await files.read(identity, "0" * 64, meta["size_bytes"])

    asyncio.run(run())
