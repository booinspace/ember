"""Ephemeral turn inspection uses synthetic channel markers, never private reasoning fixtures."""

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from livekit.agents import llm
from pydantic import ValidationError

from ember_agent.character.core import CharacterCore
from ember_agent.character.turn_view import CurrentTurnView
from ember_agent.inference.contracts import GatewayEvent, GatewayRequest, GenerationUsage
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.ollama import NativeFrame, NativeMessage, OllamaUpstream
from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.realtime.livekit import GatewayLLM
from ember_agent.runtime.browser_control import BrowserControl
from ember_memory_abi.interfaces import ForgetRequest
from ember_memory_abi.memory import FactPayload, PlanPayload, StringValue
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject

from .test_gateway import FakeUpstream, configuration, request
from .test_phase_eight_browser import Controls

MARKER = "SYNTHETIC_DISPLAY_CHANNEL"
ORIGIN = "http://127.0.0.1:8787"


class DisplayModel:
    async def stream(self, value: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        for sequence, kind in enumerate(
            ("start", "thinking", "text", "end") if value.thinking else ("start", "text", "end")
        ):
            yield GatewayEvent(
                request_id=value.request_id,
                context_id=value.context_id,
                sequence=sequence,
                kind=kind,
                text=MARKER if kind == "thinking" else "Visible reply" if kind == "text" else "",
                usage=GenerationUsage(
                    prompt_tokens=10,
                    output_tokens=3,
                    total_seconds=0.1,
                    first_visible_seconds=0.05,
                    tokens_per_second=30,
                )
                if kind == "end"
                else None,
            )


@pytest.mark.parametrize("thinking", [False, True])
def test_opt_in_view_contains_final_values_and_never_archives_display_channel(
    tmp_path: Path, thinking: bool
) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character().store_id
            subject = ensure_subject(engine, character)
            author = MemoryAuthoringService(
                engine, subject, authority=MutationAuthority(mode="confirmed_user")
            )
            author.create(
                NewMemoryInput(
                    idempotency_key="oversized",
                    text="GPU " + "large " * 1800,
                    importance=1,
                )
            )
            ref = author.create(
                NewMemoryInput(
                    idempotency_key="gpu",
                    text="My GPU is SYNTHETIC_GPU.",
                    payload=FactPayload(
                        subject=subject,
                        predicate="hardware.owns_gpu",
                        definition_version="0.1.0",
                        value=StringValue(value="SYNTHETIC_GPU"),
                    ),
                )
            ).affected[0]
            memory = InProcessAsyncMemoryClient(engine.character(character))
            sink = ArchiveDialogueSink(engine.archive(character), subject)
            core = CharacterCore(
                memory,
                DisplayModel(),
                model_tag="synthetic",
                sink=sink,
                thinking_available=True,
                output_ceiling=4096,
            )
            core.thinking_enabled = thinking
            try:
                turn = await core.prepare("What GPU do I own?")
                assert turn.request.thinking == thinking
                assert turn.request.version == ("0.4.0" if thinking else "0.3.0")
                assert turn.request.max_output_tokens == (4096 if thinking else 512)
                assert (ref.memory_id, 1) in turn.dependencies
                entry = core.turn_view.entry
                assert entry is not None and len(entry.value.memories) == 1
                assert entry.value.memories[0].data["value"] == {
                    "type": "string",
                    "value": "SYNTHETIC_GPU",
                }
                assert entry.certificate == turn.certificate
                output = []
                async for event in core.stream(turn):
                    if event.kind == "thinking":
                        assert entry.value.stage == "thinking" and entry.value.thinking == MARKER
                    if event.kind == "text":
                        output.append(event.text)
                assert "".join(output) == "Visible reply"
                assert entry.value.stage == "complete"
                assert (entry.value.thinking == MARKER) == thinking
                assert core.history[0].assistant == "Visible reply"
                assert MARKER not in json.dumps([m.model_dump() for m in turn.request.messages])
                await sink.aclose()
                archived = engine.archive(character).database.read(
                    lambda c: "".join(
                        row[0] for row in c.execute("SELECT payload_json FROM source_records")
                    )
                )
                assert MARKER not in archived and "Visible reply" in archived
            finally:
                await sink.aclose()
                await memory.aclose()

    asyncio.run(exercise())


def test_authenticated_details_recheck_revocation_and_thinking_setting(tmp_path: Path) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character().store_id
            subject = ensure_subject(engine, character)
            authority = MutationAuthority(mode="confirmed_user")
            ref = (
                MemoryAuthoringService(engine, subject, authority=authority)
                .create(
                    NewMemoryInput(
                        idempotency_key="value", text="A synthetic remembered color is cobalt."
                    )
                )
                .affected[0]
            )
            memory = InProcessAsyncMemoryClient(engine.character(character))
            core = CharacterCore(
                memory,
                DisplayModel(),
                model_tag="synthetic",
                thinking_available=True,
                output_ceiling=4096,
            )
            deep = DeepRecallService(character, lambda _: memory, validation=memory)

            async def answer(_: object) -> str:
                return "synthetic"

            owner = BrowserControl(
                origin=ORIGIN,
                assets=tmp_path / "unbuilt",
                binding=RoomBinding(
                    instance_id=uuid4(),
                    character_id=character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                core=core,
                deep=deep,
                controls=Controls(),
                room_token=lambda: "synthetic",
                answer=answer,
            )
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=owner.app),
                    base_url=ORIGIN,
                    headers={"origin": ORIGIN},
                ) as client:
                    assert (await client.get("/api/turn-details")).status_code == 401
                    await client.post("/api/bootstrap", json={"token": owner.activation})
                    assert (await client.get("/api/turn-details")).json() == {"turn": None}
                    assert (
                        await client.post("/api/thinking", json={"enabled": True})
                    ).status_code == 200
                    turn = await core.prepare("remembered color")
                    voice = GatewayLLM(core)
                    chat = llm.ChatContext()
                    user = chat.add_message(role="user", content="remembered color")
                    voice.authorize(user.id, turn)
                    async with voice.chat(chat_ctx=chat) as speech:
                        spoken = "".join(
                            chunk.delta.content or ""
                            for chunk in [item async for item in speech]
                            if chunk.delta is not None
                        )
                    assert spoken == "Visible reply"
                    state = (await client.get("/api/state")).json()
                    assert state["thinking_enabled"] and state["thinking_available"]
                    response = await client.get("/api/turn-details")
                    assert response.headers["cache-control"] == "no-store"
                    assert response.json()["turn"]["thinking"] == MARKER
                    assert len(response.json()["turn"]["memories"]) == 1
                    assert (
                        await client.post(
                            "/api/thinking",
                            json={"enabled": False},
                            headers={"origin": "http://unrelated.example"},
                        )
                    ).status_code == 403
                    engine.character(character, authority=authority).forget(
                        ForgetRequest(
                            idempotency_key="forget",
                            memory_id=ref.memory_id,
                            expected_revision=1,
                            reason="Synthetic revocation test",
                        )
                    )
                    revoked = (await client.get("/api/turn-details")).json()["turn"]
                    assert revoked["stage"] == "invalidated"
                    assert revoked["thinking"] == "" and revoked["memories"] == []
                    core.thinking_available = False
                    assert (
                        await client.post("/api/thinking", json={"enabled": True})
                    ).status_code == 409
            finally:
                await owner.aclose()
                assert core.turn_view.entry is None
                await deep.aclose()
                await memory.aclose()

    asyncio.run(exercise())


