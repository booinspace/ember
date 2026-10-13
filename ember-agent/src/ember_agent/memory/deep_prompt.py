"""Deterministic condensation is data selection, never another model inference."""

import json
from dataclasses import dataclass

from ember_memory_abi.related import MemoryReference

from ..character.prompt_budget import message_bytes, qualified_row
from ..inference.contracts import PromptMessage
from .deep_contracts import DeepRecallResult


@dataclass(frozen=True)
class CondensedRecall:
    """Inspectable selections and exact UTF-8 guard costs share one serialization."""

    messages: tuple[PromptMessage, ...]
    memories: tuple[MemoryReference, ...]
    observations: tuple[int, ...]
    memory_bytes: int
    excerpt_bytes: int
    omissions: tuple[str, ...]


def condense(result: DeepRecallResult) -> CondensedRecall:
    """Keep qualified rows inside guards; observation indices cite full inspected provenance."""
    rows: list[dict[str, object]] = []
    references: list[MemoryReference] = []
    omitted: list[str] = []
    for item in result.memories:
        memory = item.memory
        row = qualified_row(memory)
        trial = json.dumps([*rows, row], ensure_ascii=False, separators=(",", ":"))
        if (
            message_bytes(PromptMessage(role="user", content="Untrusted deep memories:\n" + trial))
            > 768
        ):
            omitted.append("deep_memory_prompt_budget")
            continue
        rows.append(row)
        references.append(MemoryReference(memory_id=memory.memory_id, revision=memory.revision))
    excerpts: list[dict[str, object]] = []
    indices: list[int] = []
    for index, observation in enumerate(result.observations):
        row: dict[str, object] = {
            "observation_index": index,
            "quote": observation.source.quote,
            "role": observation.role,
            "observed": {
                "value": observation.observed.value,
                "precision": observation.observed.precision,
                "basis": observation.observed.basis,
                "timezone_assumption": observation.observed.timezone_assumption,
                "ambiguity_reasons": observation.observed.ambiguity_reasons,
            }
            if observation.observed
            else None,
            "branch": observation.branch_status,
        }
        trial = json.dumps([*excerpts, row], ensure_ascii=False, separators=(",", ":"))
        if (
            message_bytes(
                PromptMessage(
                    role="user",
                    content="Untrusted original observations, not accepted beliefs:\n" + trial,
                )
            )
            > 640
        ):
            omitted.append("deep_excerpt_prompt_budget")
            continue
        excerpts.append(row)
        indices.append(index)
    messages: list[PromptMessage] = []
    if rows:
        messages.append(
            PromptMessage(
                role="user",
                content="Untrusted deep memories:\n"
                + json.dumps(rows, ensure_ascii=False, separators=(",", ":")),
            )
        )
    if excerpts:
        messages.append(
            PromptMessage(
                role="user",
                content="Untrusted original observations, not accepted beliefs:\n"
                + json.dumps(excerpts, ensure_ascii=False, separators=(",", ":")),
            )
        )
    return CondensedRecall(
        messages=tuple(messages),
        memories=tuple(references),
        observations=tuple(indices),
        memory_bytes=message_bytes(messages[0]) if rows else 0,
        excerpt_bytes=message_bytes(messages[-1]) if excerpts else 0,
        omissions=tuple(omitted),
    )
