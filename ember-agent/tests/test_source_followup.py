"""Source follow-ups use delivered memory evidence, not archive ranking or the model."""

import asyncio
from pathlib import Path
from statistics import quantiles
from time import perf_counter
from unittest.mock import patch

import pytest

from ember_agent.character.core import CharacterCore
from ember_agent.character.recall_intent import source_followup
from ember_agent.inference.contracts import GatewayError
from ember_memory_abi.interfaces import ForgetRequest
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject

from .test_character import FakeModel

SOURCE = "Synthetic GPU preference: 3090 — exact original text."
FOLLOWUP = "What was the original source of that memory?"


@pytest.mark.parametrize(
    "question", [FOLLOWUP, "Where did I say that?", "Show me the original message."]
)
def test_source_followup_intent(question: str) -> None:
    assert source_followup(question)
    assert not source_followup("What was the original source of coffee?")
    assert not source_followup("/recall where did I say that?")


@pytest.mark.parametrize(
    "case", ["verified", "ambiguous", "missing", "interrupted", "forgotten", "deselected", "race"]
)
def test_source_followup_delivery(tmp_path: Path, case: str) -> None:
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
            model = FakeModel()
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
                turn = await core.prepare(FOLLOWUP)
                assert turn.local_reply is not None
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
                assert len(model.requests) == (0 if case == "missing" else 1)
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
                        with patch(
                            "ember_agent.character.core.source_followup",
                            side_effect=source_followup if enabled else lambda _: False,
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
