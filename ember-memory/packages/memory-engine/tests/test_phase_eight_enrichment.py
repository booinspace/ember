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
from ember_memory_engine.bundles import BundleService, restore_bundles
from ember_memory_engine.enrichment import TemporalBatchReview, TemporalEnrichmentService
from ember_memory_engine.processing_contracts import DateSupport

from .test_phase_five_search import SyntheticEncoder
from .test_phase_seven import AUTHORITY, append_native


def test_reviewed_enrichment_restart_replay_and_preserved_identity(tmp_path: Path) -> None:
    root = tmp_path / "data"
    encoder = SyntheticEncoder()
    with CanonicalEngine(root, encoder=encoder) as engine:
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
        engine.rebuild_index(character, encoder)
        encoder.calls = 0
    with CanonicalEngine(root, encoder=encoder) as engine:
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
        engine.update_index(character, encoder)
        assert encoder.calls == 0  # Only date/proof metadata changed; text vectors are reusable.
        pinned = service.propose(
            memory.memory_id, source, approved.proposal.support, expected_revision=1
        )
        assert pinned.proposal == approved.proposal
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

        outcomes = service.apply_batch(
            TemporalBatchReview(
                reviews=(
                    next_review.model_copy(update={"decision": "approve"}),
                    approved,
                )
            )
        )
        assert [item.outcome for item in outcomes] == ["failed", "already_applied"]
        assert outcomes[0].error_code == "revision_conflict"
        assert service.store.get(memory.memory_id).revision == 3

    bundle = tmp_path / "selected"
    BundleService(root).export_character(
        character, bundle, mode="self_contained", memory_ids=(memory.memory_id,)
    )
    restored = tmp_path / "restored"
    restore_bundles((bundle,), restored)
    with CanonicalEngine(restored) as engine:
        service = TemporalEnrichmentService(engine, character, authority=AUTHORITY)
        restored_review = service.get(proposal_id)
        assert restored_review == approved
        assert service.apply(restored_review).outcome == "already_applied"
        assert service.store.get(memory.memory_id).revision == 3


def test_enrichment_preserves_library_approvals_other_seeds_and_legacy_jobs(tmp_path: Path) -> None:
    from ember_memory_abi.archive import (
        ContentPart,
        Document,
        ImportCursor,
        NormalizedArchiveBatch,
        ProcessingSpecRef,
    )
    from ember_memory_abi.evidence import SourceRef, source_text_sha256
    from ember_memory_abi.memory import CandidateMemory, Entity, EntityRef
    from ember_memory_abi.temporal import SourceTimestamp
    from ember_memory_engine.library import (
        ApprovalRequest,
        KnowledgeSelection,
        LibraryService,
        SeedRequest,
    )
    from ember_memory_engine.processing import ProcessingService
    from ember_memory_engine.processing_contracts import ExtractionSpec, JobRequest

    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("synthetic-enrichment-isolation").store_id
        service = LibraryService(engine, authority=AUTHORITY)
        subject = EntityRef(store_id=library, entity_id=uuid4())
        service.put_entity(library, Entity(reference=subject, revision=1, name="Synthetic user"))
        quote = "I visited Berlin last December."
        source = SourceRef(
            source_store_id=library,
            record_kind="document",
            record_id=uuid4(),
            record_revision=1,
            part_id=uuid4(),
        )
        observed = "2026-07-15T12:00:00.000000Z"
        document = Document(
            source=source.model_copy(update={"part_id": None}),
            recorded_at="2026-10-01T00:00:00.000000Z",
            created_time=SourceTimestamp(value=observed, precision="microsecond"),
            parts=(
                ContentPart(
                    part_id=source.part_id,
                    kind="text",
                    status="available",
                    text=quote,
                    content_sha256=source_text_sha256(quote),
                ),
            ),
        )
        writer = engine.archive(library)
        writer.write_batch(
            NormalizedArchiveBatch(
                source_store_id=library,
                source_namespace=writer.namespace,
                idempotency_key="source",
                processing_spec=ProcessingSpecRef(
                    name="ember.synthetic-enrichment",
                    version="0.1.0",
                    specification_sha256="a" * 64,
                ),
                records=(document,),
                resume_cursor=ImportCursor(
                    adapter="ember.synthetic-enrichment",
                    adapter_version="0.1.0",
                    logical_inventory_sha256="b" * 64,
                    component_sha256="c" * 64,
                    next_record_index=1,
                ),
            )
        )
        draft = MemoryDraft(
            content=MemoryContent(
                kind="episode",
                payload=EpisodePayload(event="Synthetic Berlin visit", participants=(subject,)),
                modality="FACTUAL_ASSERTION",
                confidence=0.95,
                importance=0.7,
                evidence=(
                    EvidenceAssertion(
                        evidence=Evidence(
                            evidence_id=uuid4(),
                            source=source,
                            span=make_evidence_span(quote, 0, len(quote)),
                        ),
                        relation="supports",
                        modality="FACTUAL_ASSERTION",
                        authority="confirmed_user",
                        verdict="validated",
                    ),
                ),
            ),
            temporal=TemporalBounds(observed_at=observed),
        )
        candidate = CandidateMemory(
            candidate_id=uuid4(),
            owner_store_id=library,
            revision=1,
            draft=draft,
            recorded_at="2026-10-01T00:00:00.000000Z",
        )
        approved = service.approve(
            library, ApprovalRequest(idempotency_key="approve", candidate=candidate)
        )
        selection = KnowledgeSelection(knowledge_id=approved.knowledge_id, revision=1)
        first, second = engine.create_character().store_id, engine.create_character().store_id
        seed = SeedRequest(idempotency_key="seed", library_id=library, selections=(selection,))
        first_id = service.seed(first, seed).affected[0].memory_id
        second_id = service.seed(second, seed).affected[0].memory_id
        before = engine.character(first).get(first_id)
        other = engine.character(second).get(second_id)
        processing = ProcessingService(engine, library)
        old_job = processing.create(
            JobRequest(
                owner_store_id=library,
                idempotency_key="legacy-job",
                source_kind="all",
                specification=ExtractionSpec(model="synthetic", model_digest="a" * 64),
            )
        )
        # A newly approved library revision has no authority to alter existing seeds.
        library_revision = candidate.model_copy(
            update={
                "revision": 2,
                "draft": draft.model_copy(
                    update={
                        "temporal": TemporalBounds(
                            valid_from="2025-12-01T00:00:00.000000Z",
                            valid_until="2026-01-01T00:00:00.000000Z",
                            observed_at=observed,
                            precision="month",
                        ),
                    }
                ),
            }
        )
        service.approve(
            library, ApprovalRequest(idempotency_key="approve-next", candidate=library_revision)
        )
        assert engine.character(first).get(first_id) == before
        assert engine.character(second).get(second_id) == other

        enrichment = TemporalEnrichmentService(engine, first, authority=AUTHORITY)
        review = enrichment.propose(
            first_id,
            source,
            DateSupport(expression="last December", quote=quote, status="completed"),
        )
        enrichment.apply(review.model_copy(update={"decision": "approve"}))
        after = engine.character(first).get(first_id)
        assert after.memory_id == before.memory_id and after.revision == 2
        assert (
            after.origin == before.origin and after.content.provenance == before.content.provenance
        )
        assert after.temporal.valid_from == "2025-12-01T00:00:00.000000Z"
        assert service.knowledge(library, selection) == approved
        assert engine.character(second).get(second_id) == other
        assert processing.status(old_job.definition.job_id) == old_job
        assert service.seed(first, seed).outcome == "already_applied"
        assert engine.character(first).get(first_id) == after
