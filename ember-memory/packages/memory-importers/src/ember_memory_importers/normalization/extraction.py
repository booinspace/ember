"""Narrow deterministic explicit-fact proposals; no model calls or automatic activation."""

import re
from uuid import uuid4

from ember_memory_abi.archive import Message
from ember_memory_abi.evidence import Evidence, EvidenceAssertion, SourceRef, make_evidence_span
from ember_memory_abi.memory import EntityRef, FactPayload, MemoryContent, MemoryDraft, StringValue
from ember_memory_abi.temporal import TemporalBounds

_PATTERN = re.compile(
    r"My primary GPU is ((?:NVIDIA|AMD|Intel|RTX|GTX|RX|Arc|SYNTHETIC_)"
    r"[A-Za-z0-9_ ()/+-]{0,100})\.?",
    re.IGNORECASE,
)


def explicit_primary_gpu(text: str, subject: EntityRef) -> FactPayload | None:
    """Recognize one whole explicit assertion; reject intent, quotations and speculative prose."""
    match = _PATTERN.fullmatch(text)
    if match is None:
        return None
    return FactPayload(
        subject=subject,
        predicate="hardware.primary_gpu",
        definition_version="0.1.0",
        value=StringValue(value=match.group(1)),
    )


def propose_message(message: Message, subject: EntityRef) -> tuple[MemoryDraft, ...]:
    """Propose available explicit user assertions; the caller still confirms them."""
    if message.actor.role != "user":
        return ()
    result: list[MemoryDraft] = []
    for part in message.parts:
        if part.status != "available" or part.text is None:
            continue
        payload = explicit_primary_gpu(part.text, subject)
        if payload is None:
            continue
        reference = SourceRef.model_validate(
            {**message.source.model_dump(), "part_id": part.part_id}
        )
        evidence = Evidence(
            evidence_id=uuid4(),
            source=reference,
            span=make_evidence_span(part.text, 0, len(part.text)),
        )
        result.append(
            MemoryDraft(
                content=MemoryContent(
                    kind="fact",
                    payload=payload,
                    modality="FACTUAL_ASSERTION",
                    confidence=0.5,
                    importance=0.7,
                    evidence=(
                        EvidenceAssertion(
                            evidence=evidence,
                            relation="supports",
                            modality="FACTUAL_ASSERTION",
                            authority="source_user_claim",
                            verdict="requires_confirmation",
                        ),
                    ),
                ),
                temporal=TemporalBounds(
                    observed_at=message.created_time.value, basis="source_observation_not_validity"
                ),
            )
        )
    return tuple(result)
