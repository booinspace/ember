"""Durable task state and terminal delivery owned by the agent runtime."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from time import time
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, TypeAdapter

from .config import RuntimeModel

type TaskOperation = Literal["inspect", "summarize", "recall", "review"]
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


class TaskCancelled(Exception):
    """The operator cancelled a task before its next step."""


class TaskSubmission(RuntimeModel):
    """A trusted caller binds task identity and scope before persistence."""

    character_id: UUID
    session_id: UUID
    turn_id: UUID
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]
    goal: Annotated[str, Field(min_length=1, max_length=8192, repr=False)]
    operation: TaskOperation = "inspect"


class ConfirmationRequest(RuntimeModel):
    """Exact provider action to present to the authenticated operator."""

    task_id: UUID
    tool: Annotated[str, Field(min_length=1, max_length=64)]
    arguments: dict[str, JsonValue] = Field(repr=False)
    deadline_at: float = Field(gt=0, allow_inf_nan=False)


class Confirmation(RuntimeModel):
    """A durable decision bound to one action hash and character."""

    confirmation_id: UUID
    task_id: UUID
    tool: str
    action_sha256: str
    arguments: dict[str, JsonValue] = Field(repr=False)
    state: Literal["pending", "approved", "rejected", "expired"]
    deadline_at: float


class TaskHandle(RuntimeModel):
    """Stable handle for a task and its current state."""

    task_id: UUID
    state: TaskState
    cancellation_requested: bool = False


class TaskSnapshot(TaskHandle):
    """Character-scoped operational status and optional terminal result."""

    result: str | None = Field(default=None, repr=False)
    receipt: str | None = Field(default=None, repr=False, exclude=True)
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
                CREATE TABLE IF NOT EXISTS task_confirmation (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(id),
                    character_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    action_sha256 TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    deadline_at REAL NOT NULL,
                    state TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS pending_task_confirmation
                    ON task_confirmation(task_id) WHERE state='pending';
                CREATE TABLE IF NOT EXISTS task_attempt (
                    task_id TEXT NOT NULL REFERENCES task(id),
                    attempt INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    outcome TEXT NOT NULL,
                    PRIMARY KEY(task_id, attempt)
                );
                CREATE TABLE IF NOT EXISTS terminal_outbox (
                    task_id TEXT PRIMARY KEY REFERENCES task(id),
                    state TEXT NOT NULL,
                    delivery TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE INDEX IF NOT EXISTS pending_delivery ON terminal_outbox(delivery);
                CREATE TABLE IF NOT EXISTS task_effect (
                    confirmation_id TEXT PRIMARY KEY REFERENCES task_confirmation(id),
                    task_id TEXT NOT NULL REFERENCES task(id),
                    action_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    receipt_json TEXT
                );
                CREATE TABLE IF NOT EXISTS task_speech_delivery (
                    task_id TEXT PRIMARY KEY REFERENCES task(id),
                    session_id TEXT NOT NULL,
                    state TEXT NOT NULL
                );
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
        artifact: tuple[str, int] | None = None,
    ) -> None:
        """Atomically record a terminal state, optional artifact and notification."""
        if expected in TERMINAL_STATES or state not in TERMINAL_STATES:
            raise TaskConflict("invalid_terminal_transition")
        if result is not None and len(result.encode()) > 65536:
            raise TaskConflict("result_too_large")
        if receipt is not None and len(receipt.encode()) > 65536:
            raise TaskConflict("receipt_too_large")
        await asyncio.to_thread(self._finish, task_id, expected, state, result, receipt, artifact)

    def _finish(
        self,
        task_id: UUID,
        expected: TaskState,
        state: TerminalState,
        result: str | None,
        receipt: str | None,
        artifact: tuple[str, int] | None,
    ) -> None:
        identity = str(task_id)
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                """UPDATE task
                   SET state=CASE WHEN EXISTS (SELECT 1 FROM task_effect e WHERE e.task_id=task.id)
                                        AND (?<>'succeeded' OR cancellation_requested)
                                  THEN 'uncertain'
                                  WHEN cancellation_requested AND ?='succeeded'
                                  THEN 'cancelled' ELSE ? END,
                       result=CASE WHEN cancellation_requested THEN NULL ELSE ? END,
                       receipt=CASE WHEN cancellation_requested THEN NULL ELSE ? END
                   WHERE id=? AND state=? RETURNING state""",
                (state, state, state, result, receipt, identity, expected),
            ).fetchone()
            if row is None:
                raise TaskConflict("task_state_conflict")
            db.execute(
                """UPDATE task_attempt SET outcome=?, finished_at=?
                   WHERE task_id=? AND outcome='running'""",
                (row[0], time(), identity),
            )
            if artifact is not None and row[0] == "succeeded":
                db.execute(
                    "INSERT INTO task_artifact(task_id, sha256, size_bytes) VALUES (?, ?, ?)",
                    (identity, artifact[0], artifact[1]),
                )
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
            db.execute(
                """INSERT INTO task_attempt(task_id, attempt, started_at, outcome)
                   VALUES (?, COALESCE(
                       (SELECT MAX(attempt) FROM task_attempt WHERE task_id=?), 0
                   ) + 1, ?, 'running')""",
                (row[0], row[0], cutoff),
            )
            return UUID(row[0]), TaskSubmission.model_validate_json(row[1]), float(row[2])

    async def reconcile(self, character_id: UUID) -> None:
        """Mark interrupted work uncertain before the sole runtime starts dispatching."""
        await asyncio.to_thread(self._reconcile, character_id)

    def _reconcile(self, character_id: UUID) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE task_effect SET state='uncertain' WHERE state='dispatching'
                   AND task_id IN (SELECT id FROM task WHERE character_id=?)""",
                (str(character_id),),
            )
            db.execute(
                """UPDATE task_speech_delivery SET state='uncertain' WHERE state='dispatching'
                   AND task_id IN (SELECT id FROM task WHERE character_id=?)""",
                (str(character_id),),
            )
            db.execute(
                """INSERT INTO terminal_outbox(task_id, state)
                   SELECT id, 'uncertain' FROM task
                   WHERE character_id=? AND state IN ('starting', 'running',
                                   'waiting_for_inference', 'waiting_for_tool',
                                   'waiting_for_user')""",
                (str(character_id),),
            )
            db.execute(
                """UPDATE task_attempt SET outcome='uncertain', finished_at=?
                   WHERE outcome='running' AND task_id IN (
                       SELECT id FROM task WHERE character_id=? AND state IN (
                           'starting', 'running', 'waiting_for_inference', 'waiting_for_tool',
                           'waiting_for_user'
                       )
                   )""",
                (time(), str(character_id)),
            )
            db.execute(
                """UPDATE task SET state='uncertain'
                   WHERE character_id=? AND state IN ('starting', 'running',
                                   'waiting_for_inference', 'waiting_for_tool',
                                   'waiting_for_user')""",
                (str(character_id),),
            )

    async def status(self, task_id: UUID, character_id: UUID) -> TaskSnapshot | None:
        """Read only a task owned by the authenticated character."""
        return await asyncio.to_thread(self._status, task_id, character_id)

    def _status(self, task_id: UUID, character_id: UUID) -> TaskSnapshot | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                """SELECT state, cancellation_requested, result,
                          json_extract(request_json, '$.operation'), receipt FROM task
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
                    "receipt": row[4],
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
                          json_extract(task.request_json, '$.operation'), task.receipt,
                          task.cancellation_requested
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
                    "receipt": row[4],
                    "cancellation_requested": bool(row[5]),
                }
            )
            for row in rows
        )

    async def claim_speech(self, character_id: UUID, session_id: UUID) -> UUID | None:
        """Journal one eligible announcement before sending it to the speech transport."""
        return await asyncio.to_thread(self._claim_speech, character_id, session_id)

    def _claim_speech(self, character_id: UUID, session_id: UUID) -> UUID | None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT t.id FROM task t JOIN terminal_outbox o ON o.task_id=t.id
                   WHERE t.character_id=? AND t.session_id=? AND o.delivery='pending'
                     AND NOT EXISTS (SELECT 1 FROM task_speech_delivery s WHERE s.task_id=t.id)
                   ORDER BY o.rowid LIMIT 1""",
                (str(character_id), str(session_id)),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """INSERT INTO task_speech_delivery(task_id, session_id, state)
                   VALUES (?, ?, 'dispatching')""",
                (row[0], str(session_id)),
            )
            return UUID(row[0])

    async def finish_speech(self, task_id: UUID, session_id: UUID, *, completed: bool) -> None:
        """Record playout completion or uncertainty without changing UI delivery."""
        await asyncio.to_thread(self._finish_speech, task_id, session_id, completed)

    def _finish_speech(self, task_id: UUID, session_id: UUID, completed: bool) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            result = db.execute(
                """UPDATE task_speech_delivery SET state=?
                   WHERE task_id=? AND session_id=? AND state='dispatching'""",
                ("completed" if completed else "uncertain", str(task_id), str(session_id)),
            )
            if result.rowcount != 1:
                raise TaskConflict("speech_delivery_state_conflict")

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
                """UPDATE task SET state=? WHERE id=? AND state=?
                   RETURNING cancellation_requested""",
                (state, str(task_id), expected),
            ).fetchone()
            if row is None:
                raise TaskConflict("task_state_conflict")
            cancelled = bool(row[0])
        if cancelled:
            raise TaskCancelled()

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

    async def request_confirmation(
        self, character_id: UUID, request: ConfirmationRequest
    ) -> Confirmation:
        """Create one pending action for an active task, rejecting changed actions."""
        return await asyncio.to_thread(self._request_confirmation, character_id, request)

    def _request_confirmation(
        self, character_id: UUID, request: ConfirmationRequest
    ) -> Confirmation:
        data = json.dumps(
            {"tool": request.tool, "arguments": request.arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(data) > 16384 or request.deadline_at <= time():
            raise TaskConflict("invalid_confirmation")
        digest = hashlib.sha256(data).hexdigest()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute(
                """SELECT state, deadline_at, cancellation_requested FROM task
                   WHERE id=? AND character_id=?""",
                (str(request.task_id), str(character_id)),
            ).fetchone()
            if task is None or task[0] not in ("running", "waiting_for_tool", "waiting_for_user"):
                raise TaskConflict("confirmation_task_unavailable")
            db.execute(
                """UPDATE task_confirmation SET state='expired'
                   WHERE task_id=? AND state='pending' AND deadline_at<=?""",
                (str(request.task_id), time()),
            )
            if task[2]:
                raise TaskConflict("confirmation_task_unavailable")
            if request.deadline_at > task[1]:
                raise TaskConflict("confirmation_exceeds_task_deadline")
            row = db.execute(
                """SELECT id, tool, action_sha256, deadline_at, state FROM task_confirmation
                   WHERE task_id=? AND state='pending'""",
                (str(request.task_id),),
            ).fetchone()
            if row is not None:
                if (row[1], row[2], row[3]) != (request.tool, digest, request.deadline_at):
                    raise TaskConflict("confirmation_action_changed")
                identity = UUID(row[0])
            else:
                identity = uuid4()
                db.execute(
                    """INSERT INTO task_confirmation
                       (id, task_id, character_id, tool, action_sha256,
                        arguments_json, deadline_at, state)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                    (
                        str(identity),
                        str(request.task_id),
                        str(character_id),
                        request.tool,
                        digest,
                        json.dumps(
                            request.arguments,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        ),
                        request.deadline_at,
                    ),
                )
            return Confirmation(
                confirmation_id=identity,
                task_id=request.task_id,
                tool=request.tool,
                action_sha256=digest,
                arguments=request.arguments,
                state="pending",
                deadline_at=request.deadline_at,
            )

    async def decide_confirmation(
        self, character_id: UUID, confirmation_id: UUID, action_sha256: str, *, approve: bool
    ) -> Confirmation:
        """Bind an operator decision to the exact pending action."""
        return await asyncio.to_thread(
            self._decide_confirmation, character_id, confirmation_id, action_sha256, approve
        )

    def _decide_confirmation(
        self, character_id: UUID, confirmation_id: UUID, action_sha256: str, approve: bool
    ) -> Confirmation:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT confirmation.task_id, confirmation.tool, confirmation.action_sha256,
                          confirmation.deadline_at, confirmation.state, task.state,
                          task.cancellation_requested, confirmation.arguments_json
                   FROM task_confirmation AS confirmation
                   JOIN task ON task.id=confirmation.task_id
                   WHERE confirmation.id=? AND confirmation.character_id=?""",
                (str(confirmation_id), str(character_id)),
            ).fetchone()
            if row is None or row[2] != action_sha256:
                raise TaskConflict("confirmation_scope_or_action_mismatch")
            if row[5] in TERMINAL_STATES or row[6]:
                raise TaskConflict("confirmation_task_unavailable")
            decision = "approved" if approve else "rejected"
            if row[4] != "pending" and row[4] != decision:
                raise TaskConflict("confirmation_decided")
            if row[4] == "pending":
                decision = decision if row[3] > time() else "expired"
                db.execute(
                    "UPDATE task_confirmation SET state=? WHERE id=?",
                    (decision, str(confirmation_id)),
                )
            return Confirmation(
                confirmation_id=confirmation_id,
                task_id=UUID(row[0]),
                tool=row[1],
                action_sha256=row[2],
                arguments=TypeAdapter(dict[str, JsonValue]).validate_json(row[7]),
                deadline_at=row[3],
                state=decision,
            )

    async def begin_confirmed_effect(
        self, character_id: UUID, confirmation_id: UUID, action_sha256: str
    ) -> None:
        """Fence a confirmed effect before invocation; an existing fence forbids replay."""
        await asyncio.to_thread(
            self._begin_confirmed_effect, character_id, confirmation_id, action_sha256
        )

    def _begin_confirmed_effect(
        self, character_id: UUID, confirmation_id: UUID, action_sha256: str
    ) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT c.task_id FROM task_confirmation c JOIN task t ON t.id=c.task_id
                   WHERE c.id=? AND c.character_id=? AND c.action_sha256=?
                     AND c.state='approved' AND c.deadline_at>? AND t.state='running'
                     AND t.cancellation_requested=0""",
                (str(confirmation_id), str(character_id), action_sha256, time()),
            ).fetchone()
            if row is None:
                raise TaskConflict("effect_authority_invalid")
            try:
                db.execute(
                    """INSERT INTO task_effect(confirmation_id, task_id, action_sha256, state)
                       VALUES (?, ?, ?, 'dispatching')""",
                    (str(confirmation_id), row[0], action_sha256),
                )
            except sqlite3.IntegrityError as error:
                raise TaskConflict("effect_already_dispatched") from error

    async def finish_confirmed_effect(
        self, character_id: UUID, confirmation_id: UUID, *, receipt: dict[str, JsonValue] | None
    ) -> None:
        """Record a bounded receipt or preserve an uncertain outcome after dispatch."""
        encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 4096:
            raise TaskConflict("effect_receipt_too_large")
        await asyncio.to_thread(
            self._finish_confirmed_effect,
            character_id,
            confirmation_id,
            receipt is not None,
            encoded,
        )

    def _finish_confirmed_effect(
        self, character_id: UUID, confirmation_id: UUID, completed: bool, encoded: str
    ) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute(
                """UPDATE task_effect SET state=?, receipt_json=? WHERE confirmation_id=?
                   AND state='dispatching' AND task_id IN
                   (SELECT id FROM task WHERE character_id=?)""",
                (
                    "completed" if completed else "uncertain",
                    encoded,
                    str(confirmation_id),
                    str(character_id),
                ),
            )
            if row.rowcount != 1:
                raise TaskConflict("effect_state_conflict")

    async def pending_confirmations(self, character_id: UUID) -> tuple[Confirmation, ...]:
        """List active, unexpired confirmations for this authenticated character."""
        return await asyncio.to_thread(self._pending_confirmations, character_id)

    def _pending_confirmations(self, character_id: UUID) -> tuple[Confirmation, ...]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                """SELECT c.id, c.task_id, c.tool, c.action_sha256, c.arguments_json, c.deadline_at
                   FROM task_confirmation c JOIN task t ON t.id=c.task_id
                   WHERE c.character_id=? AND c.state='pending' AND c.deadline_at>?
                     AND t.state='waiting_for_user' AND t.cancellation_requested=0
                   ORDER BY c.rowid LIMIT 16""",
                (str(character_id), time()),
            ).fetchall()
        adapter = TypeAdapter(dict[str, JsonValue])
        return tuple(
            Confirmation(
                confirmation_id=UUID(row[0]),
                task_id=UUID(row[1]),
                tool=row[2],
                action_sha256=row[3],
                arguments=adapter.validate_json(row[4]),
                deadline_at=row[5],
                state="pending",
            )
            for row in rows
        )

    async def confirmation(self, character_id: UUID, confirmation_id: UUID) -> Confirmation:
        """Read a decision under its character scope, including expiry."""
        return await asyncio.to_thread(self._confirmation, character_id, confirmation_id)

    def _confirmation(self, character_id: UUID, confirmation_id: UUID) -> Confirmation:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                """UPDATE task_confirmation SET state='expired'
                   WHERE id=? AND character_id=? AND state='pending' AND deadline_at<=?""",
                (str(confirmation_id), str(character_id), time()),
            )
            row = db.execute(
                """SELECT task_id, tool, action_sha256, arguments_json, state, deadline_at
                   FROM task_confirmation WHERE id=? AND character_id=?""",
                (str(confirmation_id), str(character_id)),
            ).fetchone()
        if row is None:
            raise TaskConflict("confirmation_not_found")
        return Confirmation(
            confirmation_id=confirmation_id,
            task_id=UUID(row[0]),
            tool=row[1],
            action_sha256=row[2],
            arguments=TypeAdapter(dict[str, JsonValue]).validate_json(row[3]),
            state=row[4],
            deadline_at=row[5],
        )
