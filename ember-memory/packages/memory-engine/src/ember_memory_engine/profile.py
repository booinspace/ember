"""Small indexed profile selection and durable human controls in the character store."""

import sqlite3
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import Field

from ember_memory_abi.common import (
    CanonicalId,
    ContractModel,
    timestamp_from_datetime,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.errors import AccessDeniedError
from ember_memory_abi.memory import FactPayload, Memory, PinnedMemory, StringValue
from ember_memory_abi.predicates import profile_predicates
from ember_memory_abi.related import ProfileOmission, ProfileSelection

from ._state import CharacterState
from .database import row_int, row_text
from .selection import origin_scope, permitted
from .source_access import memory_source_scope

FIELDS = tuple(item.name for item in profile_predicates())


class ProfileSettings(ContractModel):
    """Trusted operator controls, independent of provider/model configuration."""

    enabled: bool = True
    subject_id: CanonicalId | None = None
    excluded: Annotated[tuple[str, ...], Field(max_length=4)] = ()


def settings(state: CharacterState, connection: sqlite3.Connection) -> ProfileSettings:
    """Read durable explicit choices; automatic refresh never changes them."""
    row = connection.execute("SELECT * FROM profile_policy WHERE singleton=1").fetchone()
    return ProfileSettings(
        enabled=bool(row_int(row, "enabled")),
        subject_id=UUID(row_text(row, "subject_id")) if row["subject_id"] else None,
        excluded=tuple(
            row_text(item, "predicate")
            for item in connection.execute(
                "SELECT predicate FROM profile_exclusions ORDER BY predicate"
            )
        ),
    )


def configure(
    state: CharacterState, connection: sqlite3.Connection, value: ProfileSettings
) -> None:
    """Persist explicit settings; neither seed, approve, grant access nor mutate personality."""
    if state.authority.mode != "confirmed_user" or any(
        item not in FIELDS for item in value.excluded
    ):
        raise AccessDeniedError()
    with state.database.transaction(connection):
        if (
            value.subject_id is not None
            and not connection.execute(
                "SELECT 1 FROM entities WHERE entity_id=?", (str(value.subject_id),)
            ).fetchone()
        ):
            raise AccessDeniedError()
        connection.execute(
            "UPDATE profile_policy SET enabled=?,subject_id=?,"
            "selection_epoch=selection_epoch+1 WHERE singleton=1",
            (
                int(value.enabled),
                str(value.subject_id) if value.subject_id else None,
            ),
        )
        connection.execute("DELETE FROM profile_exclusions")
        connection.executemany(
            "INSERT INTO profile_exclusions VALUES(?)", ((x,) for x in value.excluded)
        )
        _, recorded = state.database.recorded_stamp(connection)
        connection.execute(
            "INSERT INTO audit_events VALUES(?,?,?,?,?)",
            (
                str(uuid4()),
                "profile_settings",
                None,
                recorded,
                "confirmed_user",
            ),
        )


def eligible(state: CharacterState, connection: sqlite3.Connection, memory: Memory) -> bool:
    """Check current time, all hard evidence grants, and canonical suppression."""
    now = timestamp_from_datetime(state.database.clock.now())
    return (
        memory.lifecycle == "active"
        and permitted(state, connection, str(memory.memory_id), memory.revision)
        and not state.memory_suppressed(connection, memory.memory_id, memory.revision)
        and (memory.temporal.valid_from is None or memory.temporal.valid_from <= now)
        and (memory.temporal.valid_until is None or now < memory.temporal.valid_until)
        and all(
            state.source_permitted(connection, item.evidence.source, "evidence_read")
            for item in memory.content.evidence
        )
    )


def select(
    state: CharacterState, connection: sqlite3.Connection
) -> tuple[ProfileSelection, tuple[Memory, ...]]:
    """Four indexed field reads in one existing context worker; no encoder/archive hydration."""
    policy = settings(state, connection)
    epoch = row_int(
        connection.execute("SELECT selection_epoch FROM profile_policy").fetchone(),
        "selection_epoch",
    )
    omitted: list[ProfileOmission] = []
    memories: list[Memory] = []
    pins: list[PinnedMemory] = []
    boundaries: list[str] = []
    now = timestamp_from_datetime(state.database.clock.now())
    for field in FIELDS:
        reason = (
            "disabled"
            if not policy.enabled
            else "unbound_user"
            if policy.subject_id is None
            else ("excluded" if field in policy.excluded else "unknown")
        )
        if reason not in {"disabled", "unbound_user", "excluded"}:
            # Two distinct values suffice to prove a conflict. Duplicate supports don't
            # cause a corpus walk; LIMIT bounds hydration even with bad legacy data.
            access_sql, access_values = memory_source_scope(
                state.character_id, now, "r.memory_id", "r.revision"
            )
            selection_sql, selection_values = origin_scope(state.character_id, "p.origin_key")
            rows = connection.execute(
                "SELECT p.memory_id,p.revision,p.value_key FROM claim_projection p "
                "JOIN memories m ON m.memory_id=p.memory_id AND m.current_revision=p.revision "
                "JOIN memory_revisions r ON r.memory_id=p.memory_id AND r.revision=p.revision "
                "WHERE p.subject_id=? AND p.predicate=? AND r.lifecycle='active' "
                "AND r.modality IN ('FACTUAL_ASSERTION','PREFERENCE') "
                "AND (r.valid_until_us IS NULL OR r.valid_until_us>?) AND "
                + access_sql
                + " AND "
                + selection_sql
                + " ORDER BY r.recorded_at_us DESC,p.memory_id LIMIT 32",
                (
                    str(policy.subject_id),
                    field,
                    timestamp_to_unix_microseconds(now),
                    *access_values,
                    *selection_values,
                ),
            ).fetchall()
            values: dict[str, Memory] = {}
            for row in rows:
                try:
                    memory = state.read_memory(
                        connection, UUID(row_text(row, "memory_id")), row_int(row, "revision")
                    )
                except AccessDeniedError:
                    continue
                payload = memory.content.payload
                for boundary in (memory.temporal.valid_from, memory.temporal.valid_until):
                    if boundary and boundary > now:
                        boundaries.append(boundary)
                if (
                    eligible(state, connection, memory)
                    and isinstance(payload, FactPayload)
                    and isinstance(payload.value, StringValue)
                    and payload.polarity == "positive"
                    and memory.content.modality in {"FACTUAL_ASSERTION", "PREFERENCE"}
                    and payload.subject.entity_id == policy.subject_id
                    and payload.subject.store_id == state.character_id
                    and payload.value.value.strip()
                    and len(payload.value.value.encode()) <= 192
                ):
                    values.setdefault(row_text(row, "value_key"), memory)
            if len(rows) == 32:
                reason = "selection_limit"
            elif len(values) > 1:
                reason = "conflict"
            elif values:
                memory = next(iter(values.values()))
                conflict = connection.execute(
                    "SELECT 1 FROM memory_relationships e WHERE kind='conflicts' AND "
                    "source_id=? AND source_revision=? "
                    "AND NOT EXISTS(SELECT 1 FROM review_decisions r WHERE "
                    "r.candidate_id=json_extract(e.payload_json,'$.candidate_id') "
                    "AND r.decision='reject') LIMIT 1",
                    (str(memory.memory_id), memory.revision),
                ).fetchone()
                if conflict:
                    reason = "conflict"
                else:
                    memories.append(memory)
                    pins.append(
                        PinnedMemory(
                            character_id=state.character_id,
                            memory_id=memory.memory_id,
                            memory_revision=memory.revision,
                            priority=0,
                            pinned_at=memory.created_at,
                            purpose="profile",
                        )
                    )
                    continue
        omitted.append(ProfileOmission(field=field, reason=reason))
    return ProfileSelection(
        enabled=policy.enabled,
        subject_id=policy.subject_id,
        selection_epoch=epoch,
        automatic=tuple(pins),
        omitted=tuple(omitted),
        valid_until=min(boundaries) if boundaries else None,
    ), tuple(memories)
