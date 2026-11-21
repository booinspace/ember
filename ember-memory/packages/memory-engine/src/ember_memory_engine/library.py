"""Operator approval, independent seeding, and scoped delegation for OpenCode notes."""

import sqlite3
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import Field

from ember_memory_abi.archive import (
    ContentPart,
    Document,
    ImportCursor,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.common import (
    CanonicalId,
    ContractModel,
    Revision,
    WireString,
    canonical_object_sha256,
    timestamp_from_datetime,
)
from ember_memory_abi.errors import (
    AccessDeniedError,
    InvalidEvidenceError,
    NotFoundError,
    RevisionConflictError,
)
from ember_memory_abi.evidence import (
    Evidence,
    EvidenceAssertion,
    SourceRef,
    make_evidence_span,
    source_text_sha256,
)
from ember_memory_abi.interfaces import MutationReceipt, RecordRevision
from ember_memory_abi.memory import (
    ApprovedKnowledge,
    CandidateMemory,
    Entity,
    EntityRef,
    EntityValue,
    EpisodePayload,
    FactPayload,
    Memory,
    MemoryContent,
    MemoryDraft,
    OriginMapping,
    PlanPayload,
    ValidationVerdict,
)
from ember_memory_abi.predicates import (
    initial_predicates,
    phase_seven_predicates,
    validate_memory_write,
)
from ember_memory_abi.temporal import SourceTimestamp, TemporalBounds, TemporalMetadata

from .database import load_model, model_bytes, payload_hash, row_int, row_text
from .engine import CanonicalEngine
from .errors import CorruptStoreError, IdempotencyConflictError
from .models import MutationAuthority, SourceKey
from .source_access import source_keys
from .specifications import specification_rows

OPENCODE_LIBRARY = "OpenCode"
OPENCODE_NOTE_CHARS = 512


class ApprovalRequest(ContractModel):
    """Exact candidate reviewed by a trusted operator, not a verdict from a source/model."""

    idempotency_key: WireString
    candidate: CandidateMemory


class KnowledgeSelection(ContractModel):
    """Pin an immutable approved revision; latest imports cannot change this selection."""

    knowledge_id: CanonicalId
    revision: Revision


class SeedRequest(ContractModel):
    """Selected library snapshots; destination identity is bound by trusted maintenance."""

    idempotency_key: WireString
    library_id: CanonicalId
    selections: Annotated[tuple[KnowledgeSelection, ...], Field(min_length=1, max_length=1000)]


def entity_key(reference: EntityRef) -> tuple[CanonicalId, CanonicalId]:
    """Use logical IDs as mapping keys; contract extensions can contain mutable JSON."""
    return reference.store_id, reference.entity_id


def entity_references(draft: MemoryDraft) -> tuple[EntityRef, ...]:
    """Collect supported semantic entity references without scanning arbitrary JSON."""
    payload = draft.content.payload
    if isinstance(payload, FactPayload):
        return (
            (payload.subject, payload.value.value)
            if isinstance(payload.value, EntityValue)
            else (payload.subject,)
        )
    if isinstance(payload, EpisodePayload):
        return (*payload.participants, *((payload.place,) if payload.place else ()))
    return ()


def _receipt(connection: sqlite3.Connection, key: str, operation: str, digest: str) -> str | None:
    row = connection.execute(
        "SELECT * FROM maintenance_receipts WHERE idempotency_key=?", (key,)
    ).fetchone()
    if row is None:
        return None
    if row_text(row, "operation") != operation or row_text(row, "request_sha256") != digest:
        raise IdempotencyConflictError()
    return row_text(row, "receipt_json")


def _save_receipt(
    connection: sqlite3.Connection, key: str, operation: str, digest: str, payload: str
) -> None:
    connection.execute(
        "INSERT INTO maintenance_receipts VALUES(?,?,?,?)", (key, operation, digest, payload)
    )


class LibraryService:
    """Blocking trusted maintenance boundary; libraries own approvals, characters own beliefs."""

    def __init__(
        self, engine: CanonicalEngine, *, authority: MutationAuthority | None = None
    ) -> None:
        self.engine = engine
        self.authority = authority or MutationAuthority()

    def _authorize(self) -> None:
        if self.authority.mode != "confirmed_user":
            raise AccessDeniedError()

    def put_entity(self, library_id: CanonicalId, entity: Entity) -> None:
        """Register library-owned entity context explicitly, independently of any character."""
        self._authorize()
        info, _ = self.engine.maintenance_store(library_id)
        if info.kind != "library":
            raise AccessDeniedError()
        self.engine.put_owned_entity(library_id, entity)

    def ensure_opencode_library(self) -> CanonicalId:
        """Provision the reserved library through the runtime's serialized owner."""
        if self.authority.mode != "opencode_notes":
            raise AccessDeniedError()
        library = next(
            (
                info
                for info in self.engine.stores()
                if info.kind == "library" and info.namespace == OPENCODE_LIBRARY
            ),
            None,
        ) or self.engine.create_library(OPENCODE_LIBRARY)
        self.engine.maintenance_store(library.store_id)
        return library.store_id

    def save_opencode_note(self, character_id: CanonicalId, note: str) -> MutationReceipt:
        """Append an attributed note to the fixed library and adopt it into this character."""
        if self.authority.mode != "opencode_notes":
            raise AccessDeniedError()
        note = note.strip()
        if not 1 <= len(note) <= OPENCODE_NOTE_CHARS or not note.isprintable():
            raise ValueError("Expected a short, single-line OpenCode note.")
        character = self.engine.character(character_id)
        library_id = self.ensure_opencode_library()
        writer = self.engine.archive(library_id)
        digest = canonical_object_sha256({"character": str(character_id), "note": note})
        key = SourceKey(adapter="opencode_note", record_kind="document", external_id=digest)
        assigned = writer.prepare_keys((key,))
        record_id = assigned[0].record_id
        existing = writer.latest_record("document", record_id)
        if existing is not None and not isinstance(existing, Document):
            raise CorruptStoreError()
        stamp = timestamp_from_datetime(self.engine.clock.now())
        document = (
            existing
            if existing is not None
            else Document(
                source=SourceRef(
                    source_store_id=library_id,
                    record_kind="document",
                    record_id=record_id,
                    record_revision=1,
                ),
                recorded_at=stamp,
                title="Delegated OpenCode session note",
                created_time=SourceTimestamp(value=stamp, basis="opencode_note_received"),
                parts=(
                    ContentPart(
                        part_id=uuid4(),
                        kind="text",
                        status="available",
                        text=note,
                        content_sha256=source_text_sha256(note),
                    ),
                ),
            )
        )
        if len(document.parts) != 1 or document.parts[0].text != note:
            raise IdempotencyConflictError()
        # A replay uses the stored record and original clocks rather than rebuilding a source.
        if existing is None:
            writer.write_batch(
                NormalizedArchiveBatch(
                    source_store_id=library_id,
                    source_namespace=OPENCODE_LIBRARY,
                    idempotency_key="opencode-note:" + digest,
                    processing_spec=ProcessingSpecRef(
                        name="ember.opencode_note",
                        version="0.1.0",
                        specification_sha256=canonical_object_sha256(
                            {"policy": "delegated-opencode-note-v1"}
                        ),
                    ),
                    records=(document,),
                    resume_cursor=ImportCursor(
                        logical_inventory_sha256=digest,
                        component_sha256=digest,
                        next_record_index=1,
                        adapter="opencode_note",
                        adapter_version="0.1.0",
                    ),
                ),
                keys=assigned,
            )
        part = document.parts[0]
        observed_at = document.created_time.value
        if observed_at is None:
            raise CorruptStoreError()
        candidate = CandidateMemory(
            candidate_id=record_id,
            owner_store_id=library_id,
            revision=1,
            recorded_at=observed_at,
            draft=MemoryDraft(
                temporal=TemporalBounds(observed_at=observed_at, basis="opencode_note_received"),
                content=MemoryContent(
                    kind="episode",
                    payload=EpisodePayload(event=note),
                    modality="BELIEF",
                    confidence=0.5,
                    importance=0.7,
                    evidence=(
                        EvidenceAssertion(
                            evidence=Evidence(
                                evidence_id=part.part_id,
                                source=document.source.model_copy(update={"part_id": part.part_id}),
                                span=make_evidence_span(note, 0, len(note)),
                            ),
                            relation="supports",
                            modality="BELIEF",
                            authority="delegated_opencode",
                            verdict="delegated_note",
                        ),
                    ),
                ),
            ),
        )
        approved = self._approve(
            library_id,
            ApprovalRequest(idempotency_key="opencode-note:" + digest, candidate=candidate),
        )
        receipt = self._seed(
            character_id,
            SeedRequest(
                idempotency_key="opencode-note:" + digest,
                library_id=library_id,
                selections=(
                    KnowledgeSelection(
                        knowledge_id=approved.knowledge_id, revision=approved.revision
                    ),
                ),
            ),
        )
        # A replay must not report a forgotten note as newly saved or resurrect it.
        memory = character.get(receipt.affected[0].memory_id)
        if memory.lifecycle != "active":
            raise AccessDeniedError()
        return receipt

    def approve(self, library_id: CanonicalId, request: ApprovalRequest) -> ApprovedKnowledge:
        """Validate exact evidence and persist an immutable, manually approved snapshot."""
        self._authorize()
        return self._approve(library_id, request)

    def _approve(self, library_id: CanonicalId, request: ApprovalRequest) -> ApprovedKnowledge:
        delegated = self.authority.mode == "opencode_notes"
        request = ApprovalRequest.model_validate_json(request.model_dump_json())
        info, database = self.engine.maintenance_store(library_id)
        if info.kind != "library" or request.candidate.owner_store_id != library_id:
            raise AccessDeniedError()
        if request.extensions or request.candidate.extensions:
            raise AccessDeniedError()
        digest = canonical_object_sha256(request)
        candidate = request.candidate
        operation = "accept_opencode_note" if delegated else "approve"
        policy = "delegated-opencode-note-v1" if delegated else "exact-evidence-human-review-v1"
        reason = (
            "operator_enabled_opencode_notes" if delegated else "operator_reviewed_exact_evidence"
        )

        def write(connection: sqlite3.Connection) -> ApprovedKnowledge:
            with database.transaction(connection):
                replay = _receipt(connection, request.idempotency_key, operation, digest)
                if replay is not None:
                    return ApprovedKnowledge.model_validate_json(replay)
                validate_memory_write(
                    candidate.draft, predicates=(*initial_predicates(), *phase_seven_predicates())
                )
                entities: dict[CanonicalId, int] = {}
                for reference in entity_references(candidate.draft):
                    if (
                        reference.store_id != library_id
                        or connection.execute(
                            "SELECT 1 FROM entities WHERE entity_id=?", (str(reference.entity_id),)
                        ).fetchone()
                        is None
                    ):
                        raise AccessDeniedError()
                    entity_row = connection.execute(
                        "SELECT MAX(revision) AS revision FROM entities WHERE entity_id=?",
                        (str(reference.entity_id),),
                    ).fetchone()
                    entities[reference.entity_id] = row_int(entity_row, "revision")
                assertions = candidate.draft.content.evidence
                if not any(item.relation == "supports" for item in assertions):
                    raise InvalidEvidenceError()
                for reference in (
                    *(item.evidence.source for item in assertions),
                    *(
                        source
                        for item in candidate.draft.content.provenance
                        for source in item.input_sources
                    ),
                ):
                    if reference.source_store_id != library_id:
                        raise AccessDeniedError()
                    if connection.execute(
                        "SELECT 1 FROM suppression_targets WHERE target_kind='source' "
                        "AND target_key IN (?,?)",
                        source_keys(reference),
                    ).fetchone():
                        raise AccessDeniedError()
                    self.engine.resolve(reference)
                for item in assertions:
                    self.engine.persist_evidence(
                        item.evidence, local_connection=connection, local_store_id=library_id
                    )
                latest: object = connection.execute(
                    "SELECT MAX(revision) FROM approved_knowledge WHERE knowledge_id=?",
                    (str(candidate.candidate_id),),
                ).fetchone()[0]
                if candidate.revision != (latest + 1 if isinstance(latest, int) else 1):
                    raise RevisionConflictError()
                stamp, recorded = database.recorded_stamp(connection)
                approved = ApprovedKnowledge(
                    knowledge_id=candidate.candidate_id,
                    library_id=library_id,
                    revision=candidate.revision,
                    draft=candidate.draft,
                    recorded_at=stamp,
                    approval=ValidationVerdict(
                        policy=ProcessingSpecRef(
                            name="ember.opencode_note"
                            if delegated
                            else "ember.manual_library_approval",
                            version="0.1.0",
                            specification_sha256=canonical_object_sha256({"policy": policy}),
                        ),
                        verdict="approved",
                        reasons=(reason,),
                        evaluated_at=stamp,
                        manual_override=not delegated,
                    ),
                )
                for table, snapshot in (
                    ("candidates", candidate),
                    ("approved_knowledge", approved),
                ):
                    payload = model_bytes(snapshot)
                    if table == "candidates":
                        staged = connection.execute(
                            "SELECT * FROM candidates WHERE candidate_id=? AND revision=?",
                            (str(candidate.candidate_id), candidate.revision),
                        ).fetchone()
                        if staged is not None:
                            if load_model(staged, CandidateMemory) != candidate:
                                raise RevisionConflictError()
                            continue
                    connection.execute(
                        f"INSERT INTO {table} VALUES(?,?,?,?)",
                        (
                            str(candidate.candidate_id),
                            candidate.revision,
                            payload,
                            payload_hash(payload),
                        ),
                    )
                for identity, revision in entities.items():
                    connection.execute(
                        "INSERT INTO knowledge_entities VALUES(?,?,?,?)",
                        (str(approved.knowledge_id), approved.revision, str(identity), revision),
                    )
                _save_receipt(
                    connection, request.idempotency_key, operation, digest, model_bytes(approved)
                )
                connection.execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?)",
                    (
                        str(uuid4()),
                        "accept_opencode_note" if delegated else "approve_library",
                        str(approved.knowledge_id),
                        recorded,
                        self.authority.mode,
                    ),
                )
                return approved

        return database.write(write)

    def knowledge(
        self, library_id: CanonicalId, selection: KnowledgeSelection
    ) -> ApprovedKnowledge:
        """Read an exact approved library revision for operator inspection or explicit seed."""
        info, database = self.engine.maintenance_store(library_id)
        if info.kind != "library":
            raise AccessDeniedError()

        def read(connection: sqlite3.Connection) -> ApprovedKnowledge:
            row = connection.execute(
                "SELECT * FROM approved_knowledge WHERE knowledge_id=? AND revision=?",
                (str(selection.knowledge_id), selection.revision),
            ).fetchone()
            if row is None:
                raise NotFoundError()
            approved = load_model(row, ApprovedKnowledge)
            if (approved.library_id, approved.knowledge_id, approved.revision) != (
                library_id,
                selection.knowledge_id,
                selection.revision,
            ):
                from .errors import CorruptStoreError

                raise CorruptStoreError()
            return approved

        return database.read(read)

    def has_seed(
        self, character_id: CanonicalId, library_id: CanonicalId, knowledge_id: CanonicalId
    ) -> bool:
        """Check historical origin dependencies, including disabled/forgotten seed copies.

        This discovery never restores an old belief or selects a newer revision.
        Existing independent snapshots require explicit update semantics.
        """
        store = self.engine.character(character_id)
        key = canonical_object_sha256({"store": str(library_id), "memory": str(knowledge_id)})
        return store.database.read(
            lambda connection: (
                connection.execute(
                    "SELECT 1 FROM memory_dependencies WHERE target_kind='origin' "
                    "AND target_key=? LIMIT 1",
                    (key,),
                ).fetchone()
                is not None
            )
        )

    def seed(self, character_id: CanonicalId, request: SeedRequest) -> MutationReceipt:
        """Atomically adopt independent snapshots/entities and supporting evidence grants.

        Replay returns identical UUIDs. No archive grant is implied. Suppression is
        checked after remapping, including origin dependencies, before commit.
        """
        self._authorize()
        return self._seed(character_id, request)

    def _seed(self, character_id: CanonicalId, request: SeedRequest) -> MutationReceipt:
        request = SeedRequest.model_validate_json(request.model_dump_json())
        if len({item.knowledge_id for item in request.selections}) != len(request.selections):
            raise ValueError("Duplicate seed selection.")
        store = self.engine.character(character_id, authority=self.authority)
        database = store.database
        digest = canonical_object_sha256(request)

        def write(connection: sqlite3.Connection) -> MutationReceipt:
            with database.transaction(connection):
                replay = _receipt(connection, request.idempotency_key, "seed", digest)
                if replay is not None:
                    return MutationReceipt.model_validate_json(replay).model_copy(
                        update={"outcome": "already_applied"}
                    )
                snapshots = tuple(
                    self.knowledge(request.library_id, item) for item in request.selections
                )
                _, source_database = self.engine.maintenance_store(request.library_id)
                for knowledge in snapshots:
                    references = tuple(p.specification for p in knowledge.draft.content.provenance)
                    definitions = source_database.read(
                        lambda source, references=references: specification_rows(source, references)
                    )
                    for definition in definitions:
                        connection.execute(
                            "INSERT OR IGNORE INTO processing_specs VALUES(?,?,?)",
                            tuple(definition),
                        )
                mapping: dict[tuple[CanonicalId, CanonicalId], EntityRef] = {}
                for knowledge in snapshots:
                    for reference in entity_references(knowledge.draft):
                        if entity_key(reference) not in mapping:
                            mapping[entity_key(reference)] = self._seed_entity(
                                connection, character_id, reference, knowledge
                            )
                memory_ids = {item.knowledge_id: uuid4() for item in snapshots}
                stamp, recorded = database.recorded_stamp(connection)
                operation = uuid4()
                affected: list[RecordRevision] = []
                searchable_changed = False
                grants_changed = False
                for knowledge in snapshots:
                    draft = self._remap(knowledge.draft, mapping, memory_ids)
                    for assertion in draft.content.evidence:
                        ref = assertion.evidence.source
                        previous_grant = connection.execute(
                            "SELECT expires_at FROM source_grants WHERE source_store_id=? "
                            "AND record_kind=? AND record_id=? AND record_revision=? "
                            "AND part_id=? AND capability='evidence_read'",
                            (
                                str(ref.source_store_id),
                                ref.record_kind,
                                str(ref.record_id),
                                ref.record_revision,
                                str(ref.part_id) if ref.part_id else "",
                            ),
                        ).fetchone()
                        grants_changed |= previous_grant is None or previous_grant[0] is not None
                        connection.execute(
                            "INSERT INTO source_grants VALUES(?,?,?,?,?,'evidence_read',NULL) "
                            "ON CONFLICT DO UPDATE SET expires_at=NULL",
                            (
                                str(ref.source_store_id),
                                ref.record_kind,
                                str(ref.record_id),
                                ref.record_revision,
                                str(ref.part_id) if ref.part_id else "",
                            ),
                        )
                    store.state.validate_draft(connection, draft)
                    memory = Memory(
                        memory_id=memory_ids[knowledge.knowledge_id],
                        character_id=character_id,
                        revision=1,
                        lifecycle="active",
                        content=draft.content,
                        temporal=TemporalMetadata(**draft.temporal.model_dump(), recorded_at=stamp),
                        created_at=stamp,
                        origin=OriginMapping(
                            origin_memory_id=knowledge.knowledge_id,
                            origin_memory_revision=knowledge.revision,
                            origin_source_store_id=knowledge.library_id,
                            seed_operation_id=operation,
                            seeded_at=stamp,
                        ),
                    )
                    for kind, key in store.state.dependencies(connection, memory):
                        if connection.execute(
                            "SELECT 1 FROM suppression_targets "
                            "WHERE target_kind=? AND target_key=?",
                            (kind, key),
                        ).fetchone():
                            raise AccessDeniedError()
                    from .claim_projection import identity
                    from .consolidation import attach_support

                    key = identity(memory)[-1]
                    existing = connection.execute(
                        "SELECT p.memory_id FROM claim_projection p JOIN memories m "
                        "ON m.memory_id=p.memory_id AND m.current_revision=p.revision "
                        "JOIN memory_revisions r ON r.memory_id=p.memory_id AND "
                        "r.revision=p.revision "
                        "WHERE p.claim_key=? AND r.lifecycle<>'consolidated' LIMIT 2",
                        (key,),
                    ).fetchall()
                    if len(existing) > 1:
                        raise RevisionConflictError()
                    if existing:
                        target = store.state.read_memory(
                            connection, UUID(row_text(existing[0], "memory_id"))
                        )
                        if target.lifecycle != "active":
                            raise AccessDeniedError()
                        attach_support(store.state, connection, target, draft, origin=memory.origin)
                        affected.append(
                            RecordRevision(memory_id=target.memory_id, revision=target.revision)
                        )
                    else:
                        searchable_changed = True
                        connection.execute(
                            "INSERT INTO memories VALUES(?,1)", (str(memory.memory_id),)
                        )
                        store.state.append_revision(connection, memory)
                        affected.append(RecordRevision(memory_id=memory.memory_id, revision=1))
                connection.execute("UPDATE store_metadata SET policy_epoch=policy_epoch+1")
                if searchable_changed or grants_changed:
                    connection.execute(
                        "INSERT INTO index_outbox(kind,policy_epoch,recorded_at_us) "
                        "SELECT 'seed_grants',policy_epoch,? FROM store_metadata",
                        (recorded,),
                    )
                receipt = MutationReceipt(
                    operation_id=operation,
                    idempotency_key=request.idempotency_key,
                    outcome="applied",
                    affected=tuple(affected),
                    recorded_at=stamp,
                )
                _save_receipt(
                    connection, request.idempotency_key, "seed", digest, model_bytes(receipt)
                )
                connection.execute(
                    "INSERT INTO audit_events VALUES(?,?,?,?,?)",
                    (str(operation), "seed", None, recorded, self.authority.mode),
                )
                return receipt

        return database.write(write)

    def _seed_entity(
        self,
        connection: sqlite3.Connection,
        character_id: CanonicalId,
        origin: EntityRef,
        knowledge: ApprovedKnowledge,
    ) -> EntityRef:
        existing = connection.execute(
            "SELECT entity_id FROM entity_origins WHERE origin_store_id=? AND origin_entity_id=?",
            (str(origin.store_id), str(origin.entity_id)),
        ).fetchone()
        if existing is not None:
            return EntityRef(store_id=character_id, entity_id=UUID(row_text(existing, "entity_id")))
        _, source_db = self.engine.maintenance_store(origin.store_id)

        def read(source: sqlite3.Connection) -> Entity:
            row = source.execute(
                "SELECT e.* FROM entities e JOIN knowledge_entities k "
                "ON e.entity_id=k.entity_id AND e.revision=k.entity_revision "
                "WHERE k.knowledge_id=? AND k.knowledge_revision=? AND k.entity_id=?",
                (str(knowledge.knowledge_id), knowledge.revision, str(origin.entity_id)),
            ).fetchone()
            if row is None:
                raise NotFoundError()
            return load_model(row, Entity)

        original = source_db.read(read)
        reference = EntityRef(store_id=character_id, entity_id=uuid4())
        entity = Entity(
            reference=reference,
            revision=1,
            name=original.name,
            aliases=original.aliases,
            origin=origin,
        )
        payload = model_bytes(entity)
        connection.execute(
            "INSERT INTO entities VALUES(?,1,?,?)",
            (str(reference.entity_id), payload, payload_hash(payload)),
        )
        for alias in dict.fromkeys((entity.name, *entity.aliases)):
            connection.execute(
                "INSERT INTO entity_aliases VALUES(?,?)", (str(reference.entity_id), alias)
            )
        connection.execute(
            "INSERT INTO entity_origins VALUES(?,?,?)",
            (str(origin.store_id), str(origin.entity_id), str(reference.entity_id)),
        )
        return reference

    def _remap(
        self,
        draft: MemoryDraft,
        entities: dict[tuple[CanonicalId, CanonicalId], EntityRef],
        memories: dict[CanonicalId, CanonicalId],
    ) -> MemoryDraft:
        payload = draft.content.payload
        if isinstance(payload, FactPayload):
            updates: dict[str, object] = {"subject": entities[entity_key(payload.subject)]}
            if isinstance(payload.value, EntityValue):
                updates["value"] = EntityValue(value=entities[entity_key(payload.value.value)])
            payload = FactPayload.model_validate({**payload.model_dump(), **updates})
        elif isinstance(payload, EpisodePayload):
            payload = EpisodePayload.model_validate(
                {
                    **payload.model_dump(),
                    "participants": tuple(
                        entities[entity_key(item)] for item in payload.participants
                    ),
                    "place": entities[entity_key(payload.place)] if payload.place else None,
                }
            )
        elif isinstance(payload, PlanPayload) and payload.goal_id is not None:
            if payload.goal_id not in memories:
                raise AccessDeniedError()
            payload = payload.model_copy(update={"goal_id": memories[payload.goal_id]})
        return MemoryDraft.model_validate(
            {**draft.model_dump(), "content": {**draft.content.model_dump(), "payload": payload}}
        )
