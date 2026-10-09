"""Durable native capture checkpoints reuse extraction and conservative acceptance."""

import asyncio
import sqlite3
from concurrent.futures import Executor
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter

from ember_memory_abi.archive import Message, NormalizedArchiveBatch
from ember_memory_abi.common import CanonicalId, ContractModel
from ember_memory_abi.errors import AccessDeniedError, RevisionConflictError
from ember_memory_abi.evidence import SourceRef

from .consolidation import NativeAcceptanceRequest, NativeAcceptanceService
from .database import row_int, row_text
from .engine import CanonicalEngine
from .models import MutationAuthority, SourceKeyAssignment
from .processing import ProcessingService
from .processing_contracts import ExtractionModel, JobRequest, native_specification

_REFERENCE_ADAPTER = TypeAdapter(tuple[SourceRef, ...])


class CaptureSettings(ContractModel):
    """Capture is independent of origin recall and import review authority."""

    enabled: bool = True
    revision: Annotated[int, Field(ge=0)] = 0


class CaptureCheckpoint(ContractModel):
    """Small restart-safe source snapshot; no prompt or hidden reasoning is retained."""

    delivery_key: str
    sources: Annotated[tuple[SourceRef, ...], Field(max_length=64)]
    status: Literal["queued", "paused", "processing", "complete", "failed"]
    job_id: CanonicalId | None = None
    error_code: str | None = None


def enqueue_delivery(
    connection: sqlite3.Connection,
    batch: NormalizedArchiveBatch,
    keys: tuple[SourceKeyAssignment, ...],
) -> None:
    """Run inside the archive transaction; only bound native user messages are queued."""
    if connection.execute("SELECT kind FROM store_metadata").fetchone()[0] != "character":
        return
    trusted = {
        key.record_id
        for key in keys
        if key.key.adapter == "native_delivery" and key.key.external_id.startswith("v2:user:")
    }
    references = tuple(
        record.source
        for record in batch.records
        if isinstance(record, Message)
        and record.source.record_id in trusted
        and record.actor.role == "user"
        and record.actor.actor_id is not None
        and record.channel == "final"
    )
    if not references:
        return
    enabled = connection.execute("SELECT enabled FROM capture_policy").fetchone()[0]
    pending = connection.execute(
        "SELECT COUNT(*) FROM native_capture WHERE status IN ('queued','processing')"
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO native_capture VALUES(?,?,?,NULL,NULL)",
        (
            batch.idempotency_key,
            _REFERENCE_ADAPTER.dump_json(references).decode(),
            "queued" if enabled and pending < 1000 else "paused",
        ),
    )


