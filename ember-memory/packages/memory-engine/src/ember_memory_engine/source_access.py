"""Shared canonical source paging and suppression identities; no derived index dependency."""

import sqlite3
from collections.abc import Iterator
from hashlib import sha256

from pydantic import TypeAdapter, ValidationError

from ember_memory_abi.archive import SourceRecord
from ember_memory_abi.common import (
    CanonicalId,
    canonical_object_sha256,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.evidence import SourceRef

from .database import row_int, row_text
from .errors import CorruptStoreError

_ADAPTER: TypeAdapter[SourceRecord] = TypeAdapter(SourceRecord)


def source_keys(reference: SourceRef) -> tuple[str, str]:
    """Match canonical suppression keys across source revisions; part and whole are independent."""

    def key(part: str | None) -> str:
        return canonical_object_sha256(
            {
                "store": str(reference.source_store_id),
                "kind": reference.record_kind,
                "id": str(reference.record_id),
                "part": part,
            }
        )

    return key(str(reference.part_id) if reference.part_id else None), key(None)


def source_rows(
    connection: sqlite3.Connection, watermark: int, selection: str = ""
) -> Iterator[SourceRecord]:
    """Fetch bounded pages and close cursors before tokenization/inference."""
    last = 0
    while True:
        rows = connection.execute(
            "SELECT rowid AS source_rowid,* FROM source_records WHERE rowid>? AND "
            "recorded_at_us<=? " + selection + " ORDER BY rowid LIMIT 256",
            (last, watermark),
        ).fetchall()
        if not rows:
            return
        for row in rows:
            last = row_int(row, "source_rowid")
            payload = row_text(row, "payload_json")
            if sha256(payload.encode()).hexdigest() != row_text(row, "payload_sha256"):
                raise CorruptStoreError()
            try:
                yield _ADAPTER.validate_json(payload)
            except ValidationError as error:
                raise CorruptStoreError() from error


def memory_source_scope(
    character_id: CanonicalId, now: str, memory_id: str, revision: str, *, prefix: str = ""
) -> tuple[str, tuple[str, str]]:
    """Scope cited evidence before top-k; part grants never imply whole-archive access."""
    return (
        "NOT EXISTS(SELECT 1 FROM "
        + prefix
        + "memory_evidence e WHERE e.memory_id="
        + memory_id
        + " AND e.revision="
        + revision
        + " AND e.source_store_id<>? AND NOT EXISTS(SELECT 1 FROM "
        + prefix
        + "source_grants g WHERE g.source_store_id=e.source_store_id "
        "AND g.record_kind=e.record_kind "
        "AND g.record_id=e.record_id AND g.record_revision=e.record_revision "
        "AND g.part_id IN (e.part_id,'') "
        "AND g.capability='evidence_read' "
        "AND (g.expires_at IS NULL OR g.expires_at>?)))",
        (str(character_id), now),
    )


def evidence_grant_window(
    connection: sqlite3.Connection,
    character_id: CanonicalId,
    references: tuple[SourceRef, ...],
    now: str,
) -> tuple[bool, int | None]:
    """Maintenance-only projection of existing grants, not a new authorization authority.

    Each hard evidence source needs a current part or whole-record grant. An
    unbounded matching grant wins; otherwise the latest matching expiry wins.
    The complete memory expires at the earliest of these independent boundaries.
    Canonical suppression and fresh selected-result checks remain separate.
    """
    boundaries: list[int] = []
    for ref in references:
        if ref.source_store_id == character_id:
            continue
        row = connection.execute(
            "SELECT MAX(expires_at IS NULL) AS permanent,MAX(expires_at) AS expiry "
            "FROM source_grants WHERE source_store_id=? AND record_kind=? AND record_id=? "
            "AND record_revision=? AND part_id IN (?, '') AND capability='evidence_read' "
            "AND (expires_at IS NULL OR expires_at>?)",
            (
                str(ref.source_store_id),
                ref.record_kind,
                str(ref.record_id),
                ref.record_revision,
                str(ref.part_id) if ref.part_id else "",
                now,
            ),
        ).fetchone()
        if row["permanent"] == 1:
            continue
        expiry: object = row["expiry"]
        if not isinstance(expiry, str):
            return False, 0
        boundaries.append(timestamp_to_unix_microseconds(expiry))
    return True, min(boundaries) if boundaries else None
