"""Trusted policy acceptance and linear append-only support, confined to native sources."""

import re
import sqlite3
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field

from ember_memory_abi.archive import Message, ProcessingSpecRef
from ember_memory_abi.common import CanonicalId, ContractModel, canonical_object_sha256
from ember_memory_abi.errors import AccessDeniedError, MemoryError, RevisionConflictError
from ember_memory_abi.interfaces import MutationReceipt, RecordRevision, SupersedeRequest
from ember_memory_abi.memory import (
    CandidateMemory,
    FactPayload,
    Memory,
    MemoryDraft,
    OriginMapping,
    StringValue,
)
from ember_memory_abi.related import MemoryReference, MemoryRelationship
from ember_memory_abi.temporal import TemporalMetadata

from ._state import CharacterState
from .activation import evaluate
from .archive import source_text
from .claim_projection import identity
from .database import load_model, model_bytes, payload_hash, row_int, row_text
from .engine import CanonicalEngine
from .errors import IdempotencyConflictError
from .models import MutationAuthority
from .mutations import apply_validated_mutation, validate_transition
from .processing_contracts import JobDefinition, PolicyDecision


class NativeAcceptancePolicy(ContractModel):
    """Immutable host rules; model verdict/confidence never supplies acceptance authority."""

    name: Literal["ember.native-acceptance"] = "ember.native-acceptance"
    version: Literal["0.1.0"] = "0.1.0"
    rule: Literal["native-direct-v1"] = "native-direct-v1"
    confidence: float = 0.9
    importance: float = 0.5
    conflicts: Literal["explicit_transition_only"] = "explicit_transition_only"
    equivalence: Literal["exact_scoped_v1"] = "exact_scoped_v1"

    def reference(self) -> ProcessingSpecRef:
        """Bind the full exact definition for audited decisions and selective bundles."""
        return ProcessingSpecRef(
            name=self.name, version=self.version, specification_sha256=canonical_object_sha256(self)
        )


NATIVE_POLICY = NativeAcceptancePolicy()
POLICY = NATIVE_POLICY.reference()


class NativeAcceptanceRequest(ContractModel):
    """Trusted maintenance request; no grants, imports, arbitrary draft or human authority."""

    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    candidate_id: CanonicalId
    expected_revision: Annotated[int, Field(ge=1)] = 1
    policy: ProcessingSpecRef = POLICY


class NativeAcceptanceReceipt(ContractModel):
    """An audited policy decision is independent of operator confirmation and seeding."""

    operation_id: CanonicalId
    candidate_id: CanonicalId
    outcome: Literal["accepted", "supported", "superseded", "deferred", "rejected"]
    reasons: tuple[str, ...]
    policy: ProcessingSpecRef = POLICY
    memory: MemoryReference | None = None
    replay: bool = False


def literal_support(draft: MemoryDraft, quote: str) -> bool:
    """Conservative first-person rules; exact spans alone don't prove claim semantics."""
    payload = draft.content.payload
    normalized = " ".join(quote.casefold().split())
    if not isinstance(payload, FactPayload):
        return (
            draft.content.kind == "episode"
            and getattr(payload, "event", None) == quote
            and re.match(r"^i (?:am|have|use|like|own|work|live|went|did|built)\b", normalized)
            is not None
        )
    if payload.predicate == "personal.owns_object" and isinstance(payload.value, StringValue):
        return (
            payload.value.value == quote
            and payload.polarity == "positive"
            and re.search(r"\b(?:i (?:own|have)|is my)\b", normalized) is not None
            and re.search(
                r"\b(?:said|says|pretend|imagine|fiction|hypothetically|maybe|might|would|could)\b",
                normalized,
            )
            is None
        )
    if not isinstance(payload.value, StringValue) or payload.polarity != "positive":
        return False
    if re.search(
        r"\b(?:said|says|according to|quote|quoted|hypothetically|maybe|might|would|could)\b",
        normalized,
    ):
        return False
    value = " ".join(payload.value.value.casefold().split())
    if not value or value not in normalized:
        return False
    rules = {
        "user.preferred_name": (
            r"\b(?:(?:please )?call me|i prefer "
            r"(?:to be called|the name)|my preferred name is)\s+"
        ),
        "user.pronouns": r"\b(?:my pronouns are|i use (?:the )?pronouns)\s+",
        "user.timezone": r"\b(?:my timezone is|i am in (?:the )?timezone)\s+",
        "user.communication_style": r"\b(?:i prefer|please (?:use|keep|give me))\s+",
        "hardware.primary_gpu": r"\bmy (?:primary|main) gpu is\s+",
        "hardware.owns_gpu": r"\bi (?:own|have)\s+(?:an? )?",
        "music.likes_genre": r"\bi (?:like|enjoy|love)\s+",
        "entity.name": r"\bmy name is\s+",
        "entity.alias": r"\b(?:please call me|i am also known as)\s+",
    }
    rule = rules.get(payload.predicate)
    return (
        rule is not None
        and re.search(rule + re.escape(value) + r"(?=$|[\s.,!;:])", normalized) is not None
    )