class CaptureService:
    """No scheduler or writer owner of its own; the runtime controls when P3 may run."""

    def __init__(self, engine: CanonicalEngine, character: CanonicalId) -> None:
        self.engine = engine
        self.character = character
        self.database = engine.character(character).database
        self.processing = ProcessingService(engine, character)

    def settings(self) -> CaptureSettings:
        """Read durable capture policy independently of recall selection."""

        def read(connection: sqlite3.Connection) -> CaptureSettings:
            row = connection.execute("SELECT * FROM capture_policy").fetchone()
            return CaptureSettings(
                enabled=bool(row_int(row, "enabled")), revision=row_int(row, "revision")
            )

        return self.database.read(read)

    def configure(self, value: CaptureSettings, authority: MutationAuthority) -> CaptureSettings:
        """Confirmed-user CAS changes affect future drain and never approve imports."""
        if authority.mode != "confirmed_user":
            raise AccessDeniedError()

        def write(connection: sqlite3.Connection) -> CaptureSettings:
            with self.database.transaction(connection):
                row = connection.execute("SELECT * FROM capture_policy").fetchone()
                current = CaptureSettings(
                    enabled=bool(row_int(row, "enabled")), revision=row_int(row, "revision")
                )
                if value.enabled == current.enabled:
                    return current
                if value.revision != current.revision:
                    raise RevisionConflictError()
                connection.execute(
                    "UPDATE capture_policy SET enabled=?,revision=revision+1",
                    (int(value.enabled),),
                )
                return CaptureSettings(enabled=value.enabled, revision=current.revision + 1)

        return self.database.write(write)

    def pending(self, limit: int = 8) -> tuple[CaptureCheckpoint, ...]:
        """Bounded FIFO by durable insertion order; interrupted processing resumes first."""
        if not 1 <= limit <= 32:
            raise ValueError("Capture batch must contain between one and 32 deliveries.")

        def read(connection: sqlite3.Connection) -> tuple[CaptureCheckpoint, ...]:
            return tuple(
                CaptureCheckpoint.model_validate(
                    {
                        "delivery_key": row_text(row, "delivery_key"),
                        "sources": _REFERENCE_ADAPTER.validate_json(row_text(row, "source_json")),
                        "status": row_text(row, "status"),
                        "job_id": UUID(row_text(row, "job_id")) if row["job_id"] else None,
                        "error_code": row_text(row, "error_code") if row["error_code"] else None,
                    }
                )
                for row in connection.execute(
                    "SELECT * FROM native_capture WHERE status<>'complete' ORDER BY rowid LIMIT ?",
                    (limit,),
                )
            )

        return self.database.read(read)

    def _mark(self, key: str, status: str, job_id: CanonicalId) -> None:
        self.database.write(
            lambda connection: connection.execute(
                "UPDATE native_capture SET status=?,job_id=? WHERE delivery_key=?",
                (status, str(job_id), key),
            )
        )

    async def drain(
        self, model: ExtractionModel, executor: Executor, *, limit: int = 8
    ) -> tuple[CaptureCheckpoint, ...]:
        """Called only after realtime reservation release; cancellation leaves durable work.

        Gateway admission is the final P3 guard. Caller owns and drains the executor
        before closing the engine. No import approval, seeding or whole-history snapshot occurs.
        """
        loop = asyncio.get_running_loop()
        if not (await loop.run_in_executor(executor, self.settings)).enabled:
            return ()
        checkpoints = await loop.run_in_executor(executor, lambda: self.pending(limit))
        spec = native_specification(
            model.specification.model, model.specification.model_digest, workload="P3"
        )
        if model.specification != spec:
            raise ValueError("Native capture requires the pinned P3 processing profile.")
        for item in checkpoints:
            if not (await loop.run_in_executor(executor, self.settings)).enabled:
                break
            request = JobRequest(
                idempotency_key="native-capture:" + item.delivery_key,
                owner_store_id=self.character,
                specification=spec,
                source_kind="native",
                source_references=item.sources,
            )
            job = await loop.run_in_executor(
                executor, lambda request=request: self.processing.create(request)
            )
            identity = job.definition.job_id
            await loop.run_in_executor(
                executor,
                lambda item=item, identity=identity: self._mark(
                    item.delivery_key, "processing", identity
                ),
            )
            outcome = await self.processing.run(
                identity,
                model,
                max_windows=8,
                retry_failed=True,
                window_attempts=3,
                continue_on_error=True,
            )

            # Each native decision has its own immutable receipt; retrying a partially
            # accepted delivery cannot append duplicate memories or supports.
            def accept(identity: CanonicalId = identity) -> bool:
                last = ""
                while True:
                    rows = self.database.read(
                        lambda connection, last=last: connection.execute(
                            "SELECT candidate_id FROM processing_candidates WHERE job_id=? "
                            "AND candidate_id>? ORDER BY candidate_id LIMIT 128",
                            (str(identity), last),
                        ).fetchall()
                    )
                    if not rows:
                        return True
                    for row in rows:
                        if not self.settings().enabled:
                            return False
                        last = row_text(row, "candidate_id")
                        candidate = UUID(last)
                        NativeAcceptanceService(self.engine, self.character).accept(
                            NativeAcceptanceRequest(
                                candidate_id=candidate,
                                idempotency_key="native-capture:" + str(candidate),
                            )
                        )

            accepted_all = await loop.run_in_executor(executor, accept)
            status = (
                "complete"
                if outcome.status == "complete" and accepted_all
                else "paused"
                if not accepted_all or outcome.status in {"paused", "running"}
                else "failed"
            )
            await loop.run_in_executor(
                executor,
                lambda item=item, identity=identity, status=status: self._mark(
                    item.delivery_key, status, identity
                ),
            )
        return await loop.run_in_executor(executor, lambda: self.pending(limit))
