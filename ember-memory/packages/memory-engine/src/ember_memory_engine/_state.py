"""Character-bound canonical MemoryStore: transactional mutations and safe degraded reads."""

import sqlite3
from uuid import UUID, uuid4

from ember_memory_abi.common import (
    CanonicalId,
    canonical_object_sha256,
    timestamp_from_datetime,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.errors import (
    AccessDeniedError,
    InvalidEvidenceError,
    NotFoundError,
    SourceUnavailableError,
)
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.interfaces import (
    RequestOptions,
)
from ember_memory_abi.memory import (
    EntityValue,
    FactPayload,
    Memory,
    MemoryDraft,
    MemorySource,
    OriginMapping,
    StringValue,
)
from ember_memory_abi.predicates import (
    initial_predicates,
    phase_seven_predicates,
    validate_memory_write,
)
from ember_memory_abi.temporal import TemporalMetadata

from .archive import SourceResolver
from .database import (
    SqliteDatabase,
    check_deadline,
    load_model,
    model_bytes,
    payload_hash,
    row_int,
    row_text,
)
from .errors import CorruptStoreError
from .models import ForgetImpact, MutationAuthority, TokenCounter
from .read_budget import ReadBudget
from .retrieval_port import RetrievalBackend


class CharacterState:
    """Engine-internal worker state; SQL helpers require store-owned connection threads."""

    def __init__(
        self,
        database: SqliteDatabase,
        resolver: SourceResolver,
        authority: MutationAuthority,
        token_counter: TokenCounter,
        retriever: RetrievalBackend | None = None,
    ) -> None:
        self.database = database
        self.resolver = resolver
        self.authority = authority
        self.tokens = token_counter
        self.retriever = retriever
        self.max_read_bytes: int | None = None
        self.read_budget: ReadBudget | None = None
        self.predicates = (*initial_predicates(), *phase_seven_predicates())

        def register(connection: sqlite3.Connection) -> None:
            with database.transaction(connection):
                for definition in self.predicates:
                    payload = model_bytes(definition)
                    existing = connection.execute(
                        "SELECT payload_json FROM predicate_definitions WHERE name=? AND version=?",
                        (definition.name, definition.definition_version),
                    ).fetchone()
                    if existing is not None and row_text(existing, "payload_json") != payload:
                        raise CorruptStoreError()
                    connection.execute(
                        "INSERT OR IGNORE INTO predicate_definitions VALUES(?,?,?)",
                        (definition.name, definition.definition_version, payload),
                    )

        database.write(register)

    @property
    def character_id(self) -> CanonicalId:
        """Immutable composition-supplied identity, never chosen by query/model arguments."""
        return self.database.store_id

    def policy_epoch(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT policy_epoch FROM store_metadata").fetchone()
        if row is None:
            raise CorruptStoreError()
        return row_int(row, "policy_epoch")

    def check_policy(self, connection: sqlite3.Connection, epoch: int) -> None:
        if self.policy_epoch(connection) != epoch:
            raise AccessDeniedError()

    def source_dependency_key(self, reference: SourceRef, *, whole: bool = False) -> str:
        return canonical_object_sha256(
            {
                "store": str(reference.source_store_id),
                "kind": reference.record_kind,
                "id": str(reference.record_id),
                "part": None if whole else str(reference.part_id) if reference.part_id else None,
            }
        )

    def source_suppressed(self, connection: sqlite3.Connection, reference: SourceRef) -> bool:
        keys = (
            self.source_dependency_key(reference),
            self.source_dependency_key(reference, whole=True),
        )
        return (
            connection.execute(
                "SELECT 1 FROM suppression_targets WHERE target_kind='source' AND "
                "target_key IN (?,?)",
                keys,
            ).fetchone()
            is not None
        )

    def source_permitted(
        self, connection: sqlite3.Connection, reference: SourceRef, capability: str
    ) -> bool:
        if self.source_suppressed(connection, reference):
            return False
        if reference.source_store_id == self.character_id:
            return True
        return (
            connection.execute(
                "SELECT 1 FROM source_grants WHERE source_store_id=? AND record_kind=? "
                "AND record_id=? AND record_revision=? AND part_id IN (?, '') AND "
                "capability=? AND (expires_at IS NULL OR expires_at>?)",
                (
                    str(reference.source_store_id),
                    reference.record_kind,
                    str(reference.record_id),
                    reference.record_revision,
                    str(reference.part_id) if reference.part_id else "",
                    capability,
                    timestamp_from_datetime(self.database.clock.now()),
                ),
            ).fetchone()
            is not None
        )

    def memory_suppressed(
        self, connection: sqlite3.Connection, memory_id: CanonicalId, revision: int
    ) -> bool:
        if connection.execute(
            "SELECT 1 FROM suppression_targets WHERE target_kind='memory' AND target_key=?",
            (str(memory_id),),
        ).fetchone():
            return True
        return (
            connection.execute(
                "SELECT 1 FROM memory_dependencies d JOIN suppression_targets s ON "
                "d.target_kind=s.target_kind AND d.target_key=s.target_key WHERE "
                "d.memory_id=? AND d.revision=?",
                (str(memory_id), revision),
            ).fetchone()
            is not None
        )

    def read_memory(
        self,
        connection: sqlite3.Connection,
        memory_id: CanonicalId,
        revision: int | None = None,
        options: RequestOptions | None = None,
        *,
        allow_suppressed: bool = False,
    ) -> Memory:
        check_deadline(self.database.clock, options.deadline_at if options else None)
        if self.max_read_bytes is not None:
            size = connection.execute(
                "SELECT length(CAST(r.payload_json AS BLOB)) AS payload_size "
                "FROM memory_revisions r JOIN memories m ON m.memory_id=r.memory_id "
                "WHERE r.memory_id=? AND r.revision=COALESCE(?,m.current_revision)",
                (str(memory_id), revision),
            ).fetchone()
            if size is not None and row_int(size, "payload_size") > self.max_read_bytes:
                raise SourceUnavailableError()
        if revision is None:
            row = connection.execute(
                "SELECT r.* FROM memory_revisions r JOIN memories m ON "
                "r.memory_id=m.memory_id AND r.revision=m.current_revision WHERE "
                "m.memory_id=?",
                (str(memory_id),),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM memory_revisions WHERE memory_id=? AND revision=?",
                (str(memory_id), revision),
            ).fetchone()
        if row is None:
            raise NotFoundError()
        memory = load_model(row, Memory)
        if memory.character_id != self.character_id:
            raise CorruptStoreError()
        if not allow_suppressed and self.memory_suppressed(
            connection, memory.memory_id, memory.revision
        ):
            raise AccessDeniedError()
        return memory

    def read_sources(
        self,
        connection: sqlite3.Connection,
        memory_id: CanonicalId,
        revision: int | None = None,
        options: RequestOptions | None = None,
    ) -> tuple[MemorySource, ...]:
        epoch = self.policy_epoch(connection)
        memory = self.read_memory(connection, memory_id, revision, options)
        sources: list[MemorySource] = []
        for assertion in memory.content.evidence:
            reference = assertion.evidence.source
            if not self.source_permitted(connection, reference, "evidence_read"):
                raise AccessDeniedError()
            check_deadline(self.database.clock, options.deadline_at if options else None)
            try:
                quote = self.resolver.validate(assertion.evidence)
            except SourceUnavailableError:
                sources.append(
                    MemorySource(evidence=assertion.evidence, availability="unavailable")
                )
            else:
                sources.append(
                    MemorySource(evidence=assertion.evidence, availability="available", quote=quote)
                )
        self.check_policy(connection, epoch)
        # Expiry can pass while a source is resolved without changing policy_epoch.
        if any(
            not self.source_permitted(connection, item.evidence.source, "evidence_read")
            for item in sources
        ):
            raise AccessDeniedError()
        return tuple(sources)

    def claim_keys(self, connection: sqlite3.Connection, draft: MemoryDraft) -> tuple[str, ...]:
        payload = draft.content.payload
        if not isinstance(payload, FactPayload):
            return (
                canonical_object_sha256(
                    {"kind": draft.content.kind, "payload": payload.model_dump(mode="json")}
                ),
            )
        variants = [payload.value.model_dump(mode="json")]
        if isinstance(payload.value, StringValue) and payload.predicate in {
            "hardware.primary_gpu",
            "hardware.owns_gpu",
        }:
            entities = connection.execute(
                "SELECT entity_id FROM entity_aliases WHERE alias=? LIMIT 2", (payload.value.value,)
            ).fetchall()
            for entity in entities if len(entities) == 1 else ():
                for row in connection.execute(
                    "SELECT alias FROM entity_aliases WHERE entity_id=? LIMIT 128",
                    (row_text(entity, "entity_id"),),
                ):
                    variants.append(
                        StringValue(value=row_text(row, "alias")).model_dump(mode="json")
                    )
        return tuple(
            sorted(
                {
                    canonical_object_sha256(
                        {
                            "subject": payload.subject.model_dump(mode="json"),
                            "predicate": payload.predicate,
                            "polarity": payload.polarity,
                            "value": value,
                        }
                    )
                    for value in variants
                }
            )
        )

    def dependencies(self, connection: sqlite3.Connection, memory: Memory) -> set[tuple[str, str]]:
        draft = MemoryDraft(content=memory.content)
        dependencies = {("claim", key) for key in self.claim_keys(connection, draft)}
        for assertion in memory.content.evidence:
            dependencies.add(("source", self.source_dependency_key(assertion.evidence.source)))
            dependencies.add(
                ("source", self.source_dependency_key(assertion.evidence.source, whole=True))
            )
        if memory.origin is not None:
            dependencies.add(
                (
                    "origin",
                    canonical_object_sha256(
                        {
                            "store": str(memory.origin.origin_source_store_id),
                            "memory": str(memory.origin.origin_memory_id),
                        }
                    ),
                )
            )
        return dependencies

    def validate_draft(self, connection: sqlite3.Connection, draft: MemoryDraft) -> None:
        validate_memory_write(draft, predicates=self.predicates)
        payload = draft.content.payload
        entities = [payload.subject] if isinstance(payload, FactPayload) else []
        if isinstance(payload, FactPayload) and isinstance(payload.value, EntityValue):
            entities.append(payload.value.value)
        from ember_memory_abi.memory import EpisodePayload

        if isinstance(payload, EpisodePayload):
            entities.extend(payload.participants)
            if payload.place is not None:
                entities.append(payload.place)
        for entity in entities:
            if (
                entity.store_id != self.character_id
                or connection.execute(
                    "SELECT 1 FROM entities WHERE entity_id=?", (str(entity.entity_id),)
                ).fetchone()
                is None
            ):
                raise AccessDeniedError()
        if not draft.content.evidence or not any(
            assertion.relation == "supports" for assertion in draft.content.evidence
        ):
            raise InvalidEvidenceError()
        for key in self.claim_keys(connection, draft):
            if connection.execute(
                "SELECT 1 FROM memory_dependencies d JOIN memories m ON m.memory_id=d.memory_id "
                "JOIN memory_revisions r ON r.memory_id=m.memory_id AND "
                "r.revision=m.current_revision "
                "WHERE d.target_kind='claim' AND d.target_key=? AND r.lifecycle='disabled' LIMIT 1",
                (key,),
            ).fetchone():
                raise AccessDeniedError()
            if connection.execute(
                "SELECT 1 FROM suppression_targets WHERE target_kind='claim' AND target_key=?",
                (key,),
            ).fetchone():
                raise AccessDeniedError()
        for assertion in draft.content.evidence:
            reference = assertion.evidence.source
            if not self.source_permitted(connection, reference, "evidence_read"):
                raise AccessDeniedError()
            self.resolver.validate(assertion.evidence)
            self.resolver.persist_evidence(
                assertion.evidence, local_connection=connection, local_store_id=self.character_id
            )

    def append_revision(self, connection: sqlite3.Connection, memory: Memory) -> None:
        payload = model_bytes(memory)
        temporal = memory.temporal
        connection.execute(
            "INSERT INTO memory_revisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(memory.memory_id),
                memory.revision,
                memory.content.kind,
                memory.lifecycle,
                memory.content.modality,
                memory.content.confidence,
                memory.content.importance,
                timestamp_to_unix_microseconds(temporal.recorded_at),
                timestamp_to_unix_microseconds(temporal.valid_from)
                if temporal.valid_from
                else None,
                timestamp_to_unix_microseconds(temporal.valid_until)
                if temporal.valid_until
                else None,
                payload,
                payload_hash(payload),
            ),
        )
        connection.execute(
            "UPDATE memories SET current_revision=? WHERE memory_id=?",
            (memory.revision, str(memory.memory_id)),
        )
        for assertion in memory.content.evidence:
            reference = assertion.evidence.source
            connection.execute(
                "INSERT OR IGNORE INTO memory_evidence VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(memory.memory_id),
                    memory.revision,
                    str(assertion.evidence.evidence_id),
                    str(reference.source_store_id),
                    reference.record_kind,
                    str(reference.record_id),
                    reference.record_revision,
                    str(reference.part_id) if reference.part_id else "",
                ),
            )
        for kind, key in self.dependencies(connection, memory):
            connection.execute(
                "INSERT INTO memory_dependencies VALUES(?,?,?,?)",
                (str(memory.memory_id), memory.revision, kind, key),
            )
        from .claim_projection import project

        project(connection, memory)
        connection.execute("UPDATE profile_policy SET selection_epoch=selection_epoch+1")
        connection.execute("DELETE FROM pins WHERE memory_id=?", (str(memory.memory_id),))
        connection.execute(
            "INSERT INTO "
            "index_outbox(kind,record_id,revision,policy_epoch,recorded_at_us) SELECT "
            "'memory_revision',?,?,policy_epoch,? FROM store_metadata",
            (
                str(memory.memory_id),
                memory.revision,
                timestamp_to_unix_microseconds(temporal.recorded_at),
            ),
        )

    def create_memory(
        self,
        connection: sqlite3.Connection,
        draft: MemoryDraft,
        stamp: str,
        *,
        origin: OriginMapping | None = None,
    ) -> Memory:
        memory = Memory(
            memory_id=uuid4(),
            character_id=self.character_id,
            revision=1,
            lifecycle="active",
            content=draft.content,
            temporal=TemporalMetadata(**draft.temporal.model_dump(), recorded_at=stamp),
            created_at=stamp,
            origin=origin,
        )
        connection.execute("INSERT INTO memories VALUES(?,1)", (str(memory.memory_id),))
        self.append_revision(connection, memory)
        return memory

    def revise_memory(
        self,
        old: Memory,
        stamp: str,
        *,
        draft: MemoryDraft | None = None,
        lifecycle: str | None = None,
        valid_until: str | None = None,
    ) -> Memory:
        content = draft.content if draft else old.content
        temporal = (
            draft.temporal.model_dump()
            if draft
            else old.temporal.model_dump(exclude={"recorded_at"})
        )
        if valid_until is not None:
            temporal["valid_until"] = valid_until
        return Memory(
            memory_id=old.memory_id,
            character_id=old.character_id,
            revision=old.revision + 1,
            lifecycle=lifecycle or old.lifecycle,
            content=content,
            temporal=TemporalMetadata(**temporal, recorded_at=stamp),
            created_at=old.created_at,
            origin=old.origin,
        )

    def forget_impact(
        self, connection: sqlite3.Connection, memory_id: CanonicalId, revision: int
    ) -> ForgetImpact:
        self.read_memory(connection, memory_id, revision, allow_suppressed=True)
        claims: set[str] = set()
        source_map: dict[tuple[object, ...], SourceRef] = {}
        target_keys: set[tuple[str, str]] = set()
        for row in connection.execute(
            "SELECT * FROM memory_revisions WHERE memory_id=? ORDER BY revision",
            (str(memory_id),),
        ).fetchall():
            historical = load_model(row, Memory)
            claims.update(self.claim_keys(connection, MemoryDraft(content=historical.content)))
            for assertion in historical.content.evidence:
                source_map[assertion.evidence.source.identity_key()] = assertion.evidence.source
            target_keys.update(self.dependencies(connection, historical))
        for row in connection.execute(
            "SELECT DISTINCT source_store_id,record_kind,record_id,record_revision,part_id "
            "FROM memory_evidence WHERE memory_id=?",
            (str(memory_id),),
        ):
            reference = SourceRef(
                source_store_id=UUID(row_text(row, "source_store_id")),
                record_kind=row_text(row, "record_kind"),
                record_id=UUID(row_text(row, "record_id")),
                record_revision=row_int(row, "record_revision"),
                part_id=UUID(row_text(row, "part_id")) if row["part_id"] else None,
            )
            source_map[reference.identity_key()] = reference
            target_keys.add(("source", self.source_dependency_key(reference)))
            target_keys.add(("source", self.source_dependency_key(reference, whole=True)))
        target_keys.update(
            (row_text(row, "target_kind"), row_text(row, "target_key"))
            for row in connection.execute(
                "SELECT DISTINCT target_kind,target_key FROM memory_dependencies WHERE memory_id=? "
                "AND target_kind IN ('claim','source','origin')",
                (str(memory_id),),
            )
        )
        sources = tuple(source_map.values())
        # A part-level suppression also affects every revision that used that original part.
        affected = {memory_id}
        for kind, key in target_keys:
            for row in connection.execute(
                "SELECT DISTINCT memory_id FROM memory_dependencies WHERE target_kind=? "
                "AND target_key=?",
                (kind, key),
            ):
                affected.add(UUID(row_text(row, "memory_id")))
        return ForgetImpact(
            memory_id=memory_id,
            expected_revision=revision,
            affected_memory_ids=tuple(sorted(affected, key=str)),
            source_parts=sources,
            claim_fingerprints=tuple(sorted(claims)),
        )