def transition_kind(quote: str, draft: MemoryDraft) -> Literal["change", "correction"] | None:
    """Distinguish explicit present change from correcting a previously false interpretation."""
    if not literal_support(draft, quote):
        return None
    text = quote.casefold()
    if re.search(r"\b(correction|actually|incorrect|wrong|not .+? but)\b", text):
        return "correction"
    if re.search(r"\b(now|changed|instead|no longer)\b", text):
        return "change"
    return None


class NativeAcceptanceService:
    """One existing writer transaction owns evidence, acceptance, conflict and receipts."""

    def __init__(self, engine: CanonicalEngine, character_id: CanonicalId) -> None:
        self.engine = engine
        self.store = engine.character(character_id)
        self.state = self.store.state

    def accept(self, request: NativeAcceptanceRequest) -> NativeAcceptanceReceipt:
        """Re-evaluate current canonical policy; replay never reactivates a disabled belief."""
        request = NativeAcceptanceRequest.model_validate_json(request.model_dump_json())
        if request.policy != POLICY or request.extensions:
            raise AccessDeniedError()
        return self.store.database.write(lambda connection: self._accept(connection, request))

    def _accept(
        self, connection: sqlite3.Connection, request: NativeAcceptanceRequest
    ) -> NativeAcceptanceReceipt:
        digest = canonical_object_sha256(request)
        with self.store.database.transaction(connection):
            previous = connection.execute(
                "SELECT * FROM native_acceptance WHERE idempotency_key=?",
                (request.idempotency_key,),
            ).fetchone()
            if previous:
                if row_text(previous, "request_sha256") != digest:
                    raise IdempotencyConflictError()
                return load_model(previous, NativeAcceptanceReceipt).model_copy(
                    update={"replay": True}
                )
            row = connection.execute(
                "SELECT * FROM candidates WHERE candidate_id=? ORDER BY revision DESC LIMIT 1",
                (str(request.candidate_id),),
            ).fetchone()
            if row is None or row_int(row, "revision") != request.expected_revision:
                raise RevisionConflictError()
            candidate = load_model(row, CandidateMemory)
            if candidate.owner_store_id != self.state.character_id:
                raise AccessDeniedError()
            draft = candidate.draft
            stamp, recorded = self.store.database.recorded_stamp(connection)
            provisional = Memory(
                memory_id=uuid4(),
                character_id=self.state.character_id,
                revision=1,
                lifecycle="active",
                content=draft.content,
                temporal=TemporalMetadata(**draft.temporal.model_dump(), recorded_at=stamp),
                created_at=stamp,
            )
            _, _, _, _, key = identity(provisional)
            reasons: list[str] = []
            output = connection.execute(
                "SELECT * FROM activation_decisions WHERE candidate_id=? AND revision=?"
                " ORDER BY rowid DESC LIMIT 1",
                (str(candidate.candidate_id), candidate.revision),
            ).fetchone()
            if output is None:
                reasons.append("processing_lineage_missing")
            else:
                decision = load_model(output, PolicyDecision)
                header = connection.execute(
                    "SELECT j.* FROM processing_jobs j JOIN processing_candidates c ON "
                    "c.job_id=j.job_id "
                    "WHERE c.candidate_id=? AND c.revision=?",
                    (str(candidate.candidate_id), candidate.revision),
                ).fetchone()
                job = load_model(header, JobDefinition) if header is not None else None
                if (
                    job is None
                    or job.request.source_kind != "native"
                    or job.request.specification.extractor != "ember.atomic-claims@0.2.0"
                ):
                    reasons.append("native_processing_profile_required")
                reevaluated = evaluate(
                    decision.inputs.model_copy(update={"duplicate": False, "conflict": False}),
                    evaluated_at=stamp,
                )
                if reevaluated.verdict.verdict != "eligible":
                    reasons.extend(reevaluated.verdict.reasons)
            subject = connection.execute("SELECT subject_id FROM profile_policy").fetchone()[0]
            quotes: list[str] = []
            try:
                self.state.validate_draft(connection, draft)
                for assertion in draft.content.evidence:
                    source = assertion.evidence.source
                    record = self.engine.resolve(source)
                    native = connection.execute(
                        "SELECT 1 FROM source_keys WHERE adapter='native_delivery' AND record_id=? "
                        "AND external_id LIKE 'v2:user:%'",
                        (str(source.record_id),),
                    ).fetchone()
                    if (
                        source.source_store_id != self.state.character_id
                        or not isinstance(record, Message)
                        or record.actor.role != "user"
                        or str(record.actor.actor_id) != subject
                        or not native
                        or record.channel != "final"
                        or assertion.authority != "source_direct_user"
                    ):
                        reasons.append("native_user_binding_unverified")
                    quote = self.engine.validate(assertion.evidence)
                    quotes.append(quote)
                    full_text = source_text(record, source)
                    if not literal_support(draft, quote) or re.search(
                        r"\b(?:said|says|according to|hypothetically|quoted)\b",
                        full_text.casefold(),
                    ):
                        reasons.append("semantic_support_requires_review")
                if (
                    isinstance(draft.content.payload, FactPayload)
                    and str(draft.content.payload.subject.entity_id) != subject
                ):
                    reasons.append("claim_subject_unverified")
            except MemoryError as error:
                reasons.append(error.info.code)
            if connection.execute(
                "SELECT 1 FROM review_decisions r JOIN processing_candidates c ON "
                "c.fingerprint=r.fingerprint "
                "WHERE c.candidate_id=? AND r.decision='reject' LIMIT 1",
                (str(candidate.candidate_id),),
            ).fetchone():
                reasons.append("operator_rejected_claim")
            if connection.execute(
                "SELECT 1 FROM native_acceptance WHERE claim_key=? AND outcome='rejected' LIMIT 1",
                (key,),
            ).fetchone():
                reasons.append("policy_rejected_claim")
            memory: Memory | None = None
            outcome: Literal["accepted", "supported", "superseded", "deferred", "rejected"] = (
                "deferred"
            )
            if not reasons:
                rows = connection.execute(
                    "SELECT p.memory_id,p.revision,r.lifecycle FROM claim_projection p "
                    "JOIN memories m "
                    "ON m.memory_id=p.memory_id AND m.current_revision=p.revision JOIN "
                    "memory_revisions r "
                    "ON r.memory_id=p.memory_id AND r.revision=p.revision WHERE "
                    "p.claim_key=? AND r.lifecycle<>'consolidated' LIMIT 2",
                    (key,),
                ).fetchall()
                if len(rows) > 1:
                    reasons.append("legacy_duplicate_requires_review")
                elif rows:
                    existing = self.state.read_memory(
                        connection, UUID(row_text(rows[0], "memory_id")), allow_suppressed=True
                    )
                    if existing.lifecycle != "active" or self.state.memory_suppressed(
                        connection, existing.memory_id, existing.revision
                    ):
                        reasons.append("existing_claim_inactive")
                    else:
                        memory, outcome = existing, "supported"
                else:
                    memory, outcome, extra = self._new_or_transition(
                        connection,
                        draft,
                        provisional,
                        quotes,
                        stamp,
                        recorded,
                        candidate.candidate_id,
                    )
                    reasons.extend(extra)
            if any(
                reason
                in {
                    "operator_rejected_claim",
                    "policy_rejected_claim",
                    "native_user_binding_unverified",
                }
                for reason in reasons
            ):
                outcome = "rejected"
            if memory and outcome in {"accepted", "supported", "superseded"}:
                attach_support(self.state, connection, memory, draft)
            receipt = NativeAcceptanceReceipt(
                operation_id=uuid4(),
                candidate_id=candidate.candidate_id,
                outcome=outcome,
                reasons=tuple(dict.fromkeys(reasons)) or ("native_policy_satisfied",),
                memory=MemoryReference(memory_id=memory.memory_id, revision=memory.revision)
                if memory
                else None,
            )
            payload = model_bytes(receipt)
            connection.execute(
                "INSERT INTO native_acceptance VALUES(?,?,?,?,?,?,?,?)",
                (
                    request.idempotency_key,
                    digest,
                    str(candidate.candidate_id),
                    candidate.revision,
                    key,
                    outcome,
                    payload,
                    payload_hash(payload),
                ),
            )
            policy = model_bytes(NATIVE_POLICY)
            connection.execute(
                "INSERT OR IGNORE INTO processing_specs VALUES(?,?,?)",
                (POLICY.specification_sha256, policy, payload_hash(policy)),
            )
            connection.execute(
                "INSERT INTO audit_events VALUES(?,?,?,?,?)",
                (
                    str(receipt.operation_id),
                    "native_acceptance",
                    str(memory.memory_id) if memory else None,
                    recorded,
                    "engine_policy:" + outcome,
                ),
            )
            return receipt

    def _new_or_transition(
        self,
        connection: sqlite3.Connection,
        draft: MemoryDraft,
        provisional: Memory,
        quotes: list[str],
        stamp: str,
        recorded: int,
        candidate_id: CanonicalId,
    ) -> tuple[Memory | None, Literal["accepted", "superseded", "deferred"], tuple[str, ...]]:
        payload = draft.content.payload
        conflicts: list[Memory] = []
        if isinstance(payload, FactPayload) and any(
            d.name == payload.predicate and d.cardinality == "one" for d in self.state.predicates
        ):
            rows = connection.execute(
                "SELECT p.memory_id,p.revision FROM claim_projection p JOIN memories m "
                "ON m.memory_id=p.memory_id "
                "AND m.current_revision=p.revision JOIN memory_revisions r ON "
                "r.memory_id=p.memory_id AND r.revision=p.revision "
                "WHERE p.subject_id=? AND p.predicate=? AND r.lifecycle='active' LIMIT 3",
                (str(payload.subject.entity_id), payload.predicate),
            ).fetchall()
            for row in rows:
                old = self.state.read_memory(
                    connection, UUID(row_text(row, "memory_id")), allow_suppressed=True
                )
                # Matching independently confirmed native support may establish its
                # own origin-scoped belief. It does not approve or overwrite imports.
                if old.origin is not None and identity(old)[2] == identity(provisional)[2]:
                    continue
                # Different imported assertions still require explicit operator review.
                if old.origin is not None or self.state.memory_suppressed(
                    connection, old.memory_id, old.revision
                ):
                    return None, "deferred", ("existing_origin_or_suppression_requires_review",)
                conflicts.append(old)
        if conflicts:
            kind = transition_kind(quotes[0], draft) if len(quotes) == 1 else None
            if len(conflicts) == 1 and kind is not None:
                old = conflicts[0]
                at = draft.temporal.observed_at if kind == "change" else None
                replacement = draft
                if at is not None:
                    replacement = draft.model_copy(
                        update={
                            "temporal": draft.temporal.model_copy(
                                update={"valid_from": at, "basis": "explicit_open_end"}
                            )
                        }
                    )
                transition = SupersedeRequest(
                    idempotency_key="native:" + str(provisional.memory_id),
                    memory_id=old.memory_id,
                    expected_revision=old.revision,
                    replacement=replacement,
                    transition_evidence=draft.content.evidence,
                    previous_valid_until=at,
                    reason="native_explicit_" + kind,
                )
                validate_transition(self.state, connection, transition, old)
                changed = apply_validated_mutation(
                    self.state, connection, transition, old, stamp, recorded
                )
                memory = self.state.read_memory(connection, changed[-1].memory_id)
                return memory, "superseded", ()
            for old in conflicts:
                self.record_relationship(
                    connection,
                    MemoryRelationship(
                        relationship_id=uuid4(),
                        kind="conflicts",
                        candidate_id=candidate_id,
                        source=MemoryReference(memory_id=old.memory_id, revision=old.revision),
                        evidence=draft.content.evidence,
                        provenance=draft.content.provenance,
                    ),
                )
            connection.execute("UPDATE profile_policy SET selection_epoch=selection_epoch+1")
            return None, "deferred", ("unresolved_conflict",)
        return self.state.create_memory(connection, draft, stamp), "accepted", ()

    @staticmethod
    def record_relationship(connection: sqlite3.Connection, edge: MemoryRelationship) -> None:
        """Append an already validated edge within the caller-owned canonical transaction."""
        payload = model_bytes(edge)
        connection.execute(
            "INSERT INTO memory_relationships VALUES(?,?,?,?,?,?,?,?)",
            (
                str(edge.relationship_id),
                edge.kind,
                str(edge.source.memory_id),
                edge.source.revision,
                str(edge.target.memory_id) if edge.target else None,
                edge.target.revision if edge.target else None,
                payload,
                payload_hash(payload),
            ),
        )


