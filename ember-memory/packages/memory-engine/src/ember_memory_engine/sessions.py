"""Character-owned native conversation references; message text stays in the archive."""

import sqlite3
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import Field

from ember_memory_abi.archive import Message
from ember_memory_abi.common import ContractModel
from ember_memory_abi.errors import NotFoundError, UnsupportedWriteError
from ember_memory_abi.related import ContextDependencies, MemoryReference

from .database import payload_hash, row_int, row_text
from .errors import CorruptStoreError

if TYPE_CHECKING:
    from .archive import SqliteArchiveStore


class NativeReplyContext(ContractModel):
    """Source dependencies and replay policy for persisted delivered assistant text."""

    memories: tuple[MemoryReference, ...] = Field(default=(), max_length=256)
    certificate: ContextDependencies | None = None
    # Preserve old native batch hashes for retries when the marker is absent.
    public_web_reply: bool = Field(default=False, exclude_if=lambda value: not value)


class NativeTurnWrite(ContractModel):
    """Trusted composition metadata committed with its native input or output batch."""

    conversation_id: UUID
    request_id: UUID
    operation: Literal["input", "output"]
    message_id: UUID | None = None
    complete: bool = False
    context: NativeReplyContext = Field(default_factory=NativeReplyContext)


class NativeSessionInfo(ContractModel):
    """Content-free character-local conversation identity and durable turn count."""

    conversation_id: UUID
    turns: int = Field(ge=0)


class NativeSessionTurn(ContractModel):
    """Exact or explicitly clipped native user text and verified delivered assistant prefix."""

    request_id: UUID
    sequence: int = Field(ge=1)
    user: str = Field(repr=False)
    assistant: str = Field(default="", repr=False)
    complete: bool = False
    clipped: bool = False
    context: NativeReplyContext = Field(default_factory=NativeReplyContext)


class NativeSessionPage(ContractModel):
    """A bounded selection with coverage, never an assertion of a complete transcript."""

    turns: tuple[NativeSessionTurn, ...]
    partial: bool = False
    scanned: int = Field(ge=0)


