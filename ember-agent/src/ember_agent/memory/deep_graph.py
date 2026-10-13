"""Deterministic bounded evidence connections; topical similarity never joins incidents."""

import re
from uuid import UUID, uuid5

from ember_memory_abi.interfaces import MemoryResult
from ember_memory_abi.memory import EntityValue, EpisodePayload, FactPayload, Memory
from ember_memory_abi.related import MemoryReference, MemoryRelationship, ResolvedEntity

from .deep_contracts import RecallAssociation, RecallCluster

STOPWORDS = frozenset(
    "i me my you we our remember memories recall deeply about when last year what "
    "happened thing things working was were the a an of on in and with that this "
    "from to do does did have had can could please it something".split()
)


def terms(text: str) -> set[str]:
    """Limit alias/topic probes to bounded non-generic original-query terms."""
    return {
        word
        for word in re.findall(r"[\w]+", text.casefold().replace("_", " "))
        if len(word) > 2 and word not in STOPWORDS and not word.isdecimal()
    }


def reference(memory: Memory) -> MemoryReference:
    """Exact revision; redirect traversal must still pass engine eligibility."""
    return MemoryReference(memory_id=memory.memory_id, revision=memory.revision)


def relevant(
    memory: Memory, original_terms: set[str], aliases: tuple[ResolvedEntity, ...] = ()
) -> bool:
    """Literal topics or authorized non-generic aliases connect to the original query."""
    payload = memory.content.payload
    if not original_terms or terms(payload.model_dump_json()) & original_terms:
        return True
    matched = {(item.reference.store_id, item.reference.entity_id) for item in aliases}
    if isinstance(payload, FactPayload) and isinstance(payload.value, EntityValue):
        value = payload.value.value
        return (value.store_id, value.entity_id) in matched
    if isinstance(payload, EpisodePayload):
        return any(
            (value.store_id, value.entity_id) in matched
            for value in (*payload.participants, *((payload.place,) if payload.place else ()))
        )
    return False


def overlap(left: Memory, right: Memory) -> bool:
    """Overlapping exact spans in the same source revision provide a strong connection."""
    return any(
        a.evidence.source.identity_key() == b.evidence.source.identity_key()
        and a.evidence.span.source_content_sha256 == b.evidence.span.source_content_sha256
        and max(a.evidence.span.start_codepoint, b.evidence.span.start_codepoint)
        < min(a.evidence.span.end_codepoint, b.evidence.span.end_codepoint)
        for a in left.content.evidence
        for b in right.content.evidence
    )


def connections(
    results: tuple[MemoryResult, ...],
    edges: tuple[MemoryRelationship, ...],
    request_id: UUID,
) -> tuple[tuple[RecallAssociation, ...], tuple[RecallCluster, ...]]:
    """Join only verified transitions or shared exact evidence; labels remain separate."""
    memories = {item.memory.memory_id: item.memory for item in results}
    associations: dict[tuple[UUID, UUID], RecallAssociation] = {}
    parents = {identity: identity for identity in memories}

    def root(identity: UUID) -> UUID:
        while parents[identity] != identity:
            identity = parents[identity]
        return identity

    def link(left: UUID, right: UUID, explicit: bool) -> None:
        if left == right or left not in memories or right not in memories:
            return
        key = (left, right) if str(left) < str(right) else (right, left)
        associations[key] = RecallAssociation(
            anchor=reference(memories[left]),
            target=reference(memories[right]),
            reason="explicit_transition" if explicit else "overlapping_evidence",
            strength="explicit" if explicit else "source_supported",
        )
        parents[root(right)] = root(left)

    for edge in edges:
        if edge.target is None:
            continue
        if edge.kind in {"supersedes", "redirects"}:
            link(edge.source.memory_id, edge.target.memory_id, True)
        elif edge.kind == "conflicts":
            left, right = edge.source.memory_id, edge.target.memory_id
            if left == right or left not in memories or right not in memories:
                continue
            key = (left, right) if str(left) < str(right) else (right, left)
            associations[key] = RecallAssociation(
                anchor=reference(memories[left]),
                target=reference(memories[right]),
                reason="conflicting_claims",
                strength="explicit",
            )
    selected = tuple(memories.values())
    for index, left in enumerate(selected):
        for right in selected[index + 1 :]:
            if overlap(left, right):
                key = tuple(sorted((left.memory_id, right.memory_id), key=str))
                if key not in associations:
                    link(left.memory_id, right.memory_id, False)
    groups: dict[UUID, list[MemoryReference]] = {}
    for identity, memory in memories.items():
        groups.setdefault(root(identity), []).append(reference(memory))
    clusters = tuple(
        RecallCluster(
            cluster_id=uuid5(request_id, "|".join(sorted(str(m.memory_id) for m in members))),
            members=tuple(members),
            strength=(
                "independent"
                if len(members) == 1
                else "explicit"
                if any(
                    a.strength == "explicit" and a.anchor in members and a.target in members
                    for a in associations.values()
                )
                else "source_supported"
            ),
        )
        for members in groups.values()
    )
    return tuple(associations.values())[:256], clusters