def attach_support(
    state: CharacterState,
    connection: sqlite3.Connection,
    memory: Memory,
    draft: MemoryDraft,
    *,
    origin: OriginMapping | None = None,
) -> None:
    """Append already validated corroboration, preserving origin/seed metadata without JSON growth.

    The caller owns authorization, exact evidence validation and the writer transaction.
    Historical corroboration is inspectable; it is not hydrated on ordinary chat turns.
    """
    edge = MemoryRelationship(
        relationship_id=uuid4(),
        kind="supports",
        source=MemoryReference(memory_id=memory.memory_id, revision=memory.revision),
        evidence=draft.content.evidence,
        provenance=draft.content.provenance,
        origins=(origin,) if origin else (),
    )
    key = canonical_object_sha256(
        {
            "memory": str(memory.memory_id),
            "sources": [
                {
                    "source": a.evidence.source.model_dump(mode="json"),
                    "span": a.evidence.span.model_dump(mode="json"),
                }
                for a in draft.content.evidence
            ],
            "origin": origin.model_dump(mode="json") if origin else None,
        }
    )
    payload = model_bytes(edge)
    connection.execute(
        "INSERT OR IGNORE INTO memory_supports VALUES(?,?,?,?,?,?)",
        (
            str(edge.relationship_id),
            str(memory.memory_id),
            memory.revision,
            key,
            payload,
            payload_hash(payload),
        ),
    )
    if origin is not None:
        origin_key = canonical_object_sha256(
            {"store": str(origin.origin_source_store_id), "memory": str(origin.origin_memory_id)}
        )
        if connection.execute(
            "SELECT 1 FROM suppression_targets WHERE target_kind='origin' AND target_key=?",
            (origin_key,),
        ).fetchone():
            raise AccessDeniedError()
        connection.execute(
            "INSERT OR IGNORE INTO memory_dependencies VALUES(?,?,'origin',?)",
            (
                str(memory.memory_id),
                memory.revision,
                origin_key,
            ),
        )


