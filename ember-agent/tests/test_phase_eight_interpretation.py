"""An opt-in schema-bound P0 proposal cannot invent dates, authority or another planning call."""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from ember_agent.character.core import CharacterCore
from ember_agent.character.interpretation import CalendarInterpreter
from ember_agent.character.temporal import CalendarResolver
from ember_agent.inference.contracts import GatewayEvent, GatewayRequest, GenerationUsage
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

NOW = datetime(2026, 10, 9, tzinfo=UTC)


class ProposalModel:
    def __init__(self, proposal: dict[str, object], *, block: bool = False) -> None:
        self.proposal = proposal
        self.block = block
        self.calls: list[GatewayRequest] = []

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        self.calls.append(request)
        assert request.workload == "P0" and not request.tools
        assert request.output == "json" and request.max_output_tokens == 256
        if self.block:
            await asyncio.Event().wait()
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=0,
            kind="text",
            text=json.dumps(self.proposal),
        )
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=1,
            kind="end",
            usage=GenerationUsage(
                prompt_tokens=1,
                output_tokens=1,
                total_seconds=0.01,
                first_visible_seconds=0.005,
                tokens_per_second=100.0,
            ),
        )


def test_valid_relative_proposal_retains_original_topic_and_one_call() -> None:
    async def exercise() -> None:
        model = ProposalModel(
            {
                "status": "resolved",
                "support_quote": "summer of the previous year",
                "calendar_expression": "summer 2025",
                "axis": "event",
            }
        )
        interpreter = CalendarInterpreter(model, "synthetic")
        result = await interpreter.resolve(
            "NAS problems from summer of the previous year", NOW, None, CalendarResolver(), uuid4()
        )
        assert result.status == "resolved"
        assert result.query.text == "nas problems"
        assert result.query.temporal.valid_from == "2025-05-31T23:00:00.000000Z"
        assert interpreter.calls == 1 and len(model.calls) == 1
        assert interpreter.last_latency_seconds is not None

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "change",
    (
        {"calendar_expression": "2025-07-15"},
        {"axis": "recorded"},
        {"sql": "select * from secret"},
        {"calendar_expression": "summer 2030"},
        {"support_quote": "not in the input"},
    ),
)
def test_unsupported_days_axes_authority_and_sources_fail_closed(change: dict[str, object]) -> None:
    async def exercise() -> None:
        proposal = {
            "status": "resolved",
            "support_quote": "summer of the previous year",
            "calendar_expression": "summer 2025",
            "axis": "event",
        } | change
        interpreter = CalendarInterpreter(ProposalModel(proposal), "synthetic")
        result = await interpreter.resolve(
            "NAS from summer of the previous year", NOW, None, CalendarResolver(), uuid4()
        )
        assert result.status == "clarification"
        assert interpreter.calls == 1

    asyncio.run(exercise())


def test_timeout_and_cancellation_have_no_retry() -> None:
    async def exercise() -> None:
        model = ProposalModel({}, block=True)
        interpreter = CalendarInterpreter(model, "synthetic", timeout_seconds=0.02)
        result = await interpreter.resolve(
            "previous summer", NOW, None, CalendarResolver(), uuid4()
        )
        assert result.reasons == ("interpretation_timeout",) and len(model.calls) == 1
        request = asyncio.create_task(
            interpreter.resolve("previous summer", NOW, None, CalendarResolver(), uuid4())
        )
        await asyncio.sleep(0)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert interpreter.last_outcome == "cancelled" and interpreter.calls == 2

    asyncio.run(exercise())


def test_common_scopes_never_invoke_optional_interpreter(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        client = InProcessAsyncMemoryClient(engine.character(character))

        async def exercise() -> None:
            model = ProposalModel({})
            interpreter = CalendarInterpreter(model, "synthetic")
            core = CharacterCore(
                client,
                model,
                model_tag="synthetic",
                interpreter=interpreter,
                reference_clock=lambda: NOW,
            )
            await core.prepare("memories from summer 2026")
            assert interpreter.calls == 0 and not model.calls
            await client.aclose()

        asyncio.run(exercise())


def test_ambiguous_and_anchor_requests_clarify_without_retrieval_or_final_inference(
    tmp_path: Path,
) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        client = InProcessAsyncMemoryClient(engine.character(character))

        async def exercise() -> None:
            model = ProposalModel({})
            core = CharacterCore(
                client,
                model,
                model_tag="synthetic",
                reference_clock=lambda: NOW,
            )
            await core.prepare("memories summer 2026")
            assert core.last_scope is not None
            for question, expected in (
                ("summer 26", "four-digit year"),
                ("during that same period", "Which earlier period"),
                ("what did you believe before the correction?", "event or device period"),
            ):
                turn = await core.prepare(question)
                assert turn.local_reply is not None and turn.retained_memories == 0
                text = "".join([event.text async for event in core.stream(turn)])
                assert expected in text
                assert not model.calls and core.last_scope is None
            await client.aclose()

        asyncio.run(exercise())
