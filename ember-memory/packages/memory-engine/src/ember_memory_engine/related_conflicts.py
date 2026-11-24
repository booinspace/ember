"""Status-only read of unresolved native conflicts attached to selected revisions."""

import sqlite3

from ember_memory_abi.memory import CandidateMemory
from ember_memory_abi.related import ConflictStatus, MemoryReference, MemoryRelationship

from ._state import CharacterState
from .archive import SourceHeaderResolver
from .database import load_model
from .selection import settings


def statuses(
    state: CharacterState,
    connection: sqlite3.Connection,
    refs: tuple[MemoryReference, ...],
    row_budget: int,
) -> tuple[tuple[ConflictStatus, ...] | None, int]:
    """Return no candidate metadata. An exhausted scan cannot certify absence of conflict."""
    if not refs:
        return (), 0
    if row_budget < 1:
        return None, 0
    keys = tuple((str(ref.memory_id), ref.revision) for ref in refs)
    wanted = ",".join("(?,?)" for _ in keys)
    rows = connection.execute(
        "SELECT * FROM memory_relationships WHERE kind='conflicts' AND target_id IS NULL "
        "AND (source_id,source_revision) IN (" + wanted + ") LIMIT ?",
        (*(value for key in keys for value in key), row_budget + 1),
    ).fetchall()
    if len(rows) > row_budget:
        return None, len(rows)
    conflicts: set[tuple[str, int]] = set()
    if rows and settings(connection).local_enabled:
        for row in rows:
            edge = load_model(row, MemoryRelationship)
            key = (str(edge.source.memory_id), edge.source.revision)
            if key in keys and key not in conflicts and _visible(state, connection, edge):
                conflicts.add(key)
    return tuple(
        ConflictStatus(memory=ref, unresolved=key in conflicts)
        for ref, key in zip(refs, keys, strict=True)
    ), len(rows)


def _visible(
    state: CharacterState, connection: sqlite3.Connection, edge: MemoryRelationship
) -> bool:
    if edge.candidate_id is None or not edge.evidence:
        return False
    row = connection.execute(
        "SELECT * FROM candidates WHERE candidate_id=? ORDER BY revision DESC LIMIT 1",
        (str(edge.candidate_id),),
    ).fetchone()
    if row is None:
        return False
    candidate = load_model(row, CandidateMemory)
    if (
        candidate.owner_store_id != state.character_id
        or candidate.draft.content.evidence != edge.evidence
        or connection.execute(
            "SELECT 1 FROM processing_candidates c JOIN review_decisions r "
            "ON r.fingerprint=c.fingerprint WHERE c.candidate_id=? AND c.revision=? "
            "AND (r.candidate_id=c.candidate_id OR r.decision='reject') LIMIT 1",
            (str(candidate.candidate_id), candidate.revision),
        ).fetchone()
        or any(
            connection.execute(
                "SELECT 1 FROM suppression_targets WHERE target_kind='claim' AND target_key=?",
                (key,),
            ).fetchone()
            for key in state.claim_keys(connection, candidate.draft)
        )
    ):
        return False
    sources = tuple(
        {
            item.evidence.source.identity_key(): item.evidence.source for item in edge.evidence
        }.values()
    )
    if len(sources) > 24:
        return False
    # Native acceptance verifies local exact spans before recording this edge. Recheck
    # current part headers and suppression; never hydrate or return deferred text.
    resolver = state.resolver
    if not isinstance(resolver, SourceHeaderResolver) or any(
        ref.source_store_id != state.character_id
        or not state.source_permitted(connection, ref, "evidence_read")
        for ref in sources
    ):
        return False
    missing, _ = resolver.unavailable_sources(
        sources, local_connection=connection, local_store_id=state.character_id, call_budget=0
    )
    return not missing
