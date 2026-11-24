"""Local intent routes to delivered memory evidence, never model-authored quotes."""

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from statistics import quantiles
from time import perf_counter
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
import pytest

from ember_agent.character.core import CharacterCore
from ember_agent.character.recall_intent import source_followup, source_intent_candidate
from ember_agent.inference.contracts import (
    GatewayError,
    GatewayEvent,
    GatewayRequest,
    GenerationUsage,
    ToolCall,
)
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.runtime.browser_control import BrowserControl
from ember_agent.runtime.text_session import DirectTextSession
from ember_memory_abi.interfaces import ForgetRequest
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject

from .test_character import FakeModel

SOURCE = "Synthetic GPU preference: 3090 — exact original text."
FOLLOWUP = "What was the original source of that memory?"
PARAPHRASE = "Can you get the raw source of that memory?"
POSITIVE = (
    PARAPHRASE,
    "Could you pull up the original wording of what I told you?",
    "What's the provenance for that recalled claim?",
    "Please cite what you just told me.",
)
NEAR_MISSES = (
    "Can you explain this source code?",
    "Don't quote the original message, just summarize it.",
    "Would this count as a source for a paper?",
    "Find public sources about that GPU.",
)


class RoutingModel(FakeModel):
    def __init__(self, result: str = "auto") -> None:
        super().__init__()
        self.result = result
        self.route_seconds: list[float] = []

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        if request.output != "json":
            async for event in super().stream(request):
                yield event
            return
        self.requests.append(request)
        started = perf_counter()
        fields = {"request_id": request.request_id, "context_id": request.context_id}
        assert request.workload == "P0" and not request.tools and not request.thinking
        assert request.output_schema and request.max_output_tokens == 48
        assert len(request.messages) == 2
        assert SOURCE not in request.model_dump_json()
        assert "Synthetic visible reply" not in request.model_dump_json()
        try:
            yield GatewayEvent(**fields, kind="start", sequence=0)
            if self.result == "timeout":
                await asyncio.Future()
            if self.result == "error":
                raise GatewayError("gateway_busy")
            if self.result == "tools":
                yield GatewayEvent(
                    **fields,
                    kind="tools",
                    sequence=1,
                    calls=(ToolCall(call_id=uuid4(), name="evidence_read", arguments={}),),
                )
                return
            text = json.loads(request.messages[-1].content)
            intent = "source_followup" if text in POSITIVE else "other"
            raw = json.dumps({"intent": intent})
            if self.result == "oversized":
                raw += " " * 257
            if self.result == "invalid":
                raw = '{"intent":"source_followup","source_id":"untrusted"}'
            yield GatewayEvent(**fields, kind="text", sequence=1, text=raw)
            if self.result == "incomplete":
                return
            yield GatewayEvent(
                **fields,
                kind="end",
                sequence=2,
                truncated=self.result == "truncated",
                usage=GenerationUsage(
                    prompt_tokens=32,
                    output_tokens=8,
                    total_seconds=0.001,
                    first_visible_seconds=0.001,
                    tokens_per_second=8000.0,
                ),
            )
        finally:
            self.route_seconds.append(perf_counter() - started)


@pytest.mark.parametrize(
    "question", [FOLLOWUP, "Where did I say that?", "Show me the original message."]
)
def test_source_followup_intent(question: str) -> None:
    assert source_followup(question)
    assert not source_followup("What was the original source of coffee?")
    assert not source_followup("/recall where did I say that?")


