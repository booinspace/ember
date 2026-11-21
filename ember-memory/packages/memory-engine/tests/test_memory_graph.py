"""A bounded graph uses canonical memory and edge visibility, independent of prompt capacity."""

import asyncio
from uuid import uuid4

from ember_memory_abi.interfaces import ForgetRequest, RememberRequest
from ember_memory_abi.memory import Entity, EntityRef, EpisodePayload, MemoryDraft
from ember_memory_abi.related import MemoryReference, MemoryRelationship
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.consolidation import NativeAcceptanceService
from ember_memory_engine.memory_graph import GraphQuery
from ember_memory_engine.profile import ProfileSettings

from .conftest import SourceFixture


def test_graph_pages_expands_and_rechecks_visibility(
    engine: CanonicalEngine,
    store: SqliteMemoryStore,
    source_fixture: SourceFixture,
    draft: MemoryDraft,
) -> None:
    shared = EntityRef(store_id=store.character_id, entity_id=uuid4())
    engine.put_entity(
        store.character_id, Entity(reference=shared, revision=1, name="Synthetic lab")
    )
    store.configure_profile(ProfileSettings(subject_id=source_fixture.subject.entity_id))
    references = []
    for index in range(40):
        content = draft.content.model_copy(
            update={
                "kind": "episode",
                "payload": EpisodePayload(
                    event=f"Visited synthetic lab number {index}",
                    participants=(source_fixture.subject, shared)
                    if index < 2
                    else (source_fixture.subject,),
                ),
            }
        )
        references.append(
            store.remember(
                RememberRequest(
                    idempotency_key=str(index), draft=draft.model_copy(update={"content": content})
                )
            ).affected[0]
        )
    edge = MemoryRelationship(
        relationship_id=uuid4(),
        kind="supports",
        source=MemoryReference(memory_id=references[0].memory_id, revision=1),
        target=MemoryReference(memory_id=references[2].memory_id, revision=1),
        evidence=draft.content.evidence,
    )
    store.submit(
        lambda connection: NativeAcceptanceService.record_relationship(connection, edge),
        write=True,
    ).result()
    assertion = edge.evidence[0]
    inaccessible = assertion.evidence.model_copy(
        update={"source": source_fixture.evidence_ref.model_copy(update={"record_id": uuid4()})}
    )
    hidden_edge = edge.model_copy(
        update={
            "relationship_id": uuid4(),
            "target": MemoryReference(memory_id=references[3].memory_id, revision=1),
            "evidence": (assertion.model_copy(update={"evidence": inaccessible}),),
        }
    )
    store.submit(
        lambda connection: NativeAcceptanceService.record_relationship(connection, hidden_edge),
        write=True,
    ).result()

    async def run() -> None:
        client = InProcessAsyncMemoryClient(store)
        try:
            first = await client.memory_graph(GraphQuery())
            assert first.next_cursor is not None
            second = await client.memory_graph(GraphQuery(cursor=first.next_cursor))
            first_ids = {node.id for node in first.nodes if node.category == "memory"}
            second_ids = {node.id for node in second.nodes if node.category == "memory"}
            assert len(first_ids) == 36 and len(second_ids) == 4
            assert first_ids.isdisjoint(second_ids) and second.next_cursor is None
            assert all(link.target != references[3].memory_id for link in second.links)
            filtered = await client.memory_graph(GraphQuery(text="visited", kind="episode"))
            assert len([node for node in filtered.nodes if node.category == "memory"]) == 36
            query = GraphQuery(anchor=references[0].memory_id)
            focused = await client.memory_graph(query)
            assert focused.nodes[0].id == references[0].memory_id
            assert {node.id for node in focused.nodes} == {
                *(reference.memory_id for reference in references[:3]),
                shared.entity_id,
                source_fixture.subject.entity_id,
            }
            assert {link.kind for link in focused.links} == {"entity", "supports"}
            serialized = focused.model_dump_json()
            assert str(source_fixture.evidence_ref.record_id) not in serialized
            assert "source_store_id" not in serialized
            store.forget(
                ForgetRequest(
                    idempotency_key="forget-graph",
                    memory_id=references[1].memory_id,
                    expected_revision=1,
                    reason="synthetic graph fixture",
                )
            )
            visible = await client.memory_graph(query)
            assert all(node.id != references[1].memory_id for node in visible.nodes)
            assert all(link.source != references[1].memory_id for link in visible.links)
            engine.revoke_source(store.character_id, source_fixture.evidence_ref.source_store_id)
            assert not (await client.memory_graph(query)).nodes
        finally:
            await client.aclose()

    asyncio.run(run())
