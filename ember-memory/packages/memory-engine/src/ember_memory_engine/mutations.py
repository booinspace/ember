"""Engine-internal mutations behavior; executes on owned SQLite workers."""

import json
import sqlite3
from uuid import uuid4

from ember_memory_abi.common import (
    canonical_object_sha256,
)
from ember_memory_abi.errors import (
    AccessDeniedError,
    ErrorDetails,
    InvalidEvidenceError,
    InvalidPredicateError,
    RevisionConflictError,
    UnsupportedWriteError,
)
from ember_memory_abi.interfaces import (
    DisableRequest,
    ForgetRequest,
    MutationReceipt,
    MutationRequest,
    PinRequest,
    RecordAccessRequest,
    RecordRevision,
    RememberRequest,
    RevisionMutation,
    SupersedeRequest,
    UnpinRequest,
    UpdateRequest,
)
from ember_memory_abi.memory import (
    FactPayload,
    Memory,
    MemoryDraft,
)

from ._state import CharacterState
from .database import (
    check_deadline,
    model_bytes,
    payload_hash,
    row_text,
)
from .errors import IdempotencyConflictError


def mutate(
    state: CharacterState, connection: sqlite3.Connection, operation: str, request: MutationRequest
) -> MutationReceipt:
    """Commit validated effects, receipt and audit together, or replay the exact prior request."""
    if state.authority.mode != "confirmed_user":
        raise AccessDeniedError()
    request = type(request).model_validate_json(request.model_dump_json())
    check_deadline(state.database.clock, request.options.deadline_at)
    if request.extensions or request.options.extensions:
        raise UnsupportedWriteError()
    semantic = request.model_dump(mode="json", exclude={"options"})
    request_hash = canonical_object_sha256(semantic)
    with state.database.transaction(connection):
        old_receipt = connection.execute(
            (
                "SELECT operation,request_sha256,receipt_json FROM mutation_keys WHERE "
                "idempotency_key=?"
            ),
            (request.idempotency_key,),
        ).fetchone()
        if old_receipt is not None:
            if (
                row_text(old_receipt, "operation") != operation
                or row_text(old_receipt, "request_sha256") != request_hash
            ):
                raise IdempotencyConflictError()
            return MutationReceipt.model_validate_json(
                row_text(old_receipt, "receipt_json")
            ).model_copy(update={"outcome": "already_applied"})
        old: Memory | None = None
        if isinstance(request, RevisionMutation):
            old = state.read_memory(
                connection, request.memory_id, allow_suppressed=operation == "forget"
            )
            if old.revision != request.expected_revision:
                raise RevisionConflictError(
                    ErrorDetails(
                        record_id=old.memory_id,
                        expected_revision=request.expected_revision,
                        actual_revision=old.revision,
                    )
                )
        if isinstance(request, (RememberRequest, UpdateRequest)):
            state.validate_draft(connection, request.draft)
        if isinstance(request, SupersedeRequest):
            validate_transition(state, connection, request, old)
        stamp, recorded = state.database.recorded_stamp(connection)
        affected = apply_validated_mutation(state, connection, request, old, stamp, recorded)
        check_deadline(state.database.clock, request.options.deadline_at)
        receipt = MutationReceipt(
            operation_id=uuid4(),
            idempotency_key=request.idempotency_key,
            outcome="applied",
            affected=tuple(affected),
            recorded_at=stamp,
        )
        connection.execute(
            "INSERT INTO mutation_keys VALUES(?,?,?,?)",
            (request.idempotency_key, operation, request_hash, model_bytes(receipt)),
        )
        connection.execute(
            "INSERT INTO index_outbox(kind,record_id,policy_epoch,recorded_at_us) "
            "SELECT ?,?,policy_epoch,? FROM store_metadata",
            (operation, str(affected[0].memory_id) if affected else None, recorded),
        )
        connection.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?,?)",
            (
                str(receipt.operation_id),
                operation,
                str(affected[0].memory_id) if affected else None,
                recorded,
                "confirmed_user",
            ),
        )
    return receipt


