"""One bounded ephemeral browser view; never a transcript, prompt or memory source."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, TypeAdapter

from ember_memory_abi.memory import Memory
from ember_memory_abi.related import ContextDependencies

from ..runtime.config import RuntimeModel
from .prompt_budget import recall_value

type MemoryCategory = Literal["profile", "recall", "deep"]
type TurnStage = Literal[
    "preparing",
    "generating",
    "thinking",
    "answering",
    "awaiting_delivery",
    "complete",
    "interrupted",
    "failed",
    "invalidated",
]


class RecalledValue(RuntimeModel):
    """An exact selected interpretation, with semantic qualifiers and no source quote."""

    category: MemoryCategory
    data: dict[str, JsonValue] = Field(repr=False)


class TurnView(RuntimeModel):
    """Interactive current-turn data that must never be persisted or logged."""

    request_id: UUID
    context_id: UUID
    stage: TurnStage = "preparing"
    thinking_enabled: bool = False
    thinking: str = Field(default="", repr=False)
    thinking_truncated: bool = False
    memories: tuple[RecalledValue, ...] = Field(default=(), repr=False)
    memories_truncated: bool = False


@dataclass
class ViewEntry:
    """Private delivery authority accompanies the display without entering its serialization."""

    value: TurnView
    dependencies: tuple[tuple[UUID, int], ...] = ()
    certificate: ContextDependencies | None = None


class CurrentTurnView:
    """One event-loop-owned snapshot, with no worker, file, telemetry or database writes."""

    def __init__(self) -> None:
        self.entry: ViewEntry | None = None

    def begin(self, identity: UUID, context: UUID, *, thinking: bool) -> None:
        """Replace the previous ephemeral view once per observed user turn."""
        if self.entry is None or self.entry.value.request_id != identity:
            self.entry = ViewEntry(
                TurnView(request_id=identity, context_id=context, thinking_enabled=thinking)
            )

    def thinking_for(self, identity: UUID) -> bool:
        """Pin the setting captured at input observation, independent of later toggles."""
        return bool(
            self.entry
            and self.entry.value.request_id == identity
            and self.entry.value.thinking_enabled
        )

    def prepared(
        self,
        identity: UUID,
        rows: list[tuple[MemoryCategory, Memory]],
        dependencies: tuple[tuple[UUID, int], ...],
        certificate: ContextDependencies | None,
        *,
        historical: bool = False,
        conflicts: frozenset[tuple[UUID, int]] = frozenset(),
    ) -> None:
        """Expose only final selected values and retain their exact private certificate."""
        entry = self.entry
        if entry is None or entry.value.request_id != identity:
            return
        adapter: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
        values: list[RecalledValue] = []
        used = 0
        truncated = False
        for category, memory in rows:
            data = recall_value(memory, historical=historical and category != "profile")
            if category == "recall" and (memory.memory_id, memory.revision) in conflicts:
                data["status"] = "unresolved_conflict"
            value = RecalledValue(category=category, data=adapter.validate_python(data))
            size = len(value.model_dump_json().encode("utf-8"))
            if len(values) >= 100 or used + size > 65536:
                truncated = True
                continue
            values.append(value)
            used += size
        entry.dependencies = dependencies
        entry.certificate = certificate
        entry.value = entry.value.model_copy(
            update={
                "memories": tuple(values),
                "memories_truncated": truncated,
                "stage": "generating",
            }
        )

    def stage(self, identity: UUID, stage: TurnStage) -> None:
        """Ignore stale events from a superseded turn."""
        if (
            self.entry is not None
            and self.entry.value.request_id == identity
            and self.entry.value.stage != "invalidated"
        ):
            self.entry.value = self.entry.value.model_copy(update={"stage": stage})

    def append_thinking(self, identity: UUID, text: str) -> None:
        """Bound display bytes without modifying generation, speech or saved output."""
        entry = self.entry
        if (
            entry is None
            or entry.value.request_id != identity
            or not entry.value.thinking_enabled
            or entry.value.stage == "invalidated"
        ):
            return
        remaining = max(0, 32768 - len(entry.value.thinking.encode("utf-8")))
        data = text.encode("utf-8")
        kept = data[:remaining].decode("utf-8", errors="ignore")
        candidate = entry.value.model_copy(
            update={
                "thinking": entry.value.thinking + kept,
                "thinking_truncated": entry.value.thinking_truncated or len(data) > remaining,
                "stage": "thinking",
            }
        )
        # JSON escaping can exceed the raw UTF-8 allowance for control characters.
        if len(candidate.model_dump_json().encode("utf-8")) > 131008:
            low, high = 0, len(kept)
            while low < high:
                middle = (low + high + 1) // 2
                trial = candidate.model_copy(
                    update={"thinking": entry.value.thinking + kept[:middle]}
                )
                if len(trial.model_dump_json().encode("utf-8")) <= 131008:
                    low = middle
                else:
                    high = middle - 1
            candidate = candidate.model_copy(
                update={"thinking": entry.value.thinking + kept[:low], "thinking_truncated": True}
            )
        entry.value = candidate

    def invalidate(self, identity: UUID) -> None:
        """Revoke future inspection of content whose delivery authority changed."""
        if self.entry is not None and self.entry.value.request_id == identity:
            self.entry.value = self.entry.value.model_copy(
                update={"stage": "invalidated", "thinking": "", "memories": ()}
            )

    def clear(self) -> None:
        """Release the sole ephemeral view at runtime teardown."""
        self.entry = None