class ConsolidateRequest(ContractModel):
    """Explicit maintenance of equivalent accepted revisions within one origin scope."""

    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    survivor: MemoryReference
    duplicate: MemoryReference


def consolidate(
    engine: CanonicalEngine,
    character_id: CanonicalId,
    request: ConsolidateRequest,
    *,
    authority: MutationAuthority | None = None,
) -> MutationReceipt:
    """Retain every original support/seed, without copying growing evidence JSON each merge.

    This trusted maintenance operation requires human authority. It cannot merge
    distinct origins/events/time intervals or overwrite unresolved conflicts.
    """
    if authority is None or authority.mode != "confirmed_user":
        raise AccessDeniedError()
    store = engine.character(character_id, authority=authority)
    state = store.state
    digest = canonical_object_sha256(request)

    def write(connection: sqlite3.Connection) -> MutationReceipt:
        with store.database.transaction(connection):
            saved = connection.execute(
                "SELECT * FROM maintenance_receipts WHERE idempotency_key=?",
                (request.idempotency_key,),
            ).fetchone()
            if saved is not None:
                if (
                    row_text(saved, "operation") != "consolidate"
                    or row_text(saved, "request_sha256") != digest
                ):
                    raise IdempotencyConflictError()
                return MutationReceipt.model_validate_json(
                    row_text(saved, "receipt_json")
                ).model_copy(update={"outcome": "already_applied"})
            survivor = state.read_memory(connection, request.survivor.memory_id)
            duplicate = state.read_memory(connection, request.duplicate.memory_id)
            if (
                survivor.memory_id == duplicate.memory_id
                or survivor.revision != request.survivor.revision
                or duplicate.revision != request.duplicate.revision
                or survivor.lifecycle != "active"
                or duplicate.lifecycle != "active"
                or identity(survivor)[-1] != identity(duplicate)[-1]
            ):
                raise RevisionConflictError()
            # Canonical identity includes origin scope, modality, validity and event
            # anchors. Similar descriptions alone cannot create a redirect.
            for memory in (survivor, duplicate):
                for assertion in memory.content.evidence:
                    if not state.source_permitted(
                        connection, assertion.evidence.source, "evidence_read"
                    ):
                        raise AccessDeniedError()
                    engine.validate(assertion.evidence)
            stamp, recorded = store.database.recorded_stamp(connection)
            previous = state.revise_memory(duplicate, stamp, lifecycle="consolidated")
            state.append_revision(connection, previous)
            edge = MemoryRelationship(
                relationship_id=uuid4(),
                kind="redirects",
                source=request.duplicate,
                target=request.survivor,
                evidence=duplicate.content.evidence,
                origins=tuple(m.origin for m in (duplicate, survivor) if m.origin),
                provenance=duplicate.content.provenance,
            )
            NativeAcceptanceService.record_relationship(connection, edge)
            support = edge.model_copy(
                update={
                    "kind": "supports",
                    "source": request.survivor,
                    "target": request.duplicate,
                    "relationship_id": uuid4(),
                }
            )
            payload = model_bytes(support)
            support_key = canonical_object_sha256(
                {
                    "duplicate": request.duplicate.model_dump(mode="json"),
                    "survivor": request.survivor.model_dump(mode="json"),
                }
            )
            connection.execute(
                "INSERT OR IGNORE INTO memory_supports VALUES(?,?,?,?,?,?)",
                (
                    str(support.relationship_id),
                    str(survivor.memory_id),
                    survivor.revision,
                    support_key,
                    payload,
                    payload_hash(payload),
                ),
            )
            # The small canonical reverse-dependency/anchor tables retain hard
            # derivation origins, even though immutable JSON never grows per mention.
            connection.execute(
                "INSERT OR IGNORE INTO memory_dependencies SELECT ?,?,target_kind,target_key "
                "FROM memory_dependencies WHERE memory_id=? AND revision=? "
                "AND target_kind='origin'",
                (
                    str(survivor.memory_id),
                    survivor.revision,
                    str(duplicate.memory_id),
                    duplicate.revision,
                ),
            )
            connection.execute("UPDATE store_metadata SET policy_epoch=policy_epoch+1")
            receipt = MutationReceipt(
                operation_id=uuid4(),
                idempotency_key=request.idempotency_key,
                outcome="applied",
                recorded_at=stamp,
                affected=(
                    RecordRevision(memory_id=survivor.memory_id, revision=survivor.revision),
                    RecordRevision(memory_id=previous.memory_id, revision=previous.revision),
                ),
            )
            connection.execute(
                "INSERT INTO maintenance_receipts VALUES(?,?,?,?)",
                (
                    request.idempotency_key,
                    "consolidate",
                    digest,
                    model_bytes(receipt),
                ),
            )
            connection.execute(
                "INSERT INTO audit_events VALUES(?,?,?,?,?)",
                (
                    str(receipt.operation_id),
                    "consolidate",
                    str(survivor.memory_id),
                    recorded,
                    "confirmed_user",
                ),
            )
            return receipt

    return store.database.write(write)
