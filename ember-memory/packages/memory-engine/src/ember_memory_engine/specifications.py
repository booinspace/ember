"""Selective closure of immutable processing definitions, without jobs or private decisions."""

import sqlite3

from pydantic import JsonValue, TypeAdapter, ValidationError

from ember_memory_abi.archive import ProcessingSpecRef
from ember_memory_abi.common import Sha256, canonical_object_sha256

from .database import payload_hash, row_text
from .errors import CorruptStoreError


def specification_rows(
    connection: sqlite3.Connection, references: tuple[ProcessingSpecRef, ...]
) -> tuple[sqlite3.Row, ...]:
    """Resolve exact known definitions and their policy closure for seeding/portability.

    Legacy/provider specifications may have only a logical reference. Ember's
    Phase 6 definitions must exist and verify. Never copy an entire spec/job table.
    """
    pending = [(ref.specification_sha256, ref.name == "ember.atomic-claims") for ref in references]
    seen: set[str] = set()
    result: list[sqlite3.Row] = []
    adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
    digest_adapter: TypeAdapter[str] = TypeAdapter(Sha256)
    while pending:
        digest, required = pending.pop()
        if digest in seen:
            continue
        if len(seen) >= 1024:
            raise CorruptStoreError()
        seen.add(digest)
        row = connection.execute(
            "SELECT * FROM processing_specs WHERE specification_sha256=?", (digest,)
        ).fetchone()
        if row is None:
            if required:
                raise CorruptStoreError()
            continue
        payload = row_text(row, "payload_json")
        if payload_hash(payload) != row_text(row, "payload_sha256"):
            raise CorruptStoreError()
        try:
            value = adapter.validate_json(payload)
            if canonical_object_sha256(value) != digest:
                raise CorruptStoreError()
            if isinstance(value, dict) and "policy_sha256" in value:
                pending.append((digest_adapter.validate_python(value["policy_sha256"]), True))
        except ValidationError as error:
            raise CorruptStoreError() from error
        result.append(row)
    return tuple(result)
