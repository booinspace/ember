"""Cached operator monitoring and lazy, revocable request inspection; no prompt persistence."""

import asyncio
import csv
import io
import os
import shutil
import subprocess
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID

import psutil
from pydantic import BaseModel, ConfigDict

from ..inference.contracts import (
    GatewayError,
    GatewayEvent,
    GatewayHealth,
    GatewayRequest,
    GenerationUsage,
)
from ..observability import ActivityLog


class SystemSample(BaseModel):
    """Unknown sensors remain null instead of displaying fabricated zero load."""

    model_config = ConfigDict(strict=True, frozen=True)
    cpu_percent: float | None = None
    memory_percent: float | None = None
    memory_used_bytes: int | None = None
    memory_total_bytes: int | None = None
    process_rss_bytes: int | None = None
    gpu_name: str | None = None
    gpu_percent: float | None = None
    gpu_used_mib: float | None = None
    gpu_total_mib: float | None = None


@dataclass
class RequestView:
    request: GatewayRequest
    captured_at: datetime
    budget: int
    validate: Callable[[], Awaitable[bool]]
    activity_id: UUID
    input_units: int | None = None
    usage: GenerationUsage | None = None
    failure_code: str | None = None
    expires_at: float = 0.0


def _system_sample() -> SystemSample:
    values: dict[str, str | float | int | None] = {}
    try:
        memory = psutil.virtual_memory()
        values.update(
            cpu_percent=float(psutil.cpu_percent(interval=None)),
            memory_percent=float(memory.percent),
            memory_used_bytes=memory.used,
            memory_total_bytes=memory.total,
            process_rss_bytes=psutil.Process().memory_info().rss,
        )
    except (OSError, psutil.Error):
        pass
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            result = subprocess.run(
                [
                    executable,
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=1,
                check=True,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            rows = list(csv.reader(io.StringIO(result.stdout[:4096])))
            if rows and len(rows[0]) == 4:
                name, load, used, total = rows[0]
                values.update(
                    gpu_name=name.strip(),
                    gpu_percent=float(load),
                    gpu_used_mib=float(used),
                    gpu_total_mib=float(total),
                )
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return SystemSample.model_validate(values)


async def _public_request() -> bool:
    return True


class Dashboard:
    """Retain eight immutable requests, metadata events and one off-loop cached host sample."""

    def __init__(
        self,
        *,
        model: str,
        backend: str,
        context_tokens: int,
        health: Callable[[], Awaitable[GatewayHealth]],
    ) -> None:
        self.model, self.backend, self.context_tokens = model, backend, context_tokens
        self.health = health
        self.activity = ActivityLog()
        self.requests: OrderedDict[UUID, RequestView] = OrderedDict()
        self.last_usage: GenerationUsage | None = None
        self.system = SystemSample()
        self.gateway: GatewayHealth | None = None
        self.sampled_at = 0.0
        self.lock = asyncio.Lock()
        self.started = monotonic()
        self.closed = False

    def capture(
        self,
        request: GatewayRequest,
        *,
        budget: int = 16384,
        input_units: int | None = None,
        validate: Callable[[], Awaitable[bool]] = _public_request,
    ) -> None:
        """Keep the actual request; serialize only on authenticated inspection."""
        if self.closed or request.request_id in self.requests:
            return
        self.retire_context(request.context_id)
        activity_id = self.activity.begin(
            "inference", request.workload + " model request", request_id=request.request_id
        )
        self.requests[request.request_id] = RequestView(
            request,
            datetime.now(UTC),
            budget,
            validate,
            activity_id,
            input_units=input_units,
            expires_at=monotonic() + 300,
        )
        while len(self.requests) > 8:
            self.requests.popitem(last=False)

    def retire_context(self, context_id: UUID) -> None:
        """Input replacement revokes old inspection even while a browser validation is awaiting."""
        for identity, entry in tuple(self.requests.items()):
            if entry.request.context_id == context_id:
                self.requests.pop(identity, None)

    def event(self, event: GatewayEvent) -> None:
        """Decode tokens never enter monitoring; only terminal counters update the dashboard."""
        entry = self.requests.get(event.request_id)
        if entry is None:
            return
        if event.kind == "end":
            entry.usage = event.usage
            self.last_usage = event.usage
            self.activity.update(entry.activity_id, "succeeded")
        elif event.kind == "error":
            entry.failure_code = event.code
            self.activity.update(entry.activity_id, "failed", code=event.code)

    def finish(self, identity: UUID, complete: bool) -> None:
        """Stream cancellation is separate from the gateway's physical release state."""
        if entry := self.requests.get(identity):
            self.activity.update(
                entry.activity_id,
                "failed" if entry.failure_code else "succeeded" if complete else "cancelled",
                code=entry.failure_code,
            )

    async def sample(self) -> None:
        if self.closed or monotonic() - self.sampled_at < 3:
            return
        async with self.lock:
            if monotonic() - self.sampled_at < 3:
                return

            async def gateway() -> GatewayHealth | None:
                try:
                    async with asyncio.timeout(1.5):
                        return await self.health()
                except (GatewayError, TimeoutError, OSError):
                    return None

            self.system, self.gateway = await asyncio.gather(
                asyncio.to_thread(_system_sample), gateway()
            )
            self.sampled_at = monotonic()

    async def inspect(self, identity: UUID, *, validate_only: bool = False) -> dict[str, object]:
        """Validate on every disclosure and never substitute a truncated approximation."""
        entry = self.requests.get(identity)
        if entry is None or monotonic() > entry.expires_at:
            self.requests.pop(identity, None)
            raise GatewayError("prompt_inspection_expired")
        if not await entry.validate():
            self.requests.pop(identity, None)
            raise GatewayError("context_invalidated")
        if (
            self.requests.get(identity) is not entry
            or self.closed
            or monotonic() > entry.expires_at
        ):
            self.requests.pop(identity, None)
            raise GatewayError("prompt_inspection_expired")
        if validate_only:
            return {"valid": True}
        request = entry.request.model_dump(mode="json")
        return {
            "request": request,
            "captured_at": entry.captured_at.isoformat(),
            "input_budget_units": entry.budget,
            "input_units": entry.input_units,
            "model_context_tokens": self.context_tokens,
            "usage": entry.usage.model_dump(mode="json") if entry.usage else None,
        }

    async def snapshot(self, after: int = 0) -> dict[str, object]:
        await self.sample()
        for identity, entry in tuple(self.requests.items()):
            if monotonic() > entry.expires_at:
                self.requests.pop(identity, None)
        headers = [
            {
                "request_id": str(identity),
                "context_id": str(entry.request.context_id),
                "model": entry.request.model,
                "workload": entry.request.workload,
                "captured_at": entry.captured_at.isoformat(),
                "messages": len(entry.request.messages),
                "tools": len(entry.request.tools),
                "input_budget_units": entry.budget,
                "input_units": entry.input_units,
                "input_bytes": sum(
                    len(message.content.encode()) for message in entry.request.messages
                ),
                "prompt_tokens": entry.usage.prompt_tokens if entry.usage else None,
            }
            for identity, entry in self.requests.items()
        ]
        return {
            "revision": self.activity.revision,
            "events": [
                event.model_dump(mode="json")
                for event in self.activity.since(0 if after > self.activity.revision else after)
            ],
            "requests": headers,
            "system": self.system.model_dump(),
            "gateway": self.gateway.model_dump(mode="json") if self.gateway else None,
            "model": self.model,
            "backend": self.backend,
            "context_tokens": self.context_tokens,
            "usage": self.last_usage.model_dump(mode="json") if self.last_usage else None,
            "uptime_seconds": round(monotonic() - self.started),
            "sample_seconds": 3,
        }

    async def aclose(self) -> None:
        self.closed = True
        self.requests.clear()
        self.activity.clear()
