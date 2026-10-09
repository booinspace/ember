"""Provider-independent human memory authoring on existing archive and mutation contracts."""

import sqlite3
from hashlib import sha256
from typing import Annotated
from uuid import uuid4

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
    Confidence,
    ContractModel,
    canonical_object_sha256,
    timestamp_from_datetime,
)
from ember_memory_abi.evidence import (
    Evidence,
    EvidenceAssertion,
    SourceRef,
    make_evidence_span,
    source_text_sha256,
)
from ember_memory_abi.interfaces import MutationReceipt, RememberRequest
from ember_memory_abi.memory import (
    EntityRef,
    EpisodePayload,
    MemoryContent,
    MemoryDraft,
    MemoryPayload,
)
from ember_memory_abi.temporal import SourceTimestamp, TemporalBounds

from .engine import CanonicalEngine
from .models import MutationAuthority, SourceKey


class NewMemoryInput(ContractModel):
    """Original human observation and optional interpretation, independent of provider.

    Future summary/import adapters can supply an interpretation through the same
    authoring service after confirmation. Existing imported provenance instead uses
    RememberRequest directly, rather than laundering archive text as a new human event.
    """

    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    text: Annotated[str, Field(min_length=1, max_length=16000)] = Field(repr=False)
    payload: MemoryPayload | None = None
    modality: str = "FACTUAL_ASSERTION"
    temporal: TemporalBounds = Field(default_factory=TemporalBounds)
    importance: Confidence = 0.7


class MemoryAuthoringService:
    """Trusted human entry bound to one character; default authority is still read-only."""

    def __init__(
        self,
        engine: CanonicalEngine,
        subject: EntityRef,
        *,
        authority: MutationAuthority | None = None,
    ) -> None:
        self.engine = engine
        self.subject = subject
        self.store = engine.character(subject.store_id, authority=authority)
        self.writer = engine.archive(subject.store_id)

    def create(self, request: NewMemoryInput) -> MutationReceipt:
        """Preserve exact original evidence, then commit an idempotent manual interpretation.

        Source and belief have separate atomic receipts. An interrupted call may
        leave a reusable native observation; retry the identical key/input to resolve.
        This is not a model tool and never extracts/approves a claim automatically.
        """
        from ember_memory_abi.errors import AccessDeniedError

        if self.store.state.authority.mode != "confirmed_user":
            raise AccessDeniedError()
        request = NewMemoryInput.model_validate_json(request.model_dump_json())
        key = SourceKey(
            adapter="manual_memory", record_kind="document", external_id=request.idempotency_key
        )
        assigned = self.writer.prepare_keys((key,))
        identity = assigned[0].record_id
        existing = self.writer.latest_record("document", identity)
        stamp = timestamp_from_datetime(self.engine.clock.now())
        part_id = (
            existing.parts[0].part_id
            if isinstance(existing, Document) and existing.parts
            else uuid4()
        )
        reference = SourceRef(
            source_store_id=self.store.character_id,
            record_kind="document",
            record_id=identity,
            record_revision=1,
        )
        observed = (
            existing.created_time
            if isinstance(existing, Document)
            else SourceTimestamp(
                value=stamp, basis="human_entry_observed", units="UTC", precision="microsecond"
            )
        )
        document = Document(
            source=reference,
            recorded_at="1970-01-01T00:00:00.000000Z",
            title="Human-confirmed memory observation",
            created_time=observed,
            parts=(
                ContentPart(
                    part_id=part_id,
                    kind="text",
                    status="available",
                    text=request.text,
                    content_sha256=source_text_sha256(request.text),
                ),
            ),
        )
        digest = canonical_object_sha256(request)
        specification = ProcessingSpecRef(
            name="ember.manual_memory",
            version="0.1.0",
            specification_sha256=sha256(b"ember.manual-memory-v1").hexdigest(),
        )
        batch = NormalizedArchiveBatch(
            source_store_id=self.writer.source_store_id,
            source_namespace=self.writer.namespace,
            idempotency_key="manual-source:" + request.idempotency_key,
            processing_spec=specification,
            records=(document,),
            resume_cursor=ImportCursor(
                logical_inventory_sha256=digest,
                component_sha256=digest,
                next_record_index=1,
                adapter="manual_memory",
                adapter_version="0.1.0",
            ),
        )
        self.writer.write_batch(batch, keys=assigned)
        evidence = Evidence(
            evidence_id=uuid4(),
            source=reference.model_copy(update={"part_id": part_id}),
            span=make_evidence_span(request.text, 0, len(request.text)),
        )
        # Stable evidence identity is the source part itself, preserving mutation replay inputs.
        evidence = evidence.model_copy(update={"evidence_id": part_id})
        payload = request.payload or EpisodePayload(
            event=request.text, participants=(self.subject,)
        )
        draft = MemoryDraft(
            content=MemoryContent(
                kind=payload.type if payload.type != "opaque" else payload.original_kind,
                payload=payload,
                modality=request.modality,
                confidence=1.0,
                importance=request.importance,
                evidence=(
                    EvidenceAssertion(
                        evidence=evidence,
                        relation="supports",
                        modality=request.modality,
                        authority="confirmed_user_entry",
                        verdict="manual_confirmation",
                    ),
                ),
            ),
            temporal=request.temporal,
        )
        return self.store.remember(
            RememberRequest(idempotency_key=request.idempotency_key, draft=draft)
        )


class SubjectBinding(ContractModel):
    """Character-local human entity identity, independent of provider IDs and personality."""

    subject: EntityRef


def ensure_subject(engine: CanonicalEngine, character_id: CanonicalId) -> EntityRef:
    """Create/reopen a stable local user subject during blocking trusted composition."""
    from pydantic import TypeAdapter, ValidationError

    from ember_memory_abi.memory import Entity

    from .errors import CorruptStoreError
    from .ownership import validate_local_root

    # Narrow canonical identity once; source/model arguments cannot pick a new binding.
    adapter: TypeAdapter[CanonicalId] = TypeAdapter(CanonicalId)
    try:
        identity = adapter.validate_python(character_id)
    except ValidationError as error:
        raise CorruptStoreError() from error
    engine.character(identity)
    path = validate_local_root(
        engine.root / "characters" / str(identity) / "config" / "subject.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists():
            binding = SubjectBinding.model_validate_json(path.read_bytes())
            if binding.subject.store_id != identity:
                raise CorruptStoreError()
        else:
            binding = SubjectBinding(subject=EntityRef(store_id=identity, entity_id=uuid4()))
            with path.open("x", encoding="utf-8", newline="\n") as output:
                output.write(binding.model_dump_json())
        engine.put_entity(identity, Entity(reference=binding.subject, revision=1, name="User"))
        store = engine.character(identity)

        def bind(connection: sqlite3.Connection) -> None:
            with store.database.transaction(connection):
                connection.execute(
                    "UPDATE profile_policy SET subject_id=?,selection_epoch=selection_epoch+1 "
                    "WHERE subject_id IS NULL",
                    (str(binding.subject.entity_id),),
                )

        store.database.write(bind)
        policy = store.profile_settings()
        return EntityRef(
            store_id=identity, entity_id=policy.subject_id or binding.subject.entity_id
        )
    except (OSError, ValidationError) as error:
        raise CorruptStoreError() from error