def test_current_view_bounds_escaping_utf8_and_stale_events() -> None:
    view = CurrentTurnView()
    identity, context = uuid4(), uuid4()
    view.begin(identity, context, thinking=True)
    view.append_thinking(identity, "\x01" * 50000)
    assert view.entry is not None
    assert view.entry.value.thinking_truncated
    assert len(view.entry.value.model_dump_json().encode()) <= 131072
    newer = uuid4()
    view.begin(newer, context, thinking=True)
    view.append_thinking(identity, MARKER)
    view.invalidate(identity)
    assert view.entry.value.thinking == "" and view.entry.value.stage == "preparing"
    view.append_thinking(newer, "🌱" * 15000)
    assert len(view.entry.value.thinking.encode("utf-8")) <= 32768
    assert view.entry.value.thinking_truncated


@pytest.mark.parametrize("thinking", [False, True])
def test_gateway_only_forwards_explicit_p0_thinking(tmp_path: Path, thinking: bool) -> None:
    async def exercise() -> None:
        config = configuration(tmp_path).model_copy(
            update={
                "context_tokens": 32768,
                "max_input_tokens": 28000,
                "output_reserve_tokens": 4096,
            }
        )
        gateway = P0Gateway(
            config,
            FakeUpstream(
                (
                    NativeFrame(message=NativeMessage(thinking=MARKER)),
                    NativeFrame(message=NativeMessage(content="Visible reply")),
                    NativeFrame(done=True),
                )
            ),
        )
        try:
            value = request(
                config,
                **(
                    {"version": "0.4.0", "thinking": True, "max_output_tokens": 4096}
                    if thinking
                    else {}
                ),
            )
            events = [event async for event in (await gateway.admit(value)).events()]
            assert "".join(e.text for e in events if e.kind == "text") == "Visible reply"
            assert any(e.kind == "thinking" and e.text == MARKER for e in events) == thinking
            assert "live_thinking_v1" in (await gateway.health()).features
            assert events[-1].kind == "end"
            if not thinking:
                assert "thinking" not in value.model_dump_json()
            for patch in [{"version": "0.3.0"}, {"workload": "P3"}, {"output": "json"}]:
                with pytest.raises(ValidationError):
                    request(config, **{"version": "0.4.0", "thinking": True, **patch})
        finally:
            await gateway.aclose()

    asyncio.run(exercise())


