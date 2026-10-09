"""Canonical recall policy; origin selection narrows access without modifying grants."""

import sqlite3
from uuid import UUID, uuid4

from ember_memory_abi.errors import AccessDeniedError, RevisionConflictError
from ember_memory_abi.selection import RecallSelection

from ._state import CharacterState
from .database import row_int, row_text


def settings(connection: sqlite3.Connection) -> RecallSelection:
    """Read the singleton and at most 256 configured library overrides."""
    row = connection.execute("SELECT * FROM recall_policy WHERE singleton=1").fetchone()
    libraries = connection.execute(
        "SELECT library_id,enabled FROM recall_libraries ORDER BY library_id LIMIT 257"
    ).fetchall()
    if len(libraries) > 256:
        raise ValueError("Recall selection exceeds its configured library budget.")
    return RecallSelection.model_validate(
        {
            "local_enabled": bool(row_int(row, "local_enabled")),
            "import_mode": row_text(row, "import_mode"),
            "revision": row_int(row, "revision"),
            "included_libraries": tuple(
                UUID(row_text(item, "library_id")) for item in libraries if row_int(item, "enabled")
            ),
            "excluded_libraries": tuple(
                UUID(row_text(item, "library_id"))
                for item in libraries
                if not row_int(item, "enabled")
            ),
        }
    )


def configure(
    state: CharacterState, connection: sqlite3.Connection, value: RecallSelection
) -> RecallSelection:
    """Commit selection/epoch/audit atomically; identical retries return the current value."""
    if state.authority.mode != "confirmed_user":
        raise AccessDeniedError()
    with state.database.transaction(connection):
        current = settings(connection)
        fields = {"revision"}
        if current.model_dump(exclude=fields) == value.model_dump(exclude=fields):
            return current
        if current.revision != value.revision:
            raise RevisionConflictError()
        connection.execute(
            "UPDATE recall_policy SET local_enabled=?,import_mode=?,revision=revision+1 "
            "WHERE singleton=1",
            (int(value.local_enabled), value.import_mode),
        )
        connection.execute("DELETE FROM recall_libraries")
        connection.executemany(
            "INSERT INTO recall_libraries VALUES(?,?)",
            [(str(x), 1) for x in value.included_libraries]
            + [(str(x), 0) for x in value.excluded_libraries],
        )
        connection.execute("UPDATE store_metadata SET policy_epoch=policy_epoch+1")
        _, recorded = state.database.recorded_stamp(connection)
        connection.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?,?)",
            (str(uuid4()), "recall_selection", None, recorded, "confirmed_user"),
        )
        return settings(connection)


def origin_scope(character_id: UUID, origin_column: str) -> tuple[str, tuple[str, str]]:
    """Compile fixed internal origin columns; callers cannot supply arbitrary SQL."""
    if origin_column not in {"p.origin_key", "d.origin_key", "c.origin_key"}:
        raise ValueError("Unsupported recall origin projection.")
    sql = (
        "((" + origin_column + "=? AND (SELECT local_enabled FROM recall_policy)=1) OR "
        "(" + origin_column + "<>? AND "
        "(SELECT import_mode FROM recall_policy)<>'none' AND "
        "NOT EXISTS(SELECT 1 FROM recall_libraries rl WHERE rl.library_id="
        + origin_column
        + " AND rl.enabled=0) AND "
        "((SELECT import_mode FROM recall_policy)='all' OR "
        "EXISTS(SELECT 1 FROM recall_libraries rl WHERE rl.library_id="
        + origin_column
        + " AND rl.enabled=1))))"
    )
    return sql, (str(character_id), str(character_id))


def permitted(
    state: CharacterState, connection: sqlite3.Connection, memory_id: str, revision: int
) -> bool:
    """Check the exact memory's canonical origin; missing projection fails closed."""
    clause, parameters = origin_scope(state.character_id, "p.origin_key")
    return (
        connection.execute(
            "SELECT 1 FROM claim_projection p WHERE p.memory_id=? AND p.revision=? AND " + clause,
            (memory_id, revision, *parameters),
        ).fetchone()
        is not None
    )


def source_scope(character_id: UUID, column: str) -> tuple[str, tuple[str, str]]:
    """Scope archive sources without treating selection as an archive_search grant."""
    if column not in {"s.source_store_id", "p.source_store_id", "source_store_id"}:
        raise ValueError("Unsupported archive scope column.")
    clause, values = origin_scope(character_id, "p.origin_key")
    return clause.replace("p.origin_key", column), values


def unrestricted(connection: sqlite3.Connection) -> bool:
    """Cheap default-policy check permits existing certified projection optimization."""
    return (
        connection.execute(
            "SELECT 1 FROM recall_policy WHERE local_enabled=1 AND import_mode='all' "
            "AND NOT EXISTS(SELECT 1 FROM recall_libraries WHERE enabled=0)"
        ).fetchone()
        is not None
    )