@pytest.mark.parametrize("question", [FOLLOWUP, PARAPHRASE])
@pytest.mark.parametrize(
    "case", ["verified", "ambiguous", "missing", "interrupted", "forgotten", "deselected", "race"]
)
def test_source_followup_delivery(tmp_path: Path, case: str, question: str) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        authority = MutationAuthority(mode="confirmed_user")
        author = MemoryAuthoringService(
            engine, ensure_subject(engine, character), authority=authority
        )
        memory = author.create(NewMemoryInput(idempotency_key="gpu", text=SOURCE)).affected[0]
        if case == "ambiguous":
            author.create(
                NewMemoryInput(idempotency_key="other", text="Synthetic GPU observation.")
            )

        async def run() -> None:
            client = InProcessAsyncMemoryClient(engine.character(character, authority=authority))
            model = RoutingModel()
            core = CharacterCore(client, model, model_tag="synthetic")

            async def forget() -> None:
                await client.forget(
                    ForgetRequest(
                        idempotency_key="forget",
                        memory_id=memory.memory_id,
                        expected_revision=memory.revision,
                        reason="user_request",
                    )
                )

            try:
                if case != "missing":
                    first = await core.prepare("GPU")
                    defer = case == "interrupted"
                    _ = [event async for event in core.stream(first, defer_delivery=defer)]
                    if defer:
                        await core.acknowledge(
                            first.request.request_id, "Synthetic", complete=False
                        )
                if case == "forgotten":
                    await forget()
                if case == "deselected":
                    selection = await client.recall_selection()
                    await client.configure_recall(
                        selection.model_copy(update={"local_enabled": False})
                    )
                turn = await core.prepare(question)
                assert turn.source_lookup and turn.local_reply is not None
                if question == PARAPHRASE:
                    assert not source_followup(question)
                    assert model.requests[-1].output == "json"
                    assert model.requests[-1].request_id != turn.request.request_id
                if case in {"verified", "race"}:
                    assert turn.local_reply.endswith("\n\n" + SOURCE)
                    assert turn.certificate is not None and turn.certificate.excerpt_sources
                elif case == "ambiguous":
                    assert turn.omissions == ("source_followup_ambiguous",)
                else:
                    assert turn.omissions == ("source_followup_unavailable",)
                    assert SOURCE not in turn.local_reply
                if case == "race":
                    output = "".join([e.text async for e in core.stream(turn, defer_delivery=True)])
                    await forget()
                    with pytest.raises(GatewayError, match="context_invalidated"):
                        await core.acknowledge(turn.request.request_id, output, complete=True)
                    assert core.history[-1].assistant == ""
                else:
                    assert "".join([e.text async for e in core.stream(turn)]) == turn.local_reply
                assert len(model.requests) == int(case != "missing") + int(question == PARAPHRASE)
            finally:
                await client.aclose()

        asyncio.run(run())


def test_source_followup_paired_replay(tmp_path: Path) -> None:
    """Compare the same chat turns with source routing off and on; no served model."""
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        author = MemoryAuthoringService(
            engine,
            ensure_subject(engine, character),
            authority=MutationAuthority(mode="confirmed_user"),
        )
        author.create(NewMemoryInput(idempotency_key="gpu", text=SOURCE))
        questions = (
            FOLLOWUP,
            "Where did I say that?",
            "Show me the original message.",
            "What's the supporting source?",
        )

        async def run() -> None:
            client = InProcessAsyncMemoryClient(engine.character(character))
            samples: dict[bool, list[float]] = {False: [], True: []}
            quotes = dict.fromkeys(samples, 0)
            try:
                for index in range(24):
                    for enabled in (bool(index % 2), not index % 2):
                        model = FakeModel()
                        core = CharacterCore(client, model, model_tag="synthetic")
                        first = await core.prepare("GPU")
                        _ = [event async for event in core.stream(first)]
                        started = perf_counter()
                        with (
                            patch(
                                "ember_agent.character.core.source_followup",
                                side_effect=source_followup if enabled else lambda _: False,
                            ),
                            patch(
                                "ember_agent.character.core.source_intent_candidate",
                                return_value=False,
                            ),
                        ):
                            followup = await core.prepare(questions[index % len(questions)])
                            output = "".join([e.text async for e in core.stream(followup)])
                        samples[enabled].append((perf_counter() - started) * 1000)
                        quotes[enabled] += output.endswith("\n\n" + SOURCE)
                        assert len(model.requests) == (1 if enabled else 2)
            finally:
                await client.aclose()
            for enabled, times in samples.items():
                p95 = quantiles(times, n=20, method="inclusive")[18]
                print(
                    f"source routing {enabled}: {quotes[enabled]}/{len(times)} exact, "
                    f"p95 {p95:.2f} ms"
                )
            assert quotes == {False: 0, True: 24}

        asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["error", "timeout", "invalid", "incomplete", "truncated", "tools", "oversized"]
)
def test_failed_source_proposal_never_asks_model_to_quote(tmp_path: Path, failure: str) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            client = InProcessAsyncMemoryClient(
                engine.character(engine.create_character().store_id)
            )
            model = RoutingModel(failure)
            core = CharacterCore(client, model, model_tag="synthetic")
            try:
                with (
                    patch("ember_agent.character.source_intent._TIMEOUT_SECONDS", 0.01),
                    patch.object(
                        client, "lookup_related", side_effect=AssertionError("lookup ran")
                    ),
                ):
                    turn = await core.prepare(PARAPHRASE)
                    output = "".join([event.text async for event in core.stream(turn)])
                assert turn.omissions == ("source_intent_unavailable",)
                assert FOLLOWUP in output and SOURCE not in output
                assert len(model.requests) == len(model.route_seconds) == 1
            finally:
                await client.aclose()

    asyncio.run(run())


