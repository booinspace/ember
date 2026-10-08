"""Shared canonical source paging and suppression identities; no derived index dependency."""

import sqlite3
from collections.abc import Iterator
from hashlib import sha256

from pydantic import TypeAdapter, ValidationError

from ember_memory_abi.archive import SourceRecord
from ember_memory_abi.common import canonical_object_sha256
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