def validate_transition(
    state: CharacterState,
    connection: sqlite3.Connection,
    request: SupersedeRequest,
    old: Memory | None,
) -> None:
    """Validate compatible predicate/evidence for trusted human or policy transactions."""
    state.validate_draft(connection, request.replacement)
    if old is None or not request.transition_evidence:
        raise InvalidEvidenceError()
    old_payload, new_payload = old.content.payload, request.replacement.content.payload
    if (
        not isinstance(old_payload, FactPayload)
        or not isinstance(new_payload, FactPayload)
        or old_payload.subject != new_payload.subject
        or old_payload.predicate != new_payload.predicate
    ):
        raise InvalidPredicateError()
    definition = next(item for item in state.predicates if item.name == old_payload.predicate)
    if not definition.replacement_meaningful:
        raise InvalidPredicateError()
    for assertion in request.transition_evidence:
        if not state.source_permitted(connection, assertion.evidence.source, "evidence_read"):
            raise AccessDeniedError()
        state.resolver.validate(assertion.evidence)
        state.resolver.persist_evidence(
            assertion.evidence,
            local_connection=connection,
            local_store_id=state.character_id,
        )


def _apply_forget(
    state: CharacterState,
    connection: sqlite3.Connection,
    old: Memory,
    memory: Memory,
    recorded: int,
) -> None:
    impact = state.forget_impact(connection, old.memory_id, old.revision)
    connection.execute(
        "INSERT OR IGNORE INTO suppression_targets VALUES('memory',?)",
        (str(old.memory_id),),
    )
    for key in impact.claim_fingerprints:
        connection.execute("INSERT OR IGNORE INTO suppression_targets VALUES('claim',?)", (key,))
    for reference in impact.source_parts:
        connection.execute(
            "INSERT OR IGNORE INTO suppression_targets VALUES('source',?)",
            (state.source_dependency_key(reference),),
        )
    for row in connection.execute(
        "SELECT DISTINCT target_key FROM memory_dependencies WHERE memory_id=? "
        "AND target_kind='origin'",
        (str(old.memory_id),),
    ).fetchall():
        connection.execute(
            "INSERT OR IGNORE INTO suppression_targets VALUES('origin',?)",
            (row_text(row, "target_key"),),
        )
    connection.execute(
        "DELETE FROM pins WHERE memory_id IN (SELECT d.memory_id FROM "
        "memory_dependencies d JOIN suppression_targets s ON "
        "d.target_kind=s.target_kind AND d.target_key=s.target_key)"
    )
    connection.execute("UPDATE store_metadata SET policy_epoch=policy_epoch+1")
    connection.execute(
        (
            "INSERT INTO "
            "index_outbox(kind,record_id,revision,policy_epoch,recorded_at_us)"
            " SELECT 'suppression_changed',?,?,policy_epoch,? FROM "
            "store_metadata"
        ),
        (str(old.memory_id), memory.revision, recorded),
    )


def _record_access(
    state: CharacterState,
    connection: sqlite3.Connection,
    request: RecordAccessRequest,
) -> list[RecordRevision]:
    affected: list[RecordRevision] = []
    for access in request.accesses:
        memory = state.read_memory(connection, access.memory_id, access.memory_revision)
        if memory.lifecycle != "active":
            raise AccessDeniedError()
        connection.execute(
            (
                "INSERT INTO access_stats VALUES(?,?,1,?) ON "
                "CONFLICT(memory_id,revision) DO UPDATE SET "
                "access_count=access_count+1,last_accessed_at=excluded.last_accessed_at"
            ),
            (str(access.memory_id), access.memory_revision, access.accessed_at),
        )
        affected.append(RecordRevision(memory_id=memory.memory_id, revision=memory.revision))
    return affected


