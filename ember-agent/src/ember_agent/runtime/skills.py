"""One shared operator catalog; selected harnesses own skill parsing and execution."""

import asyncio
import json
import re
from collections.abc import Callable
from typing import Protocol

from pydantic import JsonValue, TypeAdapter, ValidationError

from ..observability import ActivityLog
from .skills_contracts import (
    SkillBundle,
    SkillCatalog,
    SkillDependency,
    SkillDocument,
    SkillSelection,
    SkillSummary,
)
from .task_store import TaskConflict
from .worker_process import WIRE_BYTES, owned_stdio

OBJECT = TypeAdapter(dict[str, JsonValue])


class SkillProvider(Protocol):
    """Harness-owned installed package discovery, inspection and explicit enablement."""

    async def call(self, action: str, value: JsonValue) -> JsonValue:
        """Perform one bounded operator/catalog operation without inference or script execution."""
        ...


class InstalledSkillProvider:
    """Versioned owned-stdio adapter; no external SDK or OS path leaks into Core."""

    def __init__(
        self, command: tuple[str, ...], profile: str, capabilities: tuple[str, ...]
    ) -> None:
        self.command, self.profile, self.capabilities = command, profile, capabilities
        self.lock = asyncio.Lock()

    async def call(self, action: str, value: JsonValue) -> JsonValue:
        request = json.dumps(
            {"action": action, "value": value, "capabilities": self.capabilities}, allow_nan=False
        ).encode()
        if len(request) > WIRE_BYTES:
            raise TaskConflict("skills_request_too_large")
        async with self.lock, asyncio.timeout(30):
            try:
                async with owned_stdio((*self.command, "--skills")) as process:
                    assert process.stdin is not None and process.stdout is not None
                    process.stdin.write(request + b"\n")
                    await process.stdin.drain()
                    process.stdin.close()
                    raw = await process.stdout.readline()
                    if len(raw) > WIRE_BYTES or await process.stdout.read(1):
                        raise TaskConflict("skills_response_too_large")
                    frame = OBJECT.validate_json(raw)
                    if (
                        frame.get("profile_sha256") != self.profile
                        or type(frame.get("protocol")) is not int
                        or frame.get("protocol") != 1
                    ):
                        raise TaskConflict("skills_profile_mismatch")
                    if frame.get("ok") is not True:
                        code = frame.get("code")
                        raise TaskConflict(
                            code
                            if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", code)
                            else "skills_unavailable"
                        )
                    if await process.wait() != 0:
                        raise TaskConflict("skills_provider_exit")
                    return frame.get("result")
            except (OSError, ValueError, ValidationError) as error:
                raise TaskConflict("skills_provider_unavailable") from error


class Skills:
    """Cache metadata only; each resource/authorization read returns to the selected harness."""

    def __init__(self, provider: str, client: SkillProvider) -> None:
        self.client = client
        self.activity: ActivityLog | None = None
        self.catalog = SkillCatalog(
            provider=provider, revision="unavailable", unavailable="skills_not_discovered"
        )
        self.changed: Callable[[], None] | None = None

    async def refresh(self) -> SkillCatalog:
        try:
            self.catalog = SkillCatalog.model_validate_json(
                json.dumps(await self.client.call("list", None))
            )
        except (TaskConflict, TimeoutError, ValidationError):
            self.catalog = SkillCatalog(
                provider=self.catalog.provider,
                revision="unavailable",
                unavailable="skills_provider_unavailable",
            )
        if self.changed:
            self.changed()
        return self.catalog

    @property
    def enabled(self) -> tuple[SkillSummary, ...]:
        return tuple(item for item in self.catalog.skills if item.enabled and not item.unavailable)

    async def inspect(self, identity: str) -> SkillDocument:
        return SkillDocument.model_validate_json(
            json.dumps(await self.client.call("view", identity))
        )

    async def select(self, selection: SkillSelection) -> SkillCatalog:
        self.catalog = SkillCatalog.model_validate_json(
            json.dumps(await self.client.call("select", selection.model_dump(mode="json")))
        )
        if self.changed:
            self.changed()
        return self.catalog

    async def install(self, package: SkillBundle) -> SkillCatalog:
        self.catalog = SkillCatalog.model_validate_json(
            json.dumps(await self.client.call("import", package.model_dump(mode="json")))
        )
        if self.changed:
            self.changed()
        return self.catalog

    async def export(self, identity: str) -> SkillBundle:
        return SkillBundle.model_validate_json(
            json.dumps(await self.client.call("export", identity))
        )

    async def valid(self, dependencies: tuple[SkillDependency, ...]) -> bool:
        if not dependencies:
            return True
        revision = self.catalog.revision
        try:
            result = await self.client.call(
                "check", [item.model_dump(mode="json") for item in dependencies]
            )
            return (
                self.catalog.revision == revision
                and isinstance(result, dict)
                and result.get("valid") is True
            )
        except (TaskConflict, TimeoutError):
            return False

    def metadata(self) -> list[dict[str, JsonValue]]:
        return [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "content_sha256": item.content_sha256,
            }
            for item in self.enabled
        ]
