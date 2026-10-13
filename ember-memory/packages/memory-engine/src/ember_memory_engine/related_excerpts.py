"""Explicit exact-span reads are independently scoped from neighboring archive search."""

import sqlite3

from ember_memory_abi.archive import Document, Message
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.memory import MemorySource
from ember_memory_abi.related import RelatedRequest, SourceObservation

from ._state import CharacterState
from .database import check_deadline
from .retrieval import branch_status
from .selection import settings, source_selected


def excerpts(
    state: CharacterState, connection: sqlite3.Connection, request: RelatedRequest
) -> tuple[tuple[SourceObservation, ...], bool]:
    """At most 24 exact spans/32 KiB; unauthorized or unavailable spans never carry text."""
    if not request.excerpts:
        return (), False
    policy = settings(connection)
    result: list[SourceObservation] = []
    total_bytes = 0
    partial = False
    seen: set[tuple[object, ...]] = set()
    for evidence in request.excerpts:
        check_deadline(state.database.clock, request.deadline_at)
        ref = evidence.source
        identity = (*ref.identity_key(), evidence.span.quoted_span_sha256)
        if identity in seen:
            continue
        seen.add(identity)
        if (
            not source_selected(policy, state.character_id, ref.source_store_id)
            or not (state.source_permitted(connection, ref, request.source_capability))
            or evidence.span.end_codepoint - evidence.span.start_codepoint > 2000
        ):
            partial = True
            continue
        try:
            quote = state.resolver.validate(evidence)
            if total_bytes + len(quote.encode("utf-8")) > 32768:
                partial = True
                continue
            record = state.resolver.resolve(ref)
        except MemoryError:
            partial = True
            continue
        total_bytes += len(quote.encode("utf-8"))
        result.append(
            SourceObservation(
                source=MemorySource(evidence=evidence, availability="available", quote=quote),
                role=record.actor.role if isinstance(record, Message) else "observation",
                conversation_id=record.conversation.record_id
                if isinstance(record, Message)
                else None,
                observed=record.created_time if isinstance(record, (Message, Document)) else None,
                recorded_at=record.recorded_at,
                branch_status=branch_status(state, record),
            )
        )
    return tuple(result), partial
