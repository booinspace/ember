"""Follow one delivered turn's supporting span without archive search or synthesis."""

from dataclasses import dataclass

from ember_memory_abi.evidence import Evidence
from ember_memory_abi.memory import Memory
from ember_memory_abi.related import (
    AsyncRelatedLookupClient,
    ContextDependencies,
    MemoryReference,
    RelatedRequest,
)
from ember_memory_abi.temporal import TemporalQuery


@dataclass(frozen=True)
class SourceAnchor:
    memory: MemoryReference
    evidence: Evidence


def supporting_anchor(memory: Memory) -> SourceAnchor | None:
    """Select one bounded supporting span; never quote a contradiction as proof."""
    for assertion in memory.content.evidence:
        evidence = assertion.evidence
        if (
            assertion.relation == "supports"
            and evidence.span.end_byte - evidence.span.start_byte <= 2000
        ):
            return SourceAnchor(
                MemoryReference(memory_id=memory.memory_id, revision=memory.revision), evidence
            )
    return None


async def verified_source(
    client: AsyncRelatedLookupClient, anchor: SourceAnchor, parent: ContextDependencies
) -> tuple[str, ContextDependencies] | None:
    """Resolve the exact span under the prior selector and a fresh delivery certificate."""
    if anchor.memory not in parent.memories:
        return None
    temporal = next(
        (group.temporal for group in parent.selections if anchor.memory in group.memories),
        TemporalQuery(),
    )
    result = await client.lookup_related(
        RelatedRequest(
            intent="expand",
            anchors=(anchor.memory,),
            excerpts=(anchor.evidence,),
            temporal=temporal,
            include_relationships=False,
            recall_only=True,
            limit=2,
        )
    )
    proof = result.dependencies
    ref = anchor.evidence.source
    if (
        proof is None
        or proof.policy_epoch != parent.policy_epoch
        or anchor.memory not in proof.memories
        or ref not in proof.excerpt_sources
    ):
        return None
    boundaries = [value for value in (parent.valid_until, proof.valid_until) if value is not None]
    proof = proof.model_copy(
        update={
            "selection_epoch": parent.selection_epoch,
            "current_conflicts": parent.current_conflicts,
            "valid_until": min(boundaries) if boundaries else None,
        }
    )
    for observation in result.excerpts:
        source = observation.source
        if (
            source.evidence == anchor.evidence
            and source.availability == "available"
            and source.quote
        ):
            observed = observation.observed.value if observation.observed else None
            date = f", source timestamp {observed}" if observed else ""
            return (
                "I recalled one memory for that reply. Its supporting source is "
                f"{ref.record_kind} {ref.record_id}, revision {ref.record_revision}{date}:"
                f"\n\n{source.quote}",
                proof,
            )
    return None
