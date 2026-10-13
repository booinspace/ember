"""Accepted beliefs and nearby raw observations retain separate grants and event semantics."""

import asyncio
from pathlib import Path
from uuid import uuid4

from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_agent.memory.deep_contracts import DeepRecallRequest
from ember_agent.memory.deep_recall import DeepRecallService
from ember_memory_abi.archive import Message
from ember_memory_abi.evidence import Evidence, EvidenceAssertion, make_evidence_span
from ember_memory_abi.interfaces import MemoryQuery, RememberRequest
from ember_memory_abi.memory import EpisodePayload, MemoryContent, MemoryDraft
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine import CanonicalEngine, SourceGrant
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import ensure_subject
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.read_budget import ReadBudget

from .conftest import STAMP, SourceFixture
from .test_phase_seven import AUTHORITY


def service(
    engine: CanonicalEngine, store: SqliteMemoryStore
) -> tuple[DeepRecallService, InProcessAsyncMemoryClient]:
    foreground = InProcessAsyncMemoryClient(store)

    def factory(deadline: float) -> InProcessAsyncMemoryClient:
        budget = ReadBudget(deadline=deadline)
        return InProcessAsyncMemoryClient(
            store, deep_sources=DeepSources(engine, budget), read_budget=budget
        )

    return DeepRecallService(store.character_id, factory, validation=foreground), foreground


def test_accepted_memory_expands_only_its_authorized_conversation(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        subject = ensure_subject(engine, character)
        store = engine.character(character, authority=AUTHORITY)
        sink = ArchiveDialogueSink(engine.archive(character), subject)

        async def exercise() -> None:
            context = uuid4()
            first = "I fixed a NAS networking issue using OPNsense."
            second = "NAS networking also needed a Tailscale MagicDNS adjustment."
            unrelated = "NAS networking in an independent incident had an AdGuard failure."
            await sink.begin_input(context, uuid4(), STAMP, first)
            await sink.begin_input(context, uuid4(), STAMP, second)
            await sink.begin_input(uuid4(), uuid4(), STAMP, unrelated)
            records = sink.archive.database.read(
                lambda c: tuple(
                    Message.model_validate_json(row[0])
                    for row in c.execute(
                        "SELECT payload_json FROM source_records WHERE record_kind='message'"
                    )
                )
            )
            source_message = next(m for m in records if m.parts[0].text == first)
            source = source_message.source.model_copy(
                update={"part_id": source_message.parts[0].part_id}
            )
            draft = MemoryDraft(
                content=MemoryContent(
                    kind="episode",
                    payload=EpisodePayload(event=first),
                    modality="FACTUAL_ASSERTION",
                    confidence=0.95,
                    importance=0.7,
                    evidence=(
                        EvidenceAssertion(
                            evidence=Evidence(
                                evidence_id=uuid4(),
                                source=source,
                                span=make_evidence_span(first, 0, len(first)),
                            ),
                            relation="supports",
                            modality="FACTUAL_ASSERTION",
                            authority="confirmed_user",
                            verdict="validated",
                        ),
                    ),
                ),
                temporal=TemporalBounds(),
            )
            accepted = store.remember(RememberRequest(idempotency_key="accepted", draft=draft))
            deep, foreground = service(engine, store)
            request = DeepRecallRequest(
                request_id=uuid4(),
                character_id=character,
                context_id=uuid4(),
                query="NAS networking",
                reference_at=STAMP,
            )
            try:
                result = await deep.investigate(request)
                assert result.outcome == "partial", result.reasons
                assert [item.memory.memory_id for item in result.memories] == [
                    accepted.affected[0].memory_id
                ]
                quotes = [item.source.quote for item in result.observations]
                assert first in quotes and second in quotes and unrelated not in quotes
                assert "archive_observations_are_not_accepted_beliefs" in result.reasons
                assert result.dependencies is not None and result.dependencies.archive_sources
                assert await deep.inspect(request.request_id, request.context_id) == result
                assert len(result.clusters) == 1 and result.clusters[0].strength == "independent"
                assert (
                    engine.character(engine.create_character().store_id)
                    .search(MemoryQuery())
                    .results
                    == ()
                )
            finally:
                await deep.aclose()
                await foreground.aclose()
                await sink.aclose()

        asyncio.run(exercise())


def test_archive_only_followup_requires_browsing_even_when_evidence_grant_remains(
    engine: CanonicalEngine, store: SqliteMemoryStore, source_fixture: SourceFixture
) -> None:
    async def exercise() -> None:
        deep, foreground = service(engine, store)
        request = DeepRecallRequest(
            request_id=uuid4(),
            character_id=store.character_id,
            context_id=uuid4(),
            query="primary GPU",
            reference_at=STAMP,
        )
        try:
            result = await deep.investigate(request)
            assert not result.memories and result.observations
            assert result.dependencies is not None and result.dependencies.archive_sources
            engine.revoke_grant(
                store.character_id,
                SourceGrant(
                    references=(source_fixture.message.source,),
                    capabilities=frozenset({"archive_search"}),
                ),
            )
            assert await deep.inspect(request.request_id, request.context_id) is None
            denied = await deep.investigate(request.model_copy(update={"request_id": uuid4()}))
            assert not denied.observations and not denied.memories
        finally:
            await deep.aclose()
            await foreground.aclose()

    asyncio.run(exercise())
