"""Explicit operator review; immutable candidate payloads and original quotes cannot be edited."""

import sqlite3
from typing import Literal
from uuid import UUID, uuid4

from ember_memory_abi.common import CanonicalId, canonical_object_sha256, timestamp_from_datetime
from ember_memory_abi.interfaces import RememberRequest
from ember_memory_abi.memory import CandidateMemory
from ember_memory_abi.predicates import (
    initial_predicates,
    phase_seven_predicates,
    validate_memory_write,
)

from .activation import evaluate
from .activation_spec import POLICY
from .database import load_model, model_bytes, payload_hash, row_text
from .library import ApprovalRequest, LibraryService
from .models import MutationAuthority
from .processing import ProcessingService
from .processing_contracts import (
    PolicyDecision,
    ProcessingError,
    ReviewEntry,
    ReviewFile,
    ReviewReceipt,
)
from .source_access import source_keys


class ReviewService:
    """Trusted human review bound to one source owner; no extraction output invokes this."""

    def __init__(
        self, processing: ProcessingService, *, authority: MutationAuthority | None = None
    ) -> None:
        self.processing = processing
        self.engine = processing.engine
        self.database = processing.database
        self.authority = authority or MutationAuthority()

    def candidate(self, candidate_id: CanonicalId, revision: int) -> CandidateMemory:
        """Return a hash-validated proposed revision without granting activation."""

        def read(connection: sqlite3.Connection) -> CandidateMemory:
            row = connection.execute(
                "SELECT * FROM candidates WHERE candidate_id=? AND revision=?",
                (str(candidate_id), revision),
            ).fetchone()
            if row is None:
                raise ProcessingError("candidate_not_found")
            candidate = load_model(row, CandidateMemory)
            if candidate.owner_store_id != self.processing.owner:
                raise ProcessingError("candidate_owner_mismatch")
            return candidate

        return self.database.read(read)

    def decision(self, candidate: CandidateMemory) -> PolicyDecision:
        """Recheck known conflicts/rejections for review; old verdicts remain immutable."""

        def read(connection: sqlite3.Connection) -> PolicyDecision:
            row = connection.execute(
                "SELECT * FROM activation_decisions WHERE candidate_id=? AND "
                "revision=? ORDER BY rowid DESC LIMIT 1",
                (str(candidate.candidate_id), candidate.revision),
            ).fetchone()
            if row is None:
                raise ProcessingError("candidate_policy_unavailable")
            decision = load_model(row, PolicyDecision)
            association = connection.execute(
                "SELECT * FROM processing_candidates WHERE candidate_id=? AND revision=?",
                (str(candidate.candidate_id), candidate.revision),
            ).fetchone()
            if association is None:
                raise ProcessingError("candidate_job_unavailable")
            conflict = (
                association["conflict_key"] is not None
                and connection.execute(
                    "SELECT 1 FROM processing_candidates WHERE conflict_key=? AND "
                    "value_key<>? LIMIT 1",
                    (association["conflict_key"], association["value_key"]),
                ).fetchone()
                is not None
            )
            rejected = (
                connection.execute(
                    "SELECT 1 FROM review_decisions WHERE fingerprint=? AND "
                    "decision='reject' LIMIT 1",
                    (row_text(association, "fingerprint"),),
                ).fetchone()
                is not None
            )
            inputs = decision.inputs.model_copy(
                update={"conflict": conflict, "rejected_previously": rejected}
            )
            return evaluate(inputs, evaluated_at=timestamp_from_datetime(self.engine.clock.now()))

        return self.database.read(read)

    def reevaluate(self, job_id: CanonicalId) -> int:
        """Append changed policy decisions in bounded pages; accepted beliefs never change.

        Repeated evaluation with identical inputs/policy is a no-op. Original
        extraction provenance and every earlier verdict remain available.
        """
        self.processing.definition(job_id)
        appended = 0
        offset = 0
        while True:
            rows = self.database.read(
                lambda connection, offset=offset: connection.execute(
                    "SELECT candidate_id,revision FROM processing_candidates WHERE job_id=? "
                    "ORDER BY ordinal,candidate_id LIMIT 128 OFFSET ?",
                    (str(job_id), offset),
                ).fetchall()
            )
            if not rows:
                return appended
            for row in rows:
                candidate = self.candidate(UUID(row_text(row, "candidate_id")), row["revision"])
                decision = self.decision(candidate)

                def write(
                    connection: sqlite3.Connection,
                    candidate: CandidateMemory = candidate,
                    decision: PolicyDecision = decision,
                ) -> bool:
                    with self.database.transaction(connection):
                        previous = connection.execute(
                            "SELECT * FROM activation_decisions WHERE candidate_id=? AND "
                            "revision=? ORDER BY rowid DESC LIMIT 1",
                            (str(candidate.candidate_id), candidate.revision),
                        ).fetchone()
                        if previous is None:
                            raise ProcessingError("candidate_policy_unavailable")
                        old = load_model(previous, PolicyDecision)
                        if (
                            old.inputs == decision.inputs
                            and old.verdict.policy == decision.verdict.policy
                            and old.verdict.verdict == decision.verdict.verdict
                            and old.verdict.reasons == decision.verdict.reasons
                        ):
                            return False
                        policy_payload = model_bytes(POLICY)
                        connection.execute(
                            "INSERT OR IGNORE INTO processing_specs VALUES(?,?,?)",
                            (
                                POLICY.reference().specification_sha256,
                                policy_payload,
                                payload_hash(policy_payload),
                            ),
                        )
                        payload = model_bytes(decision)
                        connection.execute(
                            "INSERT INTO activation_decisions VALUES(?,?,?,?,?)",
                            (
                                str(uuid4()),
                                str(candidate.candidate_id),
                                candidate.revision,
                                payload,
                                payload_hash(payload),
                            ),
                        )
                        return True

                appended += int(self.database.write(write))
            offset += len(rows)

    def _available(self, candidate: CandidateMemory) -> None:
        for assertion in candidate.draft.content.evidence:
            keys = source_keys(assertion.evidence.source)
            suppressed = self.database.read(
                lambda connection, keys=keys: (
                    connection.execute(
                        "SELECT 1 FROM suppression_targets WHERE target_kind='source' AND "
                        "target_key IN (?,?)",
                        keys,
                    ).fetchone()
                    is not None
                )
            )
            if suppressed:
                raise ProcessingError("source_suppressed")

    def _last_decision(self, candidate: CandidateMemory) -> Literal["pending", "approve", "reject"]:
        row = self.database.read(
            lambda connection: connection.execute(
                "SELECT decision FROM review_decisions WHERE candidate_id=? AND revision=? "
                "ORDER BY rowid DESC LIMIT 1",
                (str(candidate.candidate_id), candidate.revision),
            ).fetchone()
        )
        if row is None:
            return "pending"
        decision = row_text(row, "decision")
        if decision not in {"approve", "reject"}:
            raise ProcessingError("review_decision_invalid")
        return "approve" if decision == "approve" else "reject"

    def export(
        self,
        job_id: CanonicalId,
        *,
        offset: int = 0,
        limit: int = 1000,
        include_decisions: bool = False,
    ) -> ReviewFile:
        """Export exact evidence; optionally restore recorded operator decisions for a UI."""
        self.processing.definition(job_id)
        if offset < 0 or not 1 <= limit <= 1000:
            raise ProcessingError("review_page_invalid")
        rows = self.database.read(
            lambda connection: connection.execute(
                "SELECT candidate_id,revision FROM processing_candidates WHERE job_id=? "
                "ORDER BY ordinal,candidate_id LIMIT ? OFFSET ?",
                (str(job_id), limit + 1, offset),
            ).fetchall()
        )
        entries: list[ReviewEntry] = []
        for row in rows[:limit]:
            candidate = self.candidate(UUID(row_text(row, "candidate_id")), row["revision"])
            self._available(candidate)
            quotes = tuple(
                self.engine.validate(item.evidence) for item in candidate.draft.content.evidence
            )
            entries.append(
                ReviewEntry(
                    candidate=candidate,
                    candidate_sha256=canonical_object_sha256(candidate),
                    quotes=quotes,
                    policy_decision=self.decision(candidate),
                    decision=self._last_decision(candidate) if include_decisions else "pending",
                )
            )
        return ReviewFile(
            job_id=job_id,
            entries=tuple(entries),
            next_offset=offset + limit if len(rows) > limit else None,
        )

    def _record(self, candidate: CandidateMemory, decision: str) -> None:
        # Construct the client before entering its writer. Initialization itself
        # uses that writer and cannot be re-entered from the same owner thread.
        state = (
            self.engine.character(self.processing.owner).state
            if self.processing.info.kind == "character"
            else None
        )

        def write(connection: sqlite3.Connection) -> None:
            with self.database.transaction(connection):
                latest = connection.execute(
                    "SELECT decision FROM review_decisions WHERE candidate_id=? "
                    "AND revision=? ORDER BY rowid DESC LIMIT 1",
                    (str(candidate.candidate_id), candidate.revision),
                ).fetchone()
                if latest is not None and row_text(latest, "decision") == decision:
                    return
                association = connection.execute(
                    "SELECT fingerprint FROM processing_candidates WHERE candidate_id=? "
                    "AND revision=?",
                    (str(candidate.candidate_id), candidate.revision),
                ).fetchone()
                if association is None:
                    raise ProcessingError("candidate_job_unavailable")
                stamp = timestamp_from_datetime(self.engine.clock.now())
                payload = model_bytes(candidate)
                connection.execute(
                    "INSERT INTO review_decisions VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        str(candidate.candidate_id),
                        candidate.revision,
                        row_text(association, "fingerprint"),
                        decision,
                        stamp,
                        payload,
                        payload_hash(payload),
                    ),
                )
                if decision == "reject" and state is not None:
                    for claim_key in state.claim_keys(connection, candidate.draft):
                        connection.execute(
                            "INSERT OR IGNORE INTO suppression_targets VALUES('claim',?)",
                            (claim_key,),
                        )
                    connection.execute("UPDATE store_metadata SET policy_epoch=policy_epoch+1")
                    connection.execute(
                        "UPDATE profile_policy SET selection_epoch=selection_epoch+1"
                    )
                    connection.execute(
                        "INSERT INTO index_outbox(kind,policy_epoch,recorded_at_us) "
                        "SELECT 'suppression_changed',policy_epoch,recorded_clock_us FROM "
                        "store_metadata"
                    )
                _, recorded = self.database.recorded_stamp(connection)
                connection.execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?)",
                    (
                        str(uuid4()),
                        "candidate_review",
                        str(candidate.candidate_id),
                        recorded,
                        "operator_" + decision,
                    ),
                )

        self.database.write(write)

    def apply(self, review: ReviewFile) -> ReviewReceipt:
        """Apply exact explicitly selected decisions; per-candidate receipts make crash retry safe.

        A review file never supplies replacement source text, approval authority or
        candidate payload. Only decision may change; all originals are reread before
        acceptance. Pending entries remain proposals, and rejecting never forgets a belief.
        """
        if self.authority.mode != "confirmed_user":
            raise ProcessingError("operator_confirmation_required")
        review = ReviewFile.model_validate_json(review.model_dump_json())
        self.processing.definition(review.job_id)
        if len({item.candidate.candidate_id for item in review.entries}) != len(review.entries):
            raise ProcessingError("duplicate_review_selection")
        checked: list[tuple[ReviewEntry, CandidateMemory]] = []
        for entry in review.entries:
            candidate = self.candidate(entry.candidate.candidate_id, entry.candidate.revision)
            if (
                candidate != entry.candidate
                or canonical_object_sha256(candidate) != entry.candidate_sha256
            ):
                raise ProcessingError("review_candidate_changed")
            association = self.database.read(
                lambda connection, candidate=candidate: connection.execute(
                    "SELECT job_id FROM processing_candidates WHERE candidate_id=? AND revision=?",
                    (str(candidate.candidate_id), candidate.revision),
                ).fetchone()
            )
            if association is None or row_text(association, "job_id") != str(review.job_id):
                raise ProcessingError("review_job_mismatch")
            self._available(candidate)
            quotes = tuple(
                self.engine.validate(item.evidence) for item in candidate.draft.content.evidence
            )
            if quotes != entry.quotes:
                raise ProcessingError("review_quotes_changed")
            if entry.decision == "approve":
                validate_memory_write(
                    candidate.draft, predicates=(*initial_predicates(), *phase_seven_predicates())
                )
            checked.append((entry, candidate))
        approved: list[CanonicalId] = []
        rejected: list[CanonicalId] = []
        for entry, candidate in checked:
            key = "processing-review:" + str(candidate.candidate_id) + "@" + str(candidate.revision)
            if entry.decision == "approve":
                if self.processing.info.kind == "library":
                    result = LibraryService(self.engine, authority=self.authority).approve(
                        self.processing.owner,
                        ApprovalRequest(idempotency_key=key, candidate=candidate),
                    )
                    approved.append(result.knowledge_id)
                else:
                    receipt = self.engine.character(
                        self.processing.owner, authority=self.authority
                    ).remember(RememberRequest(idempotency_key=key, draft=candidate.draft))
                    approved.append(receipt.affected[0].memory_id)
                self._record(candidate, "approve")
            elif entry.decision == "reject":
                self._record(candidate, "reject")
                rejected.append(candidate.candidate_id)
        return ReviewReceipt(approved=tuple(approved), rejected=tuple(rejected))
