"""Copied skill authority is rechecked through every native task delivery surface."""

import asyncio
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest
from pydantic import JsonValue

from ember_agent.runtime.skills import Skills
from ember_agent.runtime.skills_contracts import SkillDependency
from ember_agent.runtime.task_router import TaskResult, TaskRouter, ToolContext, ToolReceipt
from ember_agent.runtime.task_store import TaskConflict, TaskSubmission


class SkillAuthority:
    def __init__(self) -> None:
        self.allowed = True

    async def call(self, action: str, value: JsonValue) -> JsonValue:
        assert action == "check"
        return {"valid": self.allowed}


class ResultWithSkill:
    async def execute(
        self, request: TaskSubmission, context: ToolContext, progress: object
    ) -> TaskResult:
        return TaskResult(
            summary="SYNTHETIC_SKILL_RESULT",
            receipt=ToolReceipt(context=context, tool="skill", data={}),
            skill_dependencies=(SkillDependency(id="shared/brief", content_sha256="a" * 64),),
        )


def test_disabled_skill_invalidates_status_notifications_receipt_and_export(tmp_path: Path) -> None:
    async def run() -> None:
        authority = SkillAuthority()
        router = TaskRouter(tmp_path, uuid4(), uuid4(), ResultWithSkill())
        router.skills = Skills("fixture", authority)
        await router.start()
        try:
            task = await router.submit("synthetic", "A synthetic skill task")
            deadline = monotonic() + 5
            while (
                state := await router.status(task.task_id)
            ) is None or state.state != "succeeded":
                assert monotonic() < deadline
                await asyncio.sleep(0.01)
            assert state.result == "SYNTHETIC_SKILL_RESULT"
            await router.save_artifact(task.task_id)
            assert await router.read_artifact(task.task_id) == b"SYNTHETIC_SKILL_RESULT"
            authority.allowed = False
            assert (await router.status(task.task_id)).result is None
            assert (await router.receipt(task.task_id)).receipt is None
            assert (await router.notifications(None)).items[0].result is None
            with pytest.raises(TaskConflict):
                await router.read_artifact(task.task_id)
            with pytest.raises(TaskConflict):
                await router.save_artifact(task.task_id)
            router.skills = None
            assert (await router.status(task.task_id)).result is None
        finally:
            await router.aclose()

    asyncio.run(run())


def test_skill_revocation_during_artifact_io_is_checked_again(tmp_path: Path) -> None:
    async def run() -> None:
        authority = SkillAuthority()
        router = TaskRouter(tmp_path, uuid4(), uuid4(), ResultWithSkill())
        router.skills = Skills("fixture", authority)
        await router.start()
        try:
            task = await router.submit("synthetic", "A synthetic skill task")
            deadline = monotonic() + 5
            while (
                state := await router.status(task.task_id)
            ) is None or state.state != "succeeded":
                assert monotonic() < deadline
                await asyncio.sleep(0.01)
            await router.save_artifact(task.task_id)
            original = router.artifacts.read

            async def revoke(value: object) -> bytes:
                data = await original(value)
                authority.allowed = False
                return data

            router.artifacts.read = revoke
            with pytest.raises(TaskConflict, match="context_invalidated"):
                await router.read_artifact(task.task_id)
        finally:
            await router.aclose()

    asyncio.run(run())
