"""Deterministic condensation is data selection, never another model inference."""

import json
from dataclasses import dataclass
from uuid import UUID

from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.related import MemoryReference

from ..character.prompt_budget import message_bytes, qualified_row
from ..inference.contracts import PromptMessage
from .deep_contracts import DeepRecallResult


@dataclass(frozen=True)
class CondensedRecall:
    """Inspectable selections and exact UTF-8 guard costs share one serialization."""

    messages: tuple[PromptMessage, ...]
    memories: tuple[MemoryReference, ...]
    memory_sources: tuple[SourceRef, ...]
    observations: tuple[int, ...]
    archive_sources: tuple[SourceRef, ...]
    excerpt_sources: tuple[SourceRef, ...]
    memory_bytes: int
    excerpt_bytes: int
    omissions: tuple[str, ...]


def condense(
    result: DeepRecallResult, *, excluded: frozenset[tuple[UUID, int]] = frozenset()
) -> CondensedRecall:
    """Keep qualified rows inside guards; observation indices cite full inspected provenance."""
    rows: list[dict[str, object]] = []
    references: list[MemoryReference] = []
    omitted: list[str] = []
    for item in result.memories:
        memory = item.memory
        if (memory.memory_id, memory.revision) in excluded:
            continue
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
    certified_sources = {
        ref.identity_key()
        for ref in (
            (*result.dependencies.archive_sources, *result.dependencies.excerpt_sources)
            if result.dependencies
            else ()
        )
    }
    for index, observation in enumerate(result.observations):
        if (
            result.dependencies is not None
            and observation.source.evidence.source.identity_key() not in certified_sources
        ):
            omitted.append("deep_observation_dependency_missing")
            continue
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
    selected_sources = {
        result.observations[index].source.evidence.source.identity_key() for index in indices
    }
    selected_memories = {(ref.memory_id, ref.revision) for ref in references}
    return CondensedRecall(
        messages=tuple(messages),
        memories=tuple(references),
        memory_sources=tuple(
            {
                assertion.evidence.source.identity_key(): assertion.evidence.source
                for item in result.memories
                if (item.memory.memory_id, item.memory.revision) in selected_memories
                for assertion in item.memory.content.evidence
            }.values()
        ),
        observations=tuple(indices),
        archive_sources=tuple(
            ref
            for ref in (result.dependencies.archive_sources if result.dependencies else ())
            if ref.identity_key() in selected_sources
        ),
        excerpt_sources=tuple(
            ref
            for ref in (result.dependencies.excerpt_sources if result.dependencies else ())
            if ref.identity_key() in selected_sources
        ),
        memory_bytes=message_bytes(messages[0]) if rows else 0,
        excerpt_bytes=message_bytes(messages[-1]) if excerpts else 0,
        omissions=tuple(omitted),
    )
