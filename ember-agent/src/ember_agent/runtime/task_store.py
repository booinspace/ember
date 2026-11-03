"""Durable task state and terminal delivery owned by the agent runtime."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from time import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field

from .config import RuntimeModel

type TaskOperation = Literal["inspect", "summarize", "recall"]
type TerminalState = Literal["succeeded", "failed", "cancelled", "timed_out", "uncertain"]
type TaskState = Literal[
    "queued",
    "starting",
    "running",
    "waiting_for_inference",
    "waiting_for_tool",
    "waiting_for_user",
    "succeeded",
    "failed",
    "cancelled",
    "timed_out",
    "uncertain",
]

TERMINAL_STATES: frozenset[str] = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "uncertain"}
)


class TaskConflict(RuntimeError):
    """The requested task operation conflicts with stored identity or state."""


class TaskSubmission(RuntimeModel):
    """A trusted caller binds task identity and scope before persistence."""

    character_id: UUID
    session_id: UUID
    turn_id: UUID
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]
    goal: Annotated[str, Field(min_length=1, max_length=8192, repr=False)]
    operation: TaskOperation = "inspect"


class TaskHandle(RuntimeModel):
    """Stable handle for a task and its current state."""

    task_id: UUID
    state: TaskState
    cancellation_requested: bool = False


class TaskSnapshot(TaskHandle):
    """Character-scoped operational status and optional terminal result."""

    result: str | None = Field(default=None, repr=False)
    operation: TaskOperation = "inspect"


class TaskStore:
    """Keep idempotent submissions and terminal notifications transactionally consistent."""

    def __init__(self, runtime_root: Path) -> None:
        self.path = runtime_root / "runtime.db"

    async def initialize(self) -> None:
        """Create the operational schema outside the realtime event loop."""
        await asyncio.to_thread(self._initialize)

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS task (
                    id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    request_key TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    deadline_at REAL NOT NULL,
                    state TEXT NOT NULL,
                    result TEXT,
                    receipt TEXT,
                    cancellation_requested INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(character_id, request_key)
                );
                CREATE INDEX IF NOT EXISTS task_dispatch ON task(character_id, state);
                CREATE TABLE IF NOT EXISTS terminal_outbox (
                    task_id TEXT PRIMARY KEY REFERENCES task(id),
                    state TEXT NOT NULL,
                    delivery TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE INDEX IF NOT EXISTS pending_delivery ON terminal_outbox(delivery);
                CREATE TABLE IF NOT EXISTS task_artifact (
                    task_id TEXT PRIMARY KEY REFERENCES task(id),
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL
                );
                """)

    async def submit(self, request: TaskSubmission) -> TaskHandle:
        """Return the same durable handle for an identical scoped retry."""
        return await asyncio.to_thread(self._submit, request)

    def _submit(self, request: TaskSubmission) -> TaskHandle:
        request_json = request.model_dump_json()
        character = str(request.character_id)
        key = (character, request.idempotency_key)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id, request_json, state, cancellation_requested FROM task
                   WHERE character_id=? AND request_key=?""",
                key,
            ).fetchone()
            if row is not None:
                original = TaskSubmission.model_validate_json(row[1])
                if (original.goal, original.operation) != (request.goal, request.operation):
                    raise TaskConflict("idempotency_key_reused")
                return TaskHandle.model_validate(
                    {
                        "task_id": UUID(row[0]),
                        "state": row[2],
                        "cancellation_requested": bool(row[3]),
                    }
                )
            count = db.execute(
                "SELECT count(*) FROM task WHERE character_id=? AND state='queued'", (character,)
            ).fetchone()
            if count[0] >= 32:
                raise TaskConflict("task_queue_full")
            identity = uuid4()
            db.execute(
                """INSERT INTO task(
                       id, character_id, session_id, request_key, request_json, deadline_at, state
                   ) VALUES (?, ?, ?, ?, ?, ?, 'queued')""",
                (
                    str(identity),
                    character,
                    str(request.session_id),
                    key[1],
                    request_json,
                    time() + 1800,
                ),
            )
            return TaskHandle(task_id=identity, state="queued")

    async def finish(
        self,
        task_id: UUID,
        expected: TaskState,
        state: TerminalState,
        *,
        result: str | None = None,
        receipt: str | None = None,
    ) -> None:
        """Atomically record a terminal state and exactly one notification record."""
        if expected in TERMINAL_STATES or state not in TERMINAL_STATES:
            raise TaskConflict("invalid_terminal_transition")
        if result is not None and len(result.encode()) > 65536:
            raise TaskConflict("result_too_large")
        if receipt is not None and len(receipt.encode()) > 4096:
            raise TaskConflict("receipt_too_large")
        await asyncio.to_thread(self._finish, task_id, expected, state, result, receipt)

    def _finish(
        self,
        task_id: UUID,
        expected: TaskState,
        state: TerminalState,
        result: str | None,
        receipt: str | None,
    ) -> None:
        identity = str(task_id)
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                """UPDATE task
                   SET state=CASE WHEN cancellation_requested AND ?='succeeded'
                                  THEN 'cancelled' ELSE ? END,
                       result=CASE WHEN cancellation_requested THEN NULL ELSE ? END,
                       receipt=CASE WHEN cancellation_requested THEN NULL ELSE ? END
                   WHERE id=? AND state=? RETURNING state""",
                (state, state, result, receipt, identity, expected),
            ).fetchone()
            if row is None:
                raise TaskConflict("task_state_conflict")
            db.execute(
                "INSERT INTO terminal_outbox(task_id, state) VALUES (?, ?)", (identity, row[0])
            )

    async def claim(self, character_id: UUID) -> tuple[UUID, TaskSubmission, float] | None:
        """Claim the oldest queued task with an atomic compare-and-set."""
        return await asyncio.to_thread(self._claim, character_id)

    def _claim(self, character_id: UUID) -> tuple[UUID, TaskSubmission, float] | None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            cutoff = time()
            expired = (str(character_id), cutoff)
            db.execute(
                """INSERT INTO terminal_outbox(task_id, state)
                   SELECT id, 'timed_out' FROM task WHERE character_id=? AND state='queued'
                   AND deadline_at<=?""",
                expired,
            )
            db.execute(
                """UPDATE task SET state='timed_out' WHERE character_id=? AND state='queued'
                   AND deadline_at<=?""",
                expired,
            )
            row = db.execute(
                """UPDATE task SET state='running'
                   WHERE id=(SELECT id FROM task WHERE character_id=? AND state='queued'
                             ORDER BY rowid LIMIT 1)
                   RETURNING id, request_json, deadline_at""",
                (str(character_id),),
            ).fetchone()
            if row is None:
                return None
            return UUID(row[0]), TaskSubmission.model_validate_json(row[1]), float(row[2])

    async def reconcile(self, character_id: UUID) -> None:
        """Mark interrupted work uncertain before the sole runtime starts dispatching."""
        await asyncio.to_thread(self._reconcile, character_id)

    def _reconcile(self, character_id: UUID) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO terminal_outbox(task_id, state)
                   SELECT id, 'uncertain' FROM task
                   WHERE character_id=? AND state IN ('starting', 'running',
                                   'waiting_for_inference', 'waiting_for_tool')""",
                (str(character_id),),
            )
            db.execute(
                """UPDATE task SET state='uncertain'
                   WHERE character_id=? AND state IN ('starting', 'running',
                                   'waiting_for_inference', 'waiting_for_tool')""",
                (str(character_id),),
            )

    async def status(self, task_id: UUID, character_id: UUID) -> TaskSnapshot | None:
        """Read only a task owned by the authenticated character."""
        return await asyncio.to_thread(self._status, task_id, character_id)

    def _status(self, task_id: UUID, character_id: UUID) -> TaskSnapshot | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                """SELECT state, cancellation_requested, result,
                          json_extract(request_json, '$.operation') FROM task
                   WHERE id=? AND character_id=?""",
                (str(task_id), str(character_id)),
            ).fetchone()
            if row is None:
                return None
            return TaskSnapshot.model_validate(
                {
                    "task_id": task_id,
                    "state": row[0],
                    "cancellation_requested": bool(row[1]),
                    "result": row[2],
                    "operation": row[3] or "inspect",
                }
            )

    async def cancel(self, task_id: UUID, character_id: UUID) -> None:
        """Cancel queued work and record a request to stop active work."""
        await asyncio.to_thread(self._cancel, task_id, character_id)

    def _cancel(self, task_id: UUID, character_id: UUID) -> None:
        identity = str(task_id)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM task WHERE id=? AND character_id=?",
                (identity, str(character_id)),
            ).fetchone()
            if row is None:
                raise TaskConflict("task_not_found")
            if row[0] in TERMINAL_STATES:
                return
            if row[0] == "queued":
                db.execute("UPDATE task SET state='cancelled' WHERE id=?", (identity,))
                db.execute(
                    "INSERT INTO terminal_outbox(task_id, state) VALUES (?, 'cancelled')",
                    (identity,),
                )
            else:
                db.execute("UPDATE task SET cancellation_requested=1 WHERE id=?", (identity,))

    async def notifications(self, character_id: UUID) -> tuple[TaskSnapshot, ...]:
        """Return a bounded, stable batch until the operator acknowledges it."""
        return await asyncio.to_thread(self._notifications, character_id)

    def _notifications(self, character_id: UUID) -> tuple[TaskSnapshot, ...]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                """SELECT task.id, task.state, task.result,
                          json_extract(task.request_json, '$.operation')
                   FROM terminal_outbox AS outbox JOIN task ON task.id=outbox.task_id
                   WHERE task.character_id=? AND outbox.delivery='pending'
                   ORDER BY outbox.rowid LIMIT 16""",
                (str(character_id),),
            ).fetchall()
        return tuple(
            TaskSnapshot.model_validate(
                {
                    "task_id": UUID(row[0]),
                    "state": row[1],
                    "result": row[2],
                    "operation": row[3] or "inspect",
                }
            )
            for row in rows
        )

    async def acknowledge(self, task_id: UUID, character_id: UUID) -> None:
        """Deduplicate delivery of a terminal result within its character scope."""
        await asyncio.to_thread(self._acknowledge, task_id, character_id)

    def _acknowledge(self, task_id: UUID, character_id: UUID) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                """UPDATE terminal_outbox SET delivery='acknowledged'
                   WHERE task_id=(SELECT id FROM task WHERE id=? AND character_id=?)""",
                (str(task_id), str(character_id)),
            )
            if row.rowcount != 1:
                raise TaskConflict("task_notification_not_found")

    async def progress(self, task_id: UUID, expected: TaskState, state: TaskState) -> None:
        """Coalesce progress to the latest provider state, preserving terminal events."""
        active = {"running", "waiting_for_inference", "waiting_for_tool", "waiting_for_user"}
        if expected not in active or state not in active:
            raise TaskConflict("invalid_progress_transition")
        await asyncio.to_thread(self._progress, task_id, expected, state)

    def _progress(self, task_id: UUID, expected: TaskState, state: TaskState) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                "UPDATE task SET state=? WHERE id=? AND state=?",
                (state, str(task_id), expected),
            )
            if row.rowcount != 1:
                raise TaskConflict("task_state_conflict")

    async def authorize_artifact(
        self, task_id: UUID, character_id: UUID, sha256: str, size_bytes: int
    ) -> None:
        """Persist the operator's artifact publication receipt."""
        await asyncio.to_thread(self._authorize_artifact, task_id, character_id, sha256, size_bytes)

    def _authorize_artifact(
        self, task_id: UUID, character_id: UUID, sha256: str, size_bytes: int
    ) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                """INSERT INTO task_artifact(task_id, sha256, size_bytes)
                   SELECT id, ?, ? FROM task WHERE id=? AND character_id=? AND state='succeeded'
                   ON CONFLICT(task_id) DO UPDATE SET sha256=excluded.sha256,
                                                      size_bytes=excluded.size_bytes""",
                (sha256, size_bytes, str(task_id), str(character_id)),
            )
            if row.rowcount != 1:
                raise TaskConflict("task_artifact_unavailable")

    async def artifact_identity(self, task_id: UUID, character_id: UUID) -> tuple[str, int] | None:
        """Read a published artifact identity under the requesting character."""
        return await asyncio.to_thread(self._artifact_identity, task_id, character_id)

    def _artifact_identity(self, task_id: UUID, character_id: UUID) -> tuple[str, int] | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                """SELECT artifact.sha256, artifact.size_bytes
                   FROM task_artifact AS artifact JOIN task ON task.id=artifact.task_id
                   WHERE task.id=? AND task.character_id=?""",
                (str(task_id), str(character_id)),
            ).fetchone()
        return (str(row[0]), int(row[1])) if row is not None else None
