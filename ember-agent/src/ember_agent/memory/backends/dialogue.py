"""Native visible delivery observations reuse canonical bounded source writer workers."""

import asyncio
from concurrent.futures import Future
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from ember_memory_abi.archive import (
    ActorAttribution,
    ArchiveWriteReceipt,
    BranchSelection,
    ContentPart,
    Conversation,
    ImportCursor,
    Message,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.evidence import SourceRef, source_text_sha256
from ember_memory_abi.memory import EntityRef
from ember_memory_abi.temporal import SourceTimestamp
from ember_memory_engine.archive import SqliteArchiveStore
from ember_memory_engine.models import SourceKey, SourceKeyAssignment


class ArchiveDialogueSink:
    """Persist original input and delivered visible output; no automatic memory extraction."""

    def __init__(self, archive: SqliteArchiveStore, subject: EntityRef | None = None) -> None:
        self.archive = archive
        self.subject = subject
        self.pending: set[Future[ArchiveWriteReceipt]] = set()

    async def commit(self, context_id: UUID, user: str, delivered: str, complete: bool) -> None:
        """Commit an immutable turn snapshot on the archive owner's bounded writer."""
        stamp = timestamp_from_datetime(datetime.now(UTC))
        observed = SourceTimestamp(
            value=stamp, basis="runtime_delivery_observed", units="UTC", precision="microsecond"
        )
        delivery_id = uuid4()
        conversation_ref = SourceRef(
            source_store_id=self.archive.source_store_id,
            record_kind="conversation",
            record_id=context_id,
            record_revision=1,
        )
        # Archive replay ignores recorded_at and retains the first persisted timestamp.
        # All other conversation fields must stay stable for this session identity.
        conversation = Conversation(
            source=conversation_ref,
            recorded_at=stamp,
            selection=BranchSelection(
                status="unavailable", reason_codes=("native_session_ordered_delivery",)
            ),
        )
        messages: list[Message] = []
        for role, text in (("user", user), ("assistant", delivered)):
            if not text:
                continue
            messages.append(
                Message(
                    source=SourceRef(
                        source_store_id=self.archive.source_store_id,
                        record_kind="message",
                        record_id=uuid4(),
                        record_revision=1,
                    ),
                    recorded_at=stamp,
                    conversation=conversation_ref,
                    actor=ActorAttribution(
                        role=role,
                        actor_id=self.subject.entity_id
                        if role == "user" and self.subject
                        else None,
                    ),
                    channel="final",
                    created_time=observed,
                    recipient="complete" if complete else "partial_delivery",
                    parts=(
                        ContentPart(
                            part_id=uuid4(),
                            kind="text",
                            status="available",
                            text=text,
                            content_sha256=source_text_sha256(text),
                        ),
                    ),
                )
            )
        conversation_digest = sha256(str(context_id).encode()).hexdigest()
        # Each atomic delivery is a component, including shorter interrupted turns.
        # Sharing its cursor with another delivery would make record counts regress.
        delivery_digest = sha256(str(delivery_id).encode()).hexdigest()
        specification = ProcessingSpecRef(
            name="ember.native_delivery",
            version="0.2.0",
            specification_sha256=sha256(b"native-delivery-v2-bound-speaker").hexdigest(),
        )
        batch = NormalizedArchiveBatch(
            source_store_id=self.archive.source_store_id,
            source_namespace=self.archive.namespace,
            idempotency_key="delivery:" + str(delivery_id),
            processing_spec=specification,
            records=(conversation, *messages),
            resume_cursor=ImportCursor(
                logical_inventory_sha256=conversation_digest,
                component_sha256=delivery_digest,
                next_record_index=len(messages) + 1,
                adapter="native_delivery",
                adapter_version="0.2.0",
            ),
        )
        keys = (
            tuple(
                SourceKeyAssignment(
                    key=SourceKey(
                        adapter="native_delivery",
                        record_kind="message",
                        external_id="v2:"
                        + message.actor.role
                        + ":"
                        + str(message.source.record_id),
                    ),
                    record_id=message.source.record_id,
                )
                for message in messages
            )
            if self.subject
            else ()
        )
        future = self.archive.submit_batch(batch, keys=keys)
        # The engine owns execution; this adapter owns observation/draining of the returned future.
        self.pending.add(future)
        try:
            await asyncio.shield(asyncio.wrap_future(future))
        finally:
            if future.done():
                self.pending.discard(future)

    async def aclose(self) -> None:
        """Observe/drain every accepted native write before engine shutdown."""
        for future in tuple(self.pending):
            await asyncio.shield(asyncio.wrap_future(future))
            self.pending.discard(future)
