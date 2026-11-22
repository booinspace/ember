"""Bounded ephemeral operator observations; never canonical memory or a prompt log."""

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

type ActivityKind = Literal["tool", "memory", "task", "inference", "system"]
type ActivityStatus = Literal[
    "running",
    "waiting_for_realtime",
    "waiting_for_model",
    "waiting_for_tool",
    "waiting_for_user",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "uncertain",
]


class ActivityEvent(BaseModel):
    """Content-free transaction metadata can outlive a turn without retaining recalled text."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    id: UUID
    sequence: int
    started_at: datetime
    kind: ActivityKind
    name: str = Field(max_length=128)
    status: ActivityStatus
    request_id: UUID | None = None
    task_id: UUID | None = None
    duration_ms: float | None = None
    count: int | None = None
    provider: str = Field(default="ember", max_length=64)
    code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,64}$")


@dataclass
class _Activity:
    value: ActivityEvent
    started: float


class ActivityLog:
    """One event-loop-owned ring; emitting never waits on a subscriber, disk, or network."""

    def __init__(self) -> None:
        self.entries: OrderedDict[UUID, _Activity] = OrderedDict()
        self.revision = 0

    def begin(
        self,
        kind: ActivityKind,
        name: str,
        *,
        request_id: UUID | None = None,
        task_id: UUID | None = None,
        provider: str = "ember",
    ) -> UUID:
        identity = uuid4()
        self.revision += 1
        self.entries[identity] = _Activity(
            ActivityEvent(
                id=identity,
                sequence=self.revision,
                started_at=datetime.now(UTC),
                kind=kind,
                name=name[:128],
                status="running",
                request_id=request_id,
                task_id=task_id,
                provider=provider[:64],
            ),
            monotonic(),
        )
        while len(self.entries) > 256:
            self.entries.popitem(last=False)
        return identity

    def update(
        self,
        identity: UUID,
        status: ActivityStatus,
        *,
        count: int | None = None,
        code: str | None = None,
    ) -> None:
        entry = self.entries.get(identity)
        if entry is None:
            return
        self.revision += 1
        entry.value = entry.value.model_copy(
            update={
                "sequence": self.revision,
                "status": status,
                "count": count,
                "code": code,
                "duration_ms": round((monotonic() - entry.started) * 1000, 2),
            }
        )

    def since(self, sequence: int) -> tuple[ActivityEvent, ...]:
        return tuple(
            entry.value for entry in self.entries.values() if entry.value.sequence > sequence
        )

    def clear(self) -> None:
        self.entries.clear()


def notify[**P](callback: Callable[P, None] | None, *args: P.args, **kwargs: P.kwargs) -> bool:
    """Non-authoritative inspection failures are counted by the owner, never generation failures."""
    if callback is None:
        return True
    try:
        callback(*args, **kwargs)
    except Exception:
        return False
    return True
