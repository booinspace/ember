"""Explicitly reviewed temporal revisions target characters, never shared seeded libraries."""

import sqlite3
from typing import Literal
from uuid import UUID, uuid4

from pydantic import JsonValue

from ember_memory_abi.archive import Document, Message
from ember_memory_abi.common import (
    CanonicalId,
    ContractModel,
    Revision,
    Sha256,
    canonical_object_sha256,
)
from ember_memory_abi.errors import AccessDeniedError
from ember_memory_abi.evidence import Evidence, EvidenceAssertion, SourceRef, make_evidence_span
from ember_memory_abi.interfaces import MutationReceipt, UpdateRequest
from ember_memory_abi.memory import MemoryDraft
from ember_memory_abi.temporal import TemporalBounds

from . import mutations
from .archive import source_text
from .database import load_model, model_bytes, payload_hash, row_text
from .engine import CanonicalEngine
from .event_dates import resolve_date, supported_position
from .models import MutationAuthority
from .processing_contracts import DateSupport, ProcessingError


class TemporalProposal(ContractModel):
    """Immutable temporal-only proposal with exact support and an expected character revision."""

    version: Literal["ember.temporal-review@0.1.0"] = "ember.temporal-review@0.1.0"
    proposal_id: CanonicalId
    character_id: CanonicalId
    memory_id: CanonicalId
    expected_revision: Revision
    original_content_sha256: Sha256
    source: SourceRef
    support: DateSupport
    timezone: str
    temporal: TemporalBounds
    draft: MemoryDraft


class TemporalReview(ContractModel):
    """Only the explicit decision is mutable; original proposal bytes remain hash verified."""

    proposal: TemporalProposal
    decision: Literal["pending", "approve", "reject"] = "pending"


