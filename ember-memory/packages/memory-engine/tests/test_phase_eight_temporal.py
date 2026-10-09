"""Historical query certificates retain selectors while enforcing today's permission policy."""

from ember_memory_abi.interfaces import (
    ContextRequest,
    DisableRequest,
    MemoryQuery,
    RememberRequest,
    TemporalQuery,
)
from ember_memory_abi.memory import EpisodePayload, MemoryDraft
from ember_memory_abi.related import RelatedRequest
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine.character import SqliteMemoryStore

from .conftest import TestClock


def test_ended_episode_recall_and_historical_delivery(
    store: SqliteMemoryStore, draft: MemoryDraft
) -> None:
    event = draft.model_copy(
        update={
            "content": draft.content.model_copy(
                update={
                    "kind": "episode",
                    "payload": EpisodePayload(event="Synthetic device repair"),
                }
            ),
            "temporal": TemporalBounds(
                valid_from="2026-06-01T00:00:00.000000Z",
                valid_until="2026-07-01T00:00:00.000000Z",
                observed_at="2026-08-01T00:00:00.000000Z",
                basis="explicit_event_interval",
            ),
        }
    )
    identity = store.remember(RememberRequest(idempotency_key="event", draft=event)).affected[0]
    assert store.search(MemoryQuery()).results
    selected = store.retrieve_context(
        ContextRequest(
            memory_query=MemoryQuery(
                mode="structured",
                temporal=TemporalQuery(
                    valid_from="2026-06-01T00:00:00.000000Z",
                    valid_until="2026-09-01T00:00:00.000000Z",
                ),
            ),
            include_pinned=False,
            memory_token_budget=4096,
        )
    )
    assert len(selected.memories) == 1
    certificate = selected.fast_dependencies
    assert certificate is not None and len(certificate.selections) == 1
    assert certificate.valid_until is None
    assert store.lookup_related(RelatedRequest(intent="validate", dependencies=certificate)).valid
    store.disable(
        DisableRequest(
            idempotency_key="disable-event",
            memory_id=identity.memory_id,
            expected_revision=1,
            reason="Synthetic test",
        )
    )
    assert not store.lookup_related(
        RelatedRequest(intent="validate", dependencies=certificate)
    ).valid


def test_historical_state_and_recorded_revision(
    store: SqliteMemoryStore, draft: MemoryDraft, clock: TestClock
) -> None:
    from ember_memory_abi.interfaces import UpdateRequest

    past = draft.model_copy(
        update={
            "temporal": TemporalBounds(
                valid_from="2026-06-01T00:00:00.000000Z",
                valid_until="2026-07-01T00:00:00.000000Z",
                observed_at="2026-06-20T00:00:00.000000Z",
                basis="explicit_event_interval",
            )
        }
    )
    identity = store.remember(RememberRequest(idempotency_key="past", draft=past)).affected[0]
    assert not store.search(MemoryQuery()).results
    selected = store.retrieve_context(
        ContextRequest(
            memory_query=MemoryQuery(
                mode="structured",
                temporal=TemporalQuery(
                    valid_from="2026-06-01T00:00:00.000000Z",
                    valid_until="2026-07-01T00:00:00.000000Z",
                ),
            ),
            include_pinned=False,
            memory_token_budget=4096,
        )
    )
    assert selected.memories
    assert store.lookup_related(
        RelatedRequest(
            intent="validate",
            dependencies=selected.fast_dependencies,
        )
    ).valid
    clock.advance()
    cutoff = "2026-10-07T12:00:01.000000Z"
    clock.advance()
    store.update(
        UpdateRequest(
            idempotency_key="new-revision",
            memory_id=identity.memory_id,
            expected_revision=1,
            draft=draft,
            reason="Synthetic correction",
        )
    )
    history = store.retrieve_context(
        ContextRequest(
            memory_query=MemoryQuery(
                mode="structured", temporal=TemporalQuery(recorded_before=cutoff)
            ),
            include_pinned=False,
            memory_token_budget=4096,
        )
    )
    assert history.memories[0].memory.revision == 1
    assert store.lookup_related(
        RelatedRequest(
            intent="validate",
            dependencies=history.fast_dependencies,
        )
    ).valid
