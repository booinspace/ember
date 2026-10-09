"""Atomic normalized archive revisions, provider crosswalks and resume cursors."""

import sqlite3
from concurrent.futures import Future
from typing import Protocol, runtime_checkable
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from ember_memory_abi.archive import (
    ArchiveWriteReceipt,
    Document,
    GraphNode,
    ImportCursor,
    Message,
    NormalizedArchiveBatch,
    SourceRecord,
    validate_archive_write,
)
from ember_memory_abi.common import (
    CanonicalId,
    canonical_object_sha256,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.errors import (
    ErrorDetails,
    NotFoundError,
    RevisionConflictError,
    UnsupportedWriteError,
)
from ember_memory_abi.evidence import Evidence, SourceRef

from .database import SqliteDatabase, model_bytes, payload_hash, row_int, row_text
from .errors import CorruptStoreError, IdempotencyConflictError
from .models import SourceKey, SourceKeyAssignment


class SourceResolver(Protocol):
    """Resolve immutable sources without coupling a character client to library storage layout."""

    def resolve(self, reference: SourceRef) -> SourceRecord:
        """Read a verified exact source revision or report typed unavailability/corruption."""
        ...

    def validate(self, evidence: Evidence) -> str:
        """Resolve original text and validate its exact evidence coordinates and hashes."""
        ...

    def persist_evidence(
        self,
        evidence: Evidence,
        *,
        local_connection: sqlite3.Connection | None = None,
        local_store_id: CanonicalId | None = None,
    ) -> None:
        """Persist an immutable span with its source, reusing a native writer transaction."""
        ...


@runtime_checkable
class SourceHeaderResolver(Protocol):
    """Optional bounded metadata availability check, without source body hydration."""

    def unavailable_sources(
        self,
        references: tuple[SourceRef, ...],
        *,
        local_connection: sqlite3.Connection,
        local_store_id: CanonicalId,
        call_budget: int,
    ) -> tuple[tuple[SourceRef, ...], int]:
        """Read exact source/part headers grouped by store; unavailable scopes fail closed."""
        ...


def source_text(record: SourceRecord, reference: SourceRef) -> str:
    """Resolve a specific available canonical text part; never concatenate or return reasoning."""
    from ember_memory_abi.errors import SourceUnavailableError

    if not isinstance(record, (Message, Document)) or reference.part_id is None:
        raise SourceUnavailableError()
    for part in record.parts:
        if (
            part.part_id == reference.part_id
            and part.status == "available"
            and part.text is not None
        ):
            return part.text
    raise SourceUnavailableError()


class SqliteArchiveStore:
    """Trusted maintenance writer bound to one source store/namespace, implementing
    ArchiveWriter.
    """

    def __init__(
        self,
        database: SqliteDatabase,
        namespace: str,
        *,
        supported_extensions: frozenset[str] = frozenset(),
    ) -> None:
        self.database = database
        self.namespace = namespace
        self._extensions = supported_extensions
        self._adapter: TypeAdapter[SourceRecord] = TypeAdapter(SourceRecord)

    @property
    def source_store_id(self) -> CanonicalId:
        """Immutable trusted store identity, independent of provider records."""
        return self.database.store_id

    def prepare_keys(self, keys: tuple[SourceKey, ...]) -> tuple[SourceKeyAssignment, ...]:
        """Prepare stable/existing or fresh UUID4 assignments without committing partial import
        state.
        """
        if len(keys) > 1000 or len(
            set((key.adapter, key.record_kind, key.external_id) for key in keys)
        ) != len(keys):
            raise ValueError("Source-key preparation must be bounded and unique.")

        def prepare(connection: sqlite3.Connection) -> tuple[SourceKeyAssignment, ...]:
            assignments: list[SourceKeyAssignment] = []
            for key in keys:
                row = connection.execute(
                    (
                        "SELECT record_id FROM source_keys WHERE adapter=? AND record_kind=? "
                        "AND external_id=?"
                    ),
                    (key.adapter, key.record_kind, key.external_id),
                ).fetchone()
                if row is None:
                    record_id = uuid4()
                else:
                    from pydantic import UUID4

                    adapter: TypeAdapter[CanonicalId] = TypeAdapter(UUID4)
                    record_id = adapter.validate_json('"' + row_text(row, "record_id") + '"')
                assignments.append(SourceKeyAssignment(key=key, record_id=record_id))
            return tuple(assignments)

        return self.database.read(prepare)

    def write_batch(
        self, batch: NormalizedArchiveBatch, *, keys: tuple[SourceKeyAssignment, ...] = ()
    ) -> ArchiveWriteReceipt:
        """Commit immutable records, key assignments, cursor, receipt and audit in one
        transaction.
        """
        snapshot = NormalizedArchiveBatch.model_validate_json(batch.model_dump_json())
        frozen_keys = tuple(
            SourceKeyAssignment.model_validate_json(key.model_dump_json()) for key in keys
        )
        return self.database.write(
            lambda connection: self._write(connection, snapshot, frozen_keys)
        )

    def submit_batch(
        self, batch: NormalizedArchiveBatch, *, keys: tuple[SourceKeyAssignment, ...] = ()
    ) -> Future[ArchiveWriteReceipt]:
        """Submit a snapshot to existing bounded writers without blocking a realtime event loop."""
        snapshot = NormalizedArchiveBatch.model_validate_json(batch.model_dump_json())
        return self.database.submit(
            lambda connection: self._write(connection, snapshot, tuple(keys)), write=True
        )

    def _write(
        self,
        connection: sqlite3.Connection,
        batch: NormalizedArchiveBatch,
        keys: tuple[SourceKeyAssignment, ...],
    ) -> ArchiveWriteReceipt:
        if (
            batch.source_store_id != self.source_store_id
            or batch.source_namespace != self.namespace
        ):
            from ember_memory_abi.errors import AccessDeniedError

            raise AccessDeniedError()
        if len(keys) > 1000:
            raise ValueError("Source-key assignments exceed the batch bound.")
        record_keys = {
            (record.source.record_kind, record.source.record_id) for record in batch.records
        }
        if any(
            assignment.key.adapter != batch.resume_cursor.adapter
            or (assignment.key.record_kind, assignment.record_id) not in record_keys
            for assignment in keys
        ):
            raise UnsupportedWriteError()
        validate_archive_write(batch, supported_extension_names=self._extensions)
        request_hash = canonical_object_sha256(
            {
                "batch": batch.model_dump(mode="json"),
                "keys": [key.model_dump(mode="json") for key in keys],
            }
        )
        with self.database.transaction(connection):
            if (
                connection.execute("SELECT coverage FROM store_coverage").fetchone()[0]
                == "evidence_only"
            ):
                raise UnsupportedWriteError()

            existing = connection.execute(
                "SELECT request_sha256,receipt_json FROM archive_receipts WHERE idempotency_key=?",
                (batch.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if row_text(existing, "request_sha256") != request_hash:
                    raise IdempotencyConflictError()
                return ArchiveWriteReceipt.model_validate_json(
                    row_text(existing, "receipt_json")
                ).model_copy(update={"outcome": "already_applied"})
            for assignment in keys:
                key = assignment.key
                old = connection.execute(
                    (
                        "SELECT record_id FROM source_keys WHERE adapter=? AND record_kind=? "
                        "AND external_id=?"
                    ),
                    (key.adapter, key.record_kind, key.external_id),
                ).fetchone()
                if old is not None:
                    if row_text(old, "record_id") != str(assignment.record_id):
                        raise RevisionConflictError()
                else:
                    collision = connection.execute(
                        (
                            "SELECT external_id FROM source_keys WHERE adapter=? AND "
                            "record_kind=? AND record_id=?"
                        ),
                        (key.adapter, key.record_kind, str(assignment.record_id)),
                    ).fetchone()
                    if collision is not None:
                        raise RevisionConflictError()
                    connection.execute(
                        "INSERT INTO source_keys VALUES(?,?,?,?)",
                        (key.adapter, key.record_kind, key.external_id, str(assignment.record_id)),
                    )
            written = sum(self._persist_record(connection, record) for record in batch.records)
            cursor = batch.resume_cursor
            old_cursor = connection.execute(
                (
                    "SELECT cursor_json FROM imports WHERE adapter=? AND adapter_version=? "
                    "AND inventory_sha256=? AND component_sha256=?"
                ),
                (
                    cursor.adapter,
                    cursor.adapter_version,
                    cursor.logical_inventory_sha256,
                    cursor.component_sha256,
                ),
            ).fetchone()
            if (
                old_cursor is not None
                and ImportCursor.model_validate_json(
                    row_text(old_cursor, "cursor_json")
                ).next_record_index
                > cursor.next_record_index
            ):
                raise RevisionConflictError()
            connection.execute(
                (
                    "INSERT INTO imports VALUES(?,?,?,?,?) ON CONFLICT DO UPDATE SET "
                    "cursor_json=excluded.cursor_json"
                ),
                (
                    cursor.adapter,
                    cursor.adapter_version,
                    cursor.logical_inventory_sha256,
                    cursor.component_sha256,
                    model_bytes(cursor),
                ),
            )
            receipt = ArchiveWriteReceipt(
                idempotency_key=batch.idempotency_key,
                outcome="applied",
                records_written=written,
                resume_cursor=cursor,
            )
            connection.execute(
                "INSERT INTO archive_receipts VALUES(?,?,?)",
                (batch.idempotency_key, request_hash, model_bytes(receipt)),
            )
            _, recorded = self.database.recorded_stamp(connection)
            connection.execute(
                "INSERT INTO audit_events VALUES(?,?,?,?,?)",
                (str(uuid4()), "archive_batch", None, recorded, "committed"),
            )
        return receipt

    def _persist_record(self, connection: sqlite3.Connection, record: SourceRecord) -> int:
        reference = record.source
        row = connection.execute(
            (
                "SELECT payload_json,payload_sha256 FROM source_records WHERE record_kind=? "
                "AND record_id=? "
                "AND revision=?"
            ),
            (reference.record_kind, str(reference.record_id), reference.record_revision),
        ).fetchone()
        if row is not None:
            if payload_hash(row_text(row, "payload_json")) != row_text(row, "payload_sha256"):
                raise CorruptStoreError()
            try:
                existing = self._adapter.validate_json(row_text(row, "payload_json"))
            except ValidationError as error:
                raise CorruptStoreError() from error
            if canonical_object_sha256(
                existing.model_dump(mode="json", exclude={"recorded_at"})
            ) != canonical_object_sha256(record.model_dump(mode="json", exclude={"recorded_at"})):
                raise RevisionConflictError()
            return 0
        previous = connection.execute(
            (
                "SELECT MAX(revision) AS revision FROM source_records WHERE record_kind=? AND"
                " record_id=?"
            ),
            (reference.record_kind, str(reference.record_id)),
        ).fetchone()
        actual: object = previous["revision"] if previous is not None else None
        if reference.record_revision != (actual + 1 if isinstance(actual, int) else 1):
            raise RevisionConflictError(ErrorDetails(record_id=reference.record_id))
        recorded_at, recorded_us = self.database.recorded_stamp(connection)
        connection.execute(
            "INSERT INTO index_outbox(kind,record_id,revision,policy_epoch,recorded_at_us) "
            "SELECT ?,?,?,policy_epoch,? FROM store_metadata",
            ("source_changed", str(reference.record_id), reference.record_revision, recorded_us),
        )
        record = record.model_copy(update={"recorded_at": recorded_at})
        payload = model_bytes(record)
        source_time = record.created_time.value if isinstance(record, (Message, Document)) else None
        from ember_memory_abi.archive import Conversation

        if isinstance(record, Conversation):
            source_time = record.created_time.value
        role = record.actor.role if isinstance(record, Message) else None
        connection.execute(
            "INSERT INTO source_records VALUES(?,?,?,?,?,?,?,?)",
            (
                reference.record_kind,
                str(reference.record_id),
                reference.record_revision,
                timestamp_to_unix_microseconds(record.recorded_at),
                timestamp_to_unix_microseconds(source_time) if source_time else None,
                role,
                payload,
                payload_hash(payload),
            ),
        )
        if isinstance(record, (Message, Document)):
            for ordinal, part in enumerate(record.parts):
                connection.execute(
                    "INSERT INTO message_parts VALUES(?,?,?,?,?,?,?,?)",
                    (
                        reference.record_kind,
                        str(reference.record_id),
                        reference.record_revision,
                        str(part.part_id),
                        ordinal,
                        part.kind,
                        part.status,
                        part.content_sha256,
                    ),
                )
        if isinstance(record, GraphNode):
            edges = [("child", index, link) for index, link in enumerate(record.children)]
            if record.parent is not None:
                edges.append(("parent", 0, record.parent))
            for direction, ordinal, link in edges:
                connection.execute(
                    "INSERT INTO graph_edges VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(reference.record_id),
                        reference.record_revision,
                        direction,
                        ordinal,
                        link.status,
                        str(link.target.record_id) if link.target else None,
                        link.target.record_revision if link.target else None,
                        link.raw_reference_sha256,
                    ),
                )
        return 1

    def get_record(self, reference: SourceRef) -> SourceRecord:
        """Maintenance read of an exact immutable revision, verifying bytes and the typed
        contract.
        """

        def read(connection: sqlite3.Connection) -> SourceRecord:
            row = connection.execute(
                (
                    "SELECT payload_json,payload_sha256 FROM source_records WHERE "
                    "record_kind=? AND record_id=? AND revision=?"
                ),
                (reference.record_kind, str(reference.record_id), reference.record_revision),
            ).fetchone()
            if reference.source_store_id != self.source_store_id or row is None:
                raise NotFoundError()
            payload = row_text(row, "payload_json")
            if payload_hash(payload) != row_text(row, "payload_sha256"):
                raise CorruptStoreError()
            try:
                return self._adapter.validate_json(payload)
            except ValidationError as error:
                raise CorruptStoreError() from error

        return self.database.read(read)

    def cursor(self, cursor: ImportCursor) -> ImportCursor | None:
        """Read the committed resume position for an exact adapter/inventory/component identity."""

        def read(connection: sqlite3.Connection) -> ImportCursor | None:
            row = connection.execute(
                (
                    "SELECT cursor_json FROM imports WHERE adapter=? AND adapter_version=? "
                    "AND inventory_sha256=? AND component_sha256=?"
                ),
                (
                    cursor.adapter,
                    cursor.adapter_version,
                    cursor.logical_inventory_sha256,
                    cursor.component_sha256,
                ),
            ).fetchone()
            return ImportCursor.model_validate_json(row_text(row, "cursor_json")) if row else None

        return self.database.read(read)

    def latest_record(self, record_kind: str, record_id: CanonicalId) -> SourceRecord | None:
        """Maintenance lookup for deterministic normalization; unknown identity returns None."""

        def read(connection: sqlite3.Connection) -> SourceRecord | None:
            row = connection.execute(
                "SELECT payload_json,payload_sha256 FROM source_records WHERE record_kind=? "
                "AND record_id=? ORDER BY revision DESC LIMIT 1",
                (record_kind, str(record_id)),
            ).fetchone()
            if row is None:
                return None
            payload = row_text(row, "payload_json")
            if payload_hash(payload) != row_text(row, "payload_sha256"):
                raise CorruptStoreError()
            try:
                record = self._adapter.validate_json(payload)
            except ValidationError as error:
                raise CorruptStoreError() from error
            if (
                record.source.source_store_id,
                record.source.record_kind,
                record.source.record_id,
            ) != (
                self.source_store_id,
                record_kind,
                record_id,
            ):
                raise CorruptStoreError()
            return record

        return self.database.read(read)

    def latest_records(
        self, identities: tuple[tuple[str, CanonicalId], ...]
    ) -> dict[tuple[str, CanonicalId], SourceRecord]:
        """Resolve up to 10,000 current records in one reader task.

        SQLite's bound-variable limit requires small SQL chunks, but all chunks run on
        the same owned reader connection. This is intentionally an engine maintenance
        API rather than a public Memory ABI operation.
        """
        if len(identities) > 10_000 or len(set(identities)) != len(identities):
            raise ValueError("Source revision lookup must be bounded and unique.")
        requested = set(identities)

        def read(connection: sqlite3.Connection) -> dict[tuple[str, CanonicalId], SourceRecord]:
            result: dict[tuple[str, CanonicalId], SourceRecord] = {}
            for offset in range(0, len(identities), 450):
                chunk = identities[offset : offset + 450]
                placeholders = ",".join("(?,?)" for _ in chunk)
                parameters = tuple(
                    item for identity in chunk for item in (identity[0], str(identity[1]))
                )
                rows = connection.execute(
                    "SELECT record_kind,record_id,revision,payload_json,payload_sha256 "
                    "FROM source_records r "
                    "WHERE (record_kind,record_id) IN ("
                    + placeholders
                    + ") AND revision=(SELECT MAX(h.revision) FROM source_records h "
                    "WHERE h.record_kind=r.record_kind AND h.record_id=r.record_id)",
                    parameters,
                ).fetchall()
                for row in rows:
                    payload = row_text(row, "payload_json")
                    if payload_hash(payload) != row_text(row, "payload_sha256"):
                        raise CorruptStoreError()
                    try:
                        record = self._adapter.validate_json(payload)
                    except ValidationError as error:
                        raise CorruptStoreError() from error
                    key = (record.source.record_kind, record.source.record_id)
                    if (
                        key not in requested
                        or key in result
                        or record.source.source_store_id != self.source_store_id
                        or key[0] != row_text(row, "record_kind")
                        or str(key[1]) != row_text(row, "record_id")
                        or record.source.record_revision != row_int(row, "revision")
                    ):
                        raise CorruptStoreError()
                    result[key] = record
            return result

        return self.database.read(read)