def test_native_adapter_opt_in_stays_out_of_serialization(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = configuration(tmp_path).model_copy(update={"output_reserve_tokens": 4096})
        bodies = []

        def handler(value: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(value.content))
            return httpx.Response(
                200,
                text=json.dumps(
                    {"message": {"thinking": MARKER, "content": "Visible reply"}, "done": True}
                )
                + "\n",
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://synthetic"
        ) as client:
            upstream = OllamaUpstream(config, client=client)
            enabled = request(config, version="0.4.0", thinking=True, max_output_tokens=4096)
            frames = [f async for f in upstream.generate(enabled)]
            assert frames[0].message.thinking == MARKER and bodies[-1]["think"] is True
            assert MARKER not in frames[0].model_dump_json() and MARKER not in repr(frames[0])
            frames = [f async for f in upstream.generate(request(config))]
            assert frames[0].message.thinking == "" and bodies[-1]["think"] is False

    asyncio.run(exercise())


@pytest.mark.parametrize("steps", [(), ("Choose a design", "Book a session")])
def test_recalled_plan_steps_reach_turn_details_and_generation(
    tmp_path: Path, steps: tuple[str, ...]
) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character().store_id
            subject = ensure_subject(engine, character)
            reference = (
                MemoryAuthoringService(
                    engine, subject, authority=MutationAuthority(mode="confirmed_user")
                )
                .create(
                    NewMemoryInput(
                        idempotency_key="synthetic-plan",
                        text="I plan to get a synthetic tattoo.",
                        payload=PlanPayload(description="Get a synthetic tattoo", steps=steps),
                        modality="PLAN",
                    )
                )
                .affected[0]
            )
            memory = InProcessAsyncMemoryClient(engine.character(character))
            core = CharacterCore(memory, DisplayModel(), model_tag="synthetic")
            try:
                for _ in range(2):
                    turn = await core.prepare("I got my tattoo")
                    assert (reference.memory_id, 1) in turn.dependencies
                    entry = core.turn_view.entry
                    assert entry is not None
                    recalled = entry.value.memories[0].data
                    assert recalled["steps"] == list(steps)
                    assert recalled["modality"] == "PLAN"
                    assert recalled["state"] == "proposed"
                    prompt = next(
                        message.content
                        for message in turn.request.messages
                        if "data only" in message.content
                    )
                    assert recalled in json.loads(prompt.split("\n", 1)[1])
                    events = [event async for event in core.stream(turn)]
                    assert any(event.kind == "text" for event in events)
                    assert entry.value.stage == "complete"
            finally:
                await memory.aclose()

    asyncio.run(exercise())