def apply_validated_mutation(
    state: CharacterState,
    connection: sqlite3.Connection,
    request: MutationRequest,
    old: Memory | None,
    stamp: str,
    recorded: int,
) -> list[RecordRevision]:
    """Apply previously authorized, validated effects inside the caller-owned transaction."""
    affected: list[RecordRevision] = []
    if isinstance(request, RememberRequest):
        memory = state.create_memory(connection, request.draft, stamp)
        affected.append(RecordRevision(memory_id=memory.memory_id, revision=memory.revision))
    elif isinstance(request, UpdateRequest) and old is not None:
        memory = state.revise_memory(old, stamp, draft=request.draft)
        state.append_revision(connection, memory)
        affected.append(RecordRevision(memory_id=memory.memory_id, revision=memory.revision))
    elif isinstance(request, SupersedeRequest) and old is not None:
        previous = state.revise_memory(
            old, stamp, lifecycle="superseded", valid_until=request.previous_valid_until
        )
        state.append_revision(connection, previous)
        assertions = list(request.replacement.content.evidence)
        seen = {item.evidence.evidence_id for item in assertions}
        for assertion in request.transition_evidence:
            if assertion.evidence.evidence_id not in seen:
                assertions.append(assertion.model_copy(update={"relation": "transition"}))
                seen.add(assertion.evidence.evidence_id)
        replacement = MemoryDraft.model_validate(
            {
                **request.replacement.model_dump(),
                "content": {
                    **request.replacement.content.model_dump(),
                    "evidence": tuple(assertions),
                },
            }
        )
        memory = state.create_memory(connection, replacement, stamp, origin=old.origin)
        # The transition changes the assertion, not its derivation/seed lineage.
        # Corroborating source history stays in the ledger instead of growing JSON.
        connection.execute(
            "INSERT OR IGNORE INTO memory_dependencies SELECT ?,?,target_kind,target_key "
            "FROM memory_dependencies WHERE memory_id=? AND revision=? AND target_kind='origin'",
            (str(memory.memory_id), memory.revision, str(old.memory_id), old.revision),
        )
        connection.execute(
            "INSERT INTO memory_supersessions VALUES(?,?,?,?,?,?)",
            (
                str(old.memory_id),
                old.revision,
                str(memory.memory_id),
                memory.revision,
                json.dumps([item.model_dump(mode="json") for item in request.transition_evidence]),
                recorded,
            ),
        )
        from ember_memory_abi.related import MemoryReference, MemoryRelationship

        edge = MemoryRelationship(
            relationship_id=uuid4(),
            kind="supersedes",
            source=MemoryReference(memory_id=old.memory_id, revision=old.revision),
            target=MemoryReference(memory_id=memory.memory_id, revision=memory.revision),
            evidence=request.transition_evidence,
            origins=tuple(m.origin for m in (old, memory) if m.origin),
        )
        edge_payload = model_bytes(edge)
        connection.execute(
            "INSERT INTO memory_relationships VALUES(?,?,?,?,?,?,?,?)",
            (
                str(edge.relationship_id),
                edge.kind,
                str(old.memory_id),
                old.revision,
                str(memory.memory_id),
                memory.revision,
                edge_payload,
                payload_hash(edge_payload),
            ),
        )
        affected.extend(
            (
                RecordRevision(memory_id=previous.memory_id, revision=previous.revision),
                RecordRevision(memory_id=memory.memory_id, revision=memory.revision),
            )
        )
    elif isinstance(request, (DisableRequest, ForgetRequest)) and old is not None:
        lifecycle = "forgotten" if isinstance(request, ForgetRequest) else "disabled"
        memory = state.revise_memory(old, stamp, lifecycle=lifecycle)
        state.append_revision(connection, memory)
        if isinstance(request, ForgetRequest):
            _apply_forget(state, connection, old, memory, recorded)
        affected.append(RecordRevision(memory_id=memory.memory_id, revision=memory.revision))
    elif isinstance(request, PinRequest) and old is not None:
        if old.lifecycle != "active":
            raise AccessDeniedError()
        connection.execute(
            (
                "INSERT INTO pins VALUES(?,?,?,?) ON CONFLICT(memory_id) DO UPDATE "
                "SET "
                "memory_revision=excluded.memory_revision,priority=excluded.priority,pinned_at=excluded.pinned_at"
            ),
            (str(old.memory_id), old.revision, request.priority, stamp),
        )
        affected.append(RecordRevision(memory_id=old.memory_id, revision=old.revision))
    elif isinstance(request, UnpinRequest) and old is not None:
        connection.execute("DELETE FROM pins WHERE memory_id=?", (str(old.memory_id),))
        affected.append(RecordRevision(memory_id=old.memory_id, revision=old.revision))
    elif isinstance(request, RecordAccessRequest):
        affected.extend(_record_access(state, connection, request))
    else:
        raise UnsupportedWriteError()
    return affected
