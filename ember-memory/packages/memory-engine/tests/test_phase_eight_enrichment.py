"""Reviewed temporal-only revisions preserve identity and detect concurrent changes."""

from pathlib import Path
from uuid import uuid4

import pytest

from ember_memory_abi.errors import RevisionConflictError
from ember_memory_abi.evidence import Evidence, EvidenceAssertion, make_evidence_span
from ember_memory_abi.interfaces import RememberRequest, UpdateRequest
from ember_memory_abi.memory import EpisodePayload, MemoryContent, MemoryDraft
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.enrichment import TemporalEnrichmentService
from ember_memory_engine.processing_contracts import DateSupport

from .test_phase_seven import AUTHORITY, append_native


def test_reviewed_enrichment_restart_replay_and_preserved_identity(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        text = "I took a synthetic trip to Berlin last December."
        message = append_native(engine, character, text, observed="2026-07-15T12:00:00.000000Z")
        source = message.source.model_copy(update={"part_id": message.parts[0].part_id})
        draft = MemoryDraft(
            content=MemoryContent(
                kind="episode",
                payload=EpisodePayload(event="Synthetic trip to Berlin"),
                modality="FACTUAL_ASSERTION",
                confidence=0.95,
                importance=0.7,
                evidence=(
                    EvidenceAssertion(
                        evidence=Evidence(
                            evidence_id=uuid4(),
                            source=source,
                            span=make_evidence_span(text, 0, len(text)),
                        ),
                        relation="supports",
                        modality="FACTUAL_ASSERTION",
                        authority="confirmed_user",
                        verdict="validated",
                    ),
                ),
            ),
            temporal=TemporalBounds(observed_at=message.created_time.value),
        )
        store = engine.character(character, authority=AUTHORITY)
        memory = store.remember(RememberRequest(idempotency_key="event", draft=draft)).affected[0]
        before = store.get(memory.memory_id)
        service = TemporalEnrichmentService(engine, character, authority=AUTHORITY)
        review = service.propose(
            memory.memory_id,
            source,
            DateSupport(
                expression="last December",
                quote=text,
                status="completed",
            ),
        )
        assert service.propose(memory.memory_id, source, review.proposal.support) == review
        assert service.apply(review) is None
        proposal_id = review.proposal.proposal_id
    with CanonicalEngine(root) as engine:
        service = TemporalEnrichmentService(engine, character, authority=AUTHORITY)
        approved = service.get(proposal_id).model_copy(update={"decision": "approve"})
        receipt = service.apply(approved)
        assert receipt is not None and receipt.outcome == "applied"
        assert service.apply(approved).outcome == "already_applied"
        assert service.get(proposal_id).decision == "approve"
        after = service.store.get(memory.memory_id)
        assert after.revision == 2 and after.memory_id == before.memory_id
        assert after.created_at == before.created_at
        assert after.content.payload == before.content.payload
        assert after.content.confidence == before.content.confidence
        assert after.origin == before.origin
        assert after.content.provenance == before.content.provenance
        assert after.temporal.valid_from == "2025-12-01T00:00:00.000000Z"
        next_review = service.propose(memory.memory_id, source, approved.proposal.support)
        service.store.update(
            UpdateRequest(
                idempotency_key="concurrent",
                memory_id=memory.memory_id,
                expected_revision=2,
                draft=MemoryDraft(content=after.content, temporal=TemporalBounds()),
                reason="Synthetic concurrent revision",
            )
        )
        with pytest.raises(RevisionConflictError):
            service.apply(next_review.model_copy(update={"decision": "approve"}))
        assert service.get(next_review.proposal.proposal_id).decision == "pending"