def test_source_paraphrases_through_browser(tmp_path: Path) -> None:
    """Pair exact-only and proposed routing on identical acknowledged browser turns."""

    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character().store_id
            author = MemoryAuthoringService(
                engine,
                ensure_subject(engine, character),
                authority=MutationAuthority(mode="confirmed_user"),
            )
            author.create(NewMemoryInput(idempotency_key="gpu", text=SOURCE))
            memory = InProcessAsyncMemoryClient(engine.character(character))
            deep = DeepRecallService(character, lambda _: memory, validation=memory)
            try:
                for enabled in (False, True):
                    await browser_replay(tmp_path, memory, deep, enabled)
            finally:
                await deep.aclose()
                await memory.aclose()

    asyncio.run(run())


async def browser_replay(
    tmp_path: Path, memory: InProcessAsyncMemoryClient, deep: DeepRecallService, enabled: bool
) -> None:
    origin = "http://127.0.0.1:8787"
    model = RoutingModel()
    core = CharacterCore(memory, model, model_tag="synthetic")
    investigate = AsyncMock(side_effect=AssertionError("archive investigation ran"))
    session = DirectTextSession(core, deep, investigate, model_available=True)
    session.before_turn = AsyncMock()
    session.connection_changed = Mock()
    owner = BrowserControl(
        origin=origin,
        assets=tmp_path / "unbuilt",
        binding=RoomBinding(
            instance_id=uuid4(),
            character_id=memory.character_id,
            room="ember-" + uuid4().hex,
            endpoint="ws://127.0.0.1:7880",
        ),
        core=core,
        deep=deep,
        controls=Mock(),
        room_token=lambda: "unused",
        answer=AsyncMock(),
        text_session=session,
    )
    quotes = 0
    samples: list[float] = []
    try:
        with (
            patch(
                "ember_agent.character.core.source_intent_candidate",
                side_effect=source_intent_candidate if enabled else lambda _: False,
            ),
            patch.object(
                memory, "search_archive", side_effect=AssertionError("archive search ran")
            ),
            patch.object(memory, "lookup_related", wraps=memory.lookup_related) as lookup,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=owner.app),
                base_url=origin,
                headers={"origin": origin},
            ) as browser:
                assert (
                    await browser.post("/api/bootstrap", json={"token": owner.activation})
                ).is_success
                connected = await browser.post("/api/text/connect", json={})
                assert connected.is_success
                identity = connected.json()["session_id"]

                async def ask(question: str) -> str:
                    receipt = {"session_id": identity, "request_id": str(uuid4())}
                    reply = await browser.post("/api/text/turn", json={**receipt, "text": question})
                    assert reply.is_success, reply.text
                    assert (await browser.post("/api/text/acknowledge", json=receipt)).is_success
                    return reply.json()["text"]

                for question in (POSITIVE + NEAR_MISSES) * 3 + (FOLLOWUP, "How are you?"):
                    # Replay independent two-turn conversations, without prior quote context.
                    core.history.clear()
                    await ask("GPU")
                    assert core.history[-1].source_anchor is not None
                    model.requests.clear()
                    lookup.reset_mock()
                    started = perf_counter()
                    answer = await ask(question)
                    expected = question == FOLLOWUP or enabled and question in POSITIVE
                    assert answer.endswith("\n\n" + SOURCE) == expected
                    assert (
                        sum(bool(call.args[0].excerpts) for call in lookup.await_args_list)
                        == expected
                    )
                    requests = [request.output for request in model.requests]
                    if question in POSITIVE:
                        quotes += expected
                        samples.append((perf_counter() - started) * 1000)
                        assert requests == (["json"] if enabled else ["text"])
                    elif question in NEAR_MISSES:
                        assert requests == (["json", "text"] if enabled else ["text"])
                        assert answer == "Synthetic visible reply"
                    else:
                        assert requests == ([] if expected else ["text"])
    finally:
        await owner.aclose()
        await session.aclose()
    browser_p95 = quantiles(samples, n=20, method="inclusive")[18]
    route_ms = [value * 1000 for value in model.route_seconds]
    route_p95 = quantiles(route_ms, n=20, method="inclusive")[18] if route_ms else 0
    print(
        f"browser source proposal {enabled}: {quotes}/{len(samples)} exact, "
        f"turn+receipt p95 {browser_p95:.2f} ms, scripted proposal p95 {route_p95:.2f} ms"
    )
