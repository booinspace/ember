"""Typed belief recall uses the authenticated text path, not archive or deep fallback."""

import asyncio
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
import pytest

from ember_agent.character.conversation import CompletedTurn
from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayError
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.runtime.browser_control import BrowserControl
from ember_agent.runtime.controls import RuntimeControls
from ember_agent.runtime.text_session import DirectTextSession
from ember_memory_abi.interfaces import (
    ContextRequest,
    PinRequest,
    RetrievalContext,
    SupersedeRequest,
)
from ember_memory_abi.memory import FactPayload, MemoryDraft, StringValue
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject

from .test_character import FakeModel


class _Clock:
    value = datetime(2026, 5, 4, 12, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value


def test_typed_beliefs_through_browser_and_acknowledgment(tmp_path: Path) -> None:
    async def exercise() -> None:
        clock = _Clock()
        with CanonicalEngine(tmp_path / "data", clock=clock) as engine:
            character = engine.create_character().store_id
            subject = ensure_subject(engine, character)
            author = MemoryAuthoringService(
                engine, subject, authority=MutationAuthority(mode="confirmed_user")
            )
            store = author.store
            payload = FactPayload(
                subject=subject,
                predicate="hardware.primary_gpu",
                definition_version="0.1.0",
                value=StringValue(value="SYNTHETIC_OLD"),
            )
            (old,) = author.create(
                NewMemoryInput(
                    idempotency_key="gpu",
                    text="My primary GPU changed from SYNTHETIC_OLD to SYNTHETIC_NEW.",
                    payload=payload,
                    temporal=TemporalBounds(
                        valid_from="2026-05-01T00:00:00.000000Z", basis="explicit_open_end"
                    ),
                )
            ).affected
            store.pin(
                PinRequest(idempotency_key="pin", memory_id=old.memory_id, expected_revision=1)
            )
            memory = store.get(old.memory_id)
            clock.value = datetime(2026, 6, 1, tzinfo=UTC)
            change = "2026-06-01T00:00:00.000000Z"
            _, new = store.supersede(
                SupersedeRequest(
                    idempotency_key="transition",
                    memory_id=old.memory_id,
                    expected_revision=old.revision,
                    previous_valid_until=change,
                    transition_evidence=memory.content.evidence,
                    replacement=MemoryDraft(
                        content=memory.content.model_copy(
                            update={
                                "payload": payload.model_copy(
                                    update={"value": StringValue(value="SYNTHETIC_NEW")}
                                )
                            }
                        ),
                        temporal=TemporalBounds(valid_from=change, basis="explicit_open_end"),
                    ),
                    reason="Explicit synthetic correction",
                )
            ).affected
            clock.value = datetime(2026, 7, 1, tzinfo=UTC)
            (decoy,) = author.create(
                NewMemoryInput(
                    idempotency_key="pin-decoy",
                    text="SYNTHETIC_DECOY appeared during GPU setup.",
                )
            ).affected
            store.pin(
                PinRequest(
                    idempotency_key="decoy-pin", memory_id=decoy.memory_id, expected_revision=1
                )
            )
            client = InProcessAsyncMemoryClient(store)
            model = FakeModel()
            core = CharacterCore(client, model, model_tag="synthetic", reference_clock=clock.now)
            core.history.append(CompletedTurn("raw SYNTHETIC_DECOY", "raw reply", ()))
            deep = DeepRecallService(character, lambda _: client, validation=client)
            investigate = AsyncMock(side_effect=AssertionError("typed request started deep recall"))
            text = DirectTextSession(core, deep, investigate, model_available=True)
            text.before_turn = AsyncMock()
            text.connection_changed = Mock()
            origin = "http://127.0.0.1:8787"
            owner = BrowserControl(
                origin=origin,
                assets=tmp_path / "unbuilt",
                binding=RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                core=core,
                deep=deep,
                controls=Mock(spec=RuntimeControls),
                room_token=lambda: "unused",
                answer=AsyncMock(),
                text_session=text,
            )
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=owner.app),
                    base_url=origin,
                    headers={"origin": origin},
                ) as browser:
                    assert (
                        await browser.post("/api/bootstrap", json={"token": owner.activation})
                    ).is_success
                    connected = await browser.post("/api/text/connect", json={})
                    session = connected.json()["session_id"]
                    for query, wanted, excluded, identity in (
                        ("/recall current: User primary GPU", "NEW", "OLD", new.memory_id),
                        ("history: User primary GPU in May 2026", "OLD", "NEW", old.memory_id),
                        (
                            "history: User recorded-before 2026-06-01",
                            "OLD",
                            "NEW",
                            old.memory_id,
                        ),
                    ):
                        receipt = {"session_id": session, "request_id": str(uuid4())}
                        reply = await browser.post(
                            "/api/text/turn", json={**receipt, "text": query}
                        )
                        assert reply.is_success, reply.text
                        assert reply.json()["retained_memories"] == 1
                        pending = core.pending_delivery
                        assert pending is not None and pending[0].retained_pins == 0
                        assert pending[0].dependencies[0][0] == identity
                        prompt = "\n".join(msg.content for msg in model.requests[-1].messages)
                        assert "SYNTHETIC_" + wanted in prompt
                        assert (
                            "SYNTHETIC_" + excluded not in prompt
                            and "SYNTHETIC_DECOY" not in prompt
                        )
                        assert (
                            await browser.post("/api/text/acknowledge", json=receipt)
                        ).is_success
                        assert core.history[-1].complete

                    for query in (
                        "history: User",
                        "history: User in June",
                        "current: absent subject",
                    ):
                        receipt = {"session_id": session, "request_id": str(uuid4())}
                        before = len(model.requests)
                        reply = await browser.post(
                            "/api/text/turn", json={**receipt, "text": query}
                        )
                        assert reply.is_success and reply.json()["retained_memories"] == 0
                        assert (
                            await browser.post("/api/text/acknowledge", json=receipt)
                        ).is_success
                        assert len(model.requests) == before

                    query = "current: User primary GPU"
                    retrieve = client.retrieve_context

                    async def delayed_retrieval(request: ContextRequest) -> RetrievalContext:
                        await asyncio.Event().wait()
                        return await retrieve(request)

                    with patch.object(client, "retrieve_context", delayed_retrieval):
                        timed_out = await core.prepare(query)
                    assert timed_out.omissions == ("typed_recall_timeout",)
                    assert timed_out.local_reply and not timed_out.dependencies
                    with patch.object(core, "memory_ceiling", 1):
                        trimmed = await core.prepare(query)
                    assert trimmed.local_reply and not trimmed.dependencies

                    async def revoke_after_retrieval(request: ContextRequest) -> RetrievalContext:
                        result = await retrieve(request)
                        enabled = store.recall_selection()
                        store.configure_recall(enabled.model_copy(update={"local_enabled": False}))
                        return result

                    with patch.object(client, "retrieve_context", revoke_after_retrieval):
                        with pytest.raises(GatewayError, match="context_invalidated"):
                            await core.prepare(query)
                    disabled = store.recall_selection()
                    store.configure_recall(disabled.model_copy(update={"local_enabled": True}))

                    turn = await core.prepare(query)
                    async with aclosing(core.stream(turn, defer_delivery=True)) as stream:
                        assert (await anext(stream)).kind == "start"
                        enabled = store.recall_selection()
                        store.configure_recall(enabled.model_copy(update={"local_enabled": False}))
                        with pytest.raises(GatewayError, match="context_invalidated"):
                            await anext(stream)
                    await core.acknowledge(turn.request.request_id, "", complete=False)
                    disabled = store.recall_selection()
                    store.configure_recall(disabled.model_copy(update={"local_enabled": True}))

                    receipt = {"session_id": session, "request_id": str(uuid4())}
                    reply = await browser.post("/api/text/turn", json={**receipt, "text": query})
                    assert reply.is_success
                    enabled = store.recall_selection()
                    store.configure_recall(enabled.model_copy(update={"local_enabled": False}))
                    assert (
                        await browser.post("/api/text/acknowledge", json=receipt)
                    ).status_code == 409
                    assert not core.history[-1].complete and not core.history[-1].assistant
                    assert core.pending_delivery is None
                    investigate.assert_not_called()
            finally:
                await owner.aclose()
                await deep.aclose()
                await client.aclose()

    asyncio.run(exercise())
