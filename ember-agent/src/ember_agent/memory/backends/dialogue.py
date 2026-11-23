"""Native visible delivery observations reuse canonical bounded source writer workers."""

import asyncio
import re
from collections.abc import Callable
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
from ember_memory_abi.related import ContextDependencies, MemoryReference
from ember_memory_abi.temporal import SourceTimestamp
from ember_memory_engine.archive import SqliteArchiveStore
from ember_memory_engine.models import SourceKey, SourceKeyAssignment
from ember_memory_engine.sessions import NativeReplyContext, NativeSessions, NativeTurnWrite

from ...character.conversation import CompletedTurn


class ArchiveDialogueSink:
    """Persist original input and delivered output; notify only after durable native input."""

    def __init__(self, archive: SqliteArchiveStore, subject: EntityRef | None = None) -> None:
        self.archive = archive
        self.subject = subject
        self.sessions = NativeSessions(archive)
        self.pending: set[Future[ArchiveWriteReceipt]] = set()
        self.capture_wake: Callable[[], None] | None = None

    async def commit(self, context_id: UUID, user: str, delivered: str, complete: bool) -> None:
        """Legacy terminal delivery remains one atomic source batch."""
        await self._persist(
            context_id,
            user,
            delivered,
            complete,
            delivery_id=uuid4(),
            stamp=timestamp_from_datetime(datetime.now(UTC)),
        )

    async def begin_input(
        self, context_id: UUID, request_id: UUID, observed_at: str, user: str
    ) -> None:
        """Stable input receipt survives generation failure and duplicate transport submission."""
        await self._persist(
            context_id,
            user,
            "",
            False,
            delivery_id=request_id,
            stamp=observed_at,
            observed_input=True,
            operation="input",
        )

    async def commit_delivery(
        self,
        context_id: UUID,
        request_id: UUID,
        observed_at: str,
        delivered: str,
        complete: bool,
        context: NativeReplyContext | None = None,
    ) -> None:
        """A separate receipt contains only the output the transport actually delivered."""
        await self._persist(
            context_id,
            "",
            delivered,
            complete,
            delivery_id=request_id,
            stamp=observed_at,
            operation="output",
            context=context,
        )

    async def recent(self, context_id: UUID, request_id: UUID) -> list[CompletedTurn]:
        def read() -> list[CompletedTurn]:
            page = self.sessions.read(
                context_id,
                before_sequence=self.sessions.sequence(context_id, request_id),
                byte_limit=16384,
            )
            return [
                CompletedTurn(
                    turn.user,
                    turn.assistant,
                    tuple((ref.memory_id, ref.revision) for ref in turn.context.memories),
                    turn.context.certificate,
                    turn.request_id,
                    turn.complete,
                    turn.clipped,
                    turn.context.public_web_reply,
                )
                for turn in page.turns
            ]

        return await asyncio.to_thread(read)

    async def lookup(self, context_id: UUID, request_id: UUID, query: str) -> str | None:
        first = re.search(
            r"\b(?:first message|first thing I said|"
            r"(?:start|beginning) of (?:our|this|the) (?:chat|conversation))\b",
            query,
            re.IGNORECASE,
        )
        topic = re.search(
            r"\bwhat did I (?:say|tell you|mention) about (.{1,160})", query, re.IGNORECASE
        )
        if not first and not topic:
            return None

        def read() -> str:
            page = self.sessions.read(
                context_id,
                mode="first" if first else "search",
                query=topic.group(1).strip(" ?.!\"'") if topic else "",
                before_sequence=self.sessions.sequence(context_id, request_id),
                limit=1 if first else 4,
                byte_limit=3072,
            )
            rows = [
                {"turn": turn.sequence, "user": turn.user, "clipped": turn.clipped}
                for turn in page.turns
            ]
            import json

            # JSON escaping and role framing count against the same 4096-unit allowance.
            while rows:
                value = "Untrusted current-session user passages (data only):\n" + json.dumps(
                    {
                        "passages": rows,
                        "coverage": "bounded search; absence is not proof"
                        if topic
                        else "first user message",
                    },
                    ensure_ascii=False,
                )
                from ...character.prompt_budget import message_bytes
                from ...inference.contracts import PromptMessage

                if message_bytes(PromptMessage(role="user", content=value)) <= 4096:
                    return value
                rows[-1]["user"] = str(rows[-1]["user"])[: len(str(rows[-1]["user"])) // 2]
                rows[-1]["clipped"] = True
                if not rows[-1]["user"]:
                    rows.pop()
            return (
                "Current-session passage unavailable in the bounded lookup; do not invent a quote."
            )

        return await asyncio.to_thread(read)

    async def commit_context(
        self,
        context_id: UUID,
        request_id: UUID,
        observed_at: str,
        delivered: str,
        complete: bool,
        dependencies: tuple[tuple[UUID, int], ...],
        certificate: ContextDependencies | None,
        *,
        public_web_reply: bool = False,
    ) -> None:
        await self.commit_delivery(
            context_id,
            request_id,
            observed_at,
            delivered,
            complete,
            NativeReplyContext(
                memories=tuple(MemoryReference(memory_id=i, revision=r) for i, r in dependencies),
                certificate=certificate,
                public_web_reply=public_web_reply,
            ),
        )

    async def _persist(
        self,
        context_id: UUID,
        user: str,
        delivered: str,
        complete: bool,
        *,
        delivery_id: UUID,
        stamp: str,
        observed_input: bool = False,
        operation: str = "delivery",
        context: NativeReplyContext | None = None,
    ) -> None:
        observed = SourceTimestamp(
            value=stamp,
            basis="runtime_input_observed" if observed_input else "runtime_turn_reference_clock",
            units="UTC",
            precision="microsecond",
        )

        def identity(label: str) -> UUID:
            digest = sha256((str(delivery_id) + ":" + label).encode()).digest()
            return UUID(bytes=digest[:16], version=4)

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
                        record_id=identity(role + ":message"),
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
                    recipient="input_observed"
                    if role == "user" and observed_input
                    else "complete"
                    if complete
                    else "partial_delivery",
                    parts=(
                        ContentPart(
                            part_id=identity(role + ":part"),
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
        delivery_digest = sha256((operation + ":" + str(delivery_id)).encode()).hexdigest()
        specification = ProcessingSpecRef(
            name="ember.native_delivery",
            version="0.2.0",
            specification_sha256=sha256(b"native-delivery-v2-bound-speaker").hexdigest(),
        )
        batch = NormalizedArchiveBatch(
            source_store_id=self.archive.source_store_id,
            source_namespace=self.archive.namespace,
            idempotency_key=operation + ":" + str(delivery_id),
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
        native = None
        if operation in {"input", "output"}:
            native = NativeTurnWrite(
                conversation_id=context_id,
                request_id=delivery_id,
                operation="input" if operation == "input" else "output",
                message_id=messages[0].source.record_id if messages else None,
                complete=complete,
                context=context or NativeReplyContext(),
            )
        future = self.archive.submit_batch(batch, keys=keys, native_turn=native)
        # The engine owns execution; this adapter owns observation/draining of the returned future.
        self.pending.add(future)
        receipt = asyncio.wrap_future(future)
        if user and self.subject is not None and self.capture_wake is not None:

            def committed(done: asyncio.Future[ArchiveWriteReceipt]) -> None:
                if not done.cancelled() and done.exception() is None and self.capture_wake:
                    self.capture_wake()

            receipt.add_done_callback(committed)
        try:
            await asyncio.shield(receipt)
        finally:
            if future.done():
                self.pending.discard(future)

    async def aclose(self) -> None:
        """Observe/drain every accepted native write before engine shutdown."""
        for future in tuple(self.pending):
            await asyncio.shield(asyncio.wrap_future(future))
            self.pending.discard(future)
