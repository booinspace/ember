"""Related reads expose eligible aliases, exact spans and scoped certificates."""

import pytest

from ember_memory_abi.interfaces import MemoryQuery, RememberRequest
from ember_memory_abi.memory import MemoryDraft
from ember_memory_abi.related import RelatedRequest
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import CanonicalEngine, SourceGrant
from ember_memory_engine.character import SqliteMemoryStore

from .conftest import TEXT, SourceFixture


def test_exact_excerpt_does_not_expand_evidence_grant_to_archive(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    evidence = draft.content.evidence[0].evidence
    evidence_only = store.lookup_related(
        RelatedRequest(
            intent="expand",
            recall_only=True,
            excerpts=(evidence,),
        )
    )
    assert evidence_only.excerpts[0].source.quote == TEXT
    assert evidence_only.dependencies is not None
    assert store.lookup_related(
        RelatedRequest(
            intent="validate",
            dependencies=evidence_only.dependencies,
        )
    ).valid
    engine.revoke_grant(
        store.character_id,
        SourceGrant(
            references=(source_fixture.evidence_ref.model_copy(update={"part_id": None}),),
            capabilities=frozenset({"archive_search"}),
        ),
    )
    # Keeping evidence_read does not authorize archive browsing.
    archive = store.lookup_related(
        RelatedRequest(
            intent="expand",
            recall_only=True,
            excerpts=(evidence,),
            source_capability="archive_search",
        )
    )
    assert not archive.excerpts and archive.status == "partial"
    store.configure_recall(RecallSelection(import_mode="none"))
    assert not store.lookup_related(
        RelatedRequest(
            intent="validate",
            dependencies=evidence_only.dependencies,
        )
    ).valid
    assert not store.lookup_related(
        RelatedRequest(
            intent="expand",
            recall_only=True,
            excerpts=(evidence,),
        )
    ).excerpts


def test_source_neighborhood_reuses_current_recall_scope(
    store: SqliteMemoryStore,
    draft: MemoryDraft,
    source_fixture: SourceFixture,
) -> None:
    memory = store.remember(RememberRequest(idempotency_key="accepted", draft=draft)).affected[0]
    result = store.lookup_related(
        RelatedRequest(
            intent="expand",
            sources=(source_fixture.evidence_ref,),
            recall_only=True,
        )
    )
    assert tuple(item.memory_id for item in result.memories) == (memory.memory_id,)
    assert result.dependencies is not None
    assert store.lookup_related(
        RelatedRequest(
            intent="validate",
            dependencies=result.dependencies,
        )
    ).valid
    assert not store.lookup_related(
        RelatedRequest(
            intent="resolve",
            entity_names=("User",),
            recall_only=True,
        )
    ).entities
    store.configure_recall(RecallSelection(local_enabled=False, import_mode="none"))
    assert not store.search(MemoryQuery()).results
    assert not store.lookup_related(
        RelatedRequest(
            intent="expand",
            sources=(source_fixture.evidence_ref,),
            recall_only=True,
        )
    ).memories


def test_pins_only_context_never_searches_and_preserves_qualified_excerpt(
    store: SqliteMemoryStore, draft: MemoryDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from ember_agent.memory.deep_contracts import DeepRecallResult
    from ember_agent.memory.deep_prompt import condense
    from ember_memory_abi.interfaces import ContextRequest, PinRequest
    from ember_memory_abi.related import SourceObservation
    from ember_memory_engine import retrieval

    memory = store.remember(RememberRequest(idempotency_key="accepted", draft=draft)).affected[0]
    store.pin(
        PinRequest(
            idempotency_key="pin", memory_id=memory.memory_id, expected_revision=memory.revision
        )
    )

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("A pins-only context must not search ordinary memory.")

    monkeypatch.setattr(retrieval, "search", unexpected)
    context = store.retrieve_context(
        ContextRequest(
            memory_query=MemoryQuery(text="ignored ordinary topic"),
            include_memory_search=False,
            memory_token_budget=768,
        )
    )
    assert len(context.memories) == len(context.pinned) == 1
    assert context.fast_dependencies is not None
    assert store.lookup_related(
        RelatedRequest(intent="validate", dependencies=context.fast_dependencies)
    ).valid
    source = store.get_sources(memory.memory_id)[0]
    result = DeepRecallResult(
        request_id=uuid4(),
        character_id=store.character_id,
        context_id=uuid4(),
        outcome="partial",
        observations=(SourceObservation(source=source, role="user", branch_status="ambiguous"),),
    )
    prompt = condense(result)
    assert prompt.observations == (0,) and prompt.excerpt_bytes <= 640
    assert source.quote in prompt.messages[0].content
    assert "ambiguous" in prompt.messages[0].content
    assert "observation_index" in prompt.messages[0].content