class TemporalEnrichmentService:
    """Reuse canonical evidence and update receipts; no rescan, automatic approval or reseeding."""

    def __init__(
        self, engine: CanonicalEngine, character: CanonicalId, *, authority: MutationAuthority
    ) -> None:
        self.engine = engine
        self.store = engine.character(character, authority=authority)
        self.character = character

    def propose(
        self,
        memory_id: CanonicalId,
        source: SourceRef,
        support: DateSupport,
        *,
        timezone: str = "Europe/London",
    ) -> TemporalReview:
        """Persist a resumable exact-date proposal using the original source clock."""
        memory = self.store.get(memory_id)
        if memory.lifecycle != "active":
            raise ProcessingError("enrichment_requires_active_memory")
        if source not in tuple(a.evidence.source for a in memory.content.evidence):
            raise ProcessingError("enrichment_requires_existing_evidence_source")
        # This read reuses current evidence authority; source inspection never gains a grant.
        self.store.get_sources(memory_id, revision=memory.revision)
        record = self.engine.resolve(source)
        if not isinstance(record, (Message, Document)):
            raise ProcessingError("enrichment_source_clock_unavailable")
        if memory.content.kind == "episode" and support.status != "completed":
            raise ProcessingError("unsupported_completed_event")
        original = source_text(record, source)
        start = supported_position(original, support.quote, support.quote_start)
        temporal = resolve_date(support, record.created_time.value, timezone)
        if temporal.valid_from is None:
            raise ProcessingError("enrichment_date_unresolved")
        seed: dict[str, JsonValue] = {
            "character": str(self.character),
            "memory": str(memory_id),
            "revision": memory.revision,
            "source": source.model_dump(mode="json"),
            "support": support.model_dump(mode="json"),
            "timezone": timezone,
        }
        digest = canonical_object_sha256(seed)
        identity = UUID(bytes=bytes.fromhex(digest)[:16], version=4)
        evidence_identity = UUID(bytes=bytes.fromhex(digest)[16:], version=4)
        assertion = EvidenceAssertion(
            evidence=Evidence(
                evidence_id=evidence_identity,
                source=source,
                span=make_evidence_span(original, start, start + len(support.quote)),
            ),
            relation="supports",
            modality=memory.content.modality,
            authority="confirmed_user",
            verdict="exact_date_passage",
        )
        self.engine.validate(assertion.evidence)
        draft = MemoryDraft(
            content=memory.content.model_copy(
                update={
                    "evidence": (*memory.content.evidence, assertion),
                }
            ),
            temporal=temporal.model_copy(
                update={
                    "last_confirmed_at": memory.temporal.last_confirmed_at,
                }
            ),
        )
        proposal = TemporalProposal(
            proposal_id=identity,
            character_id=self.character,
            memory_id=memory_id,
            expected_revision=memory.revision,
            original_content_sha256=canonical_object_sha256(memory.content),
            source=source,
            support=support,
            timezone=timezone,
            temporal=temporal,
            draft=draft,
        )

        def write(connection: sqlite3.Connection) -> TemporalProposal:
            previous = connection.execute(
                "SELECT * FROM temporal_enrichments WHERE proposal_id=?", (str(identity),)
            ).fetchone()
            if previous is not None:
                return load_model(previous, TemporalProposal)
            payload = model_bytes(proposal)
            connection.execute(
                "INSERT INTO temporal_enrichments VALUES(?,?,?,?,?)",
                (str(identity), str(memory_id), memory.revision, payload, payload_hash(payload)),
            )
            return proposal

        return TemporalReview(proposal=self.store.database.write(write))

    def get(self, proposal_id: CanonicalId) -> TemporalReview:
        """Reload a persisted proposal after restart without repeating model interpretation."""

        def read(connection: sqlite3.Connection) -> TemporalProposal:
            row = connection.execute(
                "SELECT * FROM temporal_enrichments WHERE proposal_id=?", (str(proposal_id),)
            ).fetchone()
            if row is None:
                raise ProcessingError("enrichment_proposal_missing")
            return load_model(row, TemporalProposal)

        proposal = self.store.database.read(read)
        decision = self.store.database.read(
            lambda connection: connection.execute(
                "SELECT decision FROM temporal_enrichment_reviews WHERE proposal_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (str(proposal_id),),
            ).fetchone()
        )
        return TemporalReview.model_validate(
            {
                "proposal": proposal,
                "decision": row_text(decision, "decision") if decision else "pending",
            }
        )

    def apply(self, review: TemporalReview) -> MutationReceipt | None:
        """Apply only an approved exact proposal using existing CAS and idempotency machinery."""
        stored = self.get(review.proposal.proposal_id).proposal
        if stored != review.proposal or stored.character_id != self.character:
            raise ProcessingError("enrichment_proposal_changed")
        if review.decision == "pending":
            return None
        if self.store.state.authority.mode != "confirmed_user":
            raise AccessDeniedError()

        def decide(connection: sqlite3.Connection, receipt: MutationReceipt | None) -> None:
            previous = connection.execute(
                "SELECT decision,operation_id FROM temporal_enrichment_reviews "
                "WHERE proposal_id=? ORDER BY rowid DESC LIMIT 1",
                (str(stored.proposal_id),),
            ).fetchone()
            operation = str(receipt.operation_id) if receipt else None
            if (
                previous
                and previous["decision"] == review.decision
                and previous["operation_id"] == operation
            ):
                return
            stamp, _ = self.store.database.recorded_stamp(connection)
            connection.execute(
                "INSERT INTO temporal_enrichment_reviews VALUES(?,?,?,?,?)",
                (str(uuid4()), str(stored.proposal_id), review.decision, operation, stamp),
            )

        if review.decision == "reject":

            def reject(connection: sqlite3.Connection) -> None:
                with self.store.database.transaction(connection):
                    decide(connection, None)

            self.store.database.write(reject)
            return None
        request = UpdateRequest(
            idempotency_key="temporal-enrichment:" + str(stored.proposal_id),
            memory_id=stored.memory_id,
            expected_revision=stored.expected_revision,
            draft=stored.draft,
            reason="Explicitly approved source-supported temporal enrichment",
        )
        return self.store.database.write(
            lambda connection: mutations.mutate(
                self.store.state,
                connection,
                "update",
                request,
                on_commit=decide,
            )
        )