def write_native_turn(connection: sqlite3.Connection, value: NativeTurnWrite) -> None:
    """Bind native references atomically under the archive writer's existing transaction."""
    conversation, request = str(value.conversation_id), str(value.request_id)
    if value.operation == "input":
        connection.execute(
            "INSERT OR IGNORE INTO native_sessions(conversation_id) VALUES(?)", (conversation,)
        )
    row = connection.execute(
        "SELECT 1 FROM native_sessions WHERE conversation_id=?", (conversation,)
    ).fetchone()
    if row is None:
        raise NotFoundError()
    if value.operation == "input":
        if value.message_id is None:
            raise UnsupportedWriteError()
        sequence = connection.execute(
            "UPDATE native_sessions SET turn_count=turn_count+1 WHERE conversation_id=? "
            "RETURNING turn_count",
            (conversation,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO native_turns(conversation_id,request_id,sequence,user_record_id) "
            "VALUES(?,?,?,?)",
            (conversation, request, sequence, str(value.message_id)),
        )
    else:
        context = value.context.model_dump_json()
        cursor = connection.execute(
            "UPDATE native_turns SET assistant_record_id=?,complete=?,reply_context=?,"
            "reply_context_sha256=? WHERE conversation_id=? AND request_id=?",
            (
                str(value.message_id) if value.message_id else None,
                int(value.complete),
                context,
                payload_hash(context),
                conversation,
                request,
            ),
        )
        if cursor.rowcount != 1:
            raise NotFoundError()


class NativeSessions:
    """Engine-private native transcript operations bound to one owned character store."""

    def __init__(self, archive: "SqliteArchiveStore") -> None:
        self.archive = archive
        kind = archive.database.read(
            lambda db: db.execute("SELECT kind FROM store_metadata").fetchone()[0]
        )
        if kind != "character":
            raise UnsupportedWriteError()

    def inspect(self, conversation_id: UUID) -> NativeSessionInfo:
        """Require a native session in this character; arbitrary archive IDs grant no access."""

        def read(db: sqlite3.Connection) -> NativeSessionInfo:
            row = db.execute(
                "SELECT turn_count FROM native_sessions WHERE conversation_id=?",
                (str(conversation_id),),
            ).fetchone()
            if row is None:
                raise NotFoundError()
            count: object = row[0]
            if not isinstance(count, int):
                raise CorruptStoreError()
            return NativeSessionInfo(conversation_id=conversation_id, turns=count)

        return self.archive.database.read(read)

    def sequence(self, conversation_id: UUID, request_id: UUID) -> int:
        def read(db: sqlite3.Connection) -> int:
            row = db.execute(
                "SELECT sequence FROM native_turns WHERE conversation_id=? AND request_id=?",
                (str(conversation_id), str(request_id)),
            ).fetchone()
            if row is None:
                raise NotFoundError()
            return row_int(row, "sequence")

        return self.archive.database.read(read)

    def read(
        self,
        conversation_id: UUID,
        *,
        mode: Literal["recent", "first", "search"] = "recent",
        query: str = "",
        before_sequence: int | None = None,
        limit: int = 64,
        byte_limit: int = 16384,
    ) -> NativeSessionPage:
        """Read at most 256 reference rows and a bounded amount of text from one session.

        Topic search inspects at most the most recent 256 user messages. Coverage is
        explicit when that window or the byte allowance prevents a complete answer.
        """
        if not 1 <= limit <= 64 or not 256 <= byte_limit <= 65536 or len(query) > 1600:
            raise ValueError("Native conversation read bound exceeded.")
        self.inspect(conversation_id)
        scan_limit = 256 if mode == "search" else limit + 1
        rows = self.archive.database.read(
            lambda db: db.execute(
                "SELECT * FROM native_turns WHERE conversation_id=? "
                "AND sequence < ? ORDER BY sequence "
                + ("ASC" if mode == "first" else "DESC")
                + " LIMIT ?",
                (str(conversation_id), before_sequence or 2**63 - 1, scan_limit),
            ).fetchall()
        )
        result: list[NativeSessionTurn] = []
        remaining = byte_limit
        partial = len(rows) == scan_limit
        scan_bytes = 0
        scanned = 0
        for row in rows[: scan_limit if mode == "search" else limit]:
            user = self._message(row_text(row, "user_record_id"), conversation_id, "user")
            scanned += 1
            scan_bytes += len(user.encode())
            if mode == "search":
                if scan_bytes > 65536:
                    partial = True
                    break
                if query.casefold() not in user.casefold():
                    continue
            assistant = ""
            context = NativeReplyContext()
            if mode == "recent" and row["assistant_record_id"] is not None:
                assistant = self._message(
                    row_text(row, "assistant_record_id"), conversation_id, "assistant"
                )
                raw = row_text(row, "reply_context")
                if payload_hash(raw) != row_text(row, "reply_context_sha256"):
                    raise CorruptStoreError()
                context = NativeReplyContext.model_validate_json(raw)
            # Always retain user observations before spending the selected window on output.
            clipped_user = user.encode()[: min(remaining, 4096)].decode("utf-8", errors="ignore")
            remaining -= len(clipped_user.encode())
            clipped_assistant = assistant.encode()[: min(remaining, 2048)].decode(
                "utf-8", errors="ignore"
            )
            remaining -= len(clipped_assistant.encode())
            clipped = clipped_user != user or clipped_assistant != assistant
            partial |= clipped
            if not clipped_user:
                partial = True
                break
            result.append(
                NativeSessionTurn(
                    request_id=UUID(row_text(row, "request_id")),
                    sequence=row_int(row, "sequence"),
                    user=clipped_user,
                    assistant=clipped_assistant,
                    complete=bool(row["complete"]),
                    clipped=clipped,
                    context=context,
                )
            )
            if len(result) >= limit or remaining <= 0:
                partial |= len(rows) > len(result)
                break
        if mode != "first":
            result.reverse()
        return NativeSessionPage(turns=tuple(result), partial=partial, scanned=scanned)

    def _message(self, identity: str, conversation: UUID, role: str) -> str:
        from ember_memory_abi.evidence import SourceRef

        record = self.archive.get_record(
            SourceRef(
                source_store_id=self.archive.source_store_id,
                record_kind="message",
                record_id=UUID(identity),
                record_revision=1,
            )
        )
        if (
            not isinstance(record, Message)
            or record.conversation.record_id != conversation
            or record.actor.role != role
            or record.channel != "final"
        ):
            raise CorruptStoreError()
        return "".join(
            part.text or ""
            for part in record.parts
            if part.kind == "text" and part.status == "available"
        )
