"""Exercise native capture through the same HTTP contracts without a GPU or private store."""

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import IDLE_NATIVE_CAPTURE_FEATURE, GatewayRequest
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.ollama import NativeFrame, NativeMessage
from ember_agent.inference.service import ServiceOwner, create_app
from ember_agent.runtime.composition import LocalRuntime
from ember_agent.runtime.controls import CaptureControl
from ember_agent.runtime.text_session import DirectTextSession
from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_engine.processing_contracts import DatedClaimBatch, DatedFactClaim
from ember_memory_engine.processing_gateway import GatewayExtractionModel

from .test_gateway import FakeUpstream, configuration

SOURCE = "Please call me Rówan."
UNCERTAIN = "Maybe call me Rówan."
CONFLICT = "Please call me Sól."


class NativeUpstream(FakeUpstream):
    def __init__(self) -> None:
        super().__init__()
        self.extracted: list[str] = []
        self.foreground: list[GatewayRequest] = []
        self.capture_started = asyncio.Event()
        self.capture_release = asyncio.Event()
        self.capture_release.set()

    async def generate(self, value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
        if value.workload != "P3":
            self.foreground.append(value)
            async for frame in super().generate(value):
                yield frame
            return
        message = json.loads(value.messages[-1].content)["text"]
        self.extracted.append(message)
        self.capture_started.set()
        await self.capture_release.wait()
        claims = ()
        if message in {SOURCE, UNCERTAIN, CONFLICT}:
            # Even a model that tries stripping uncertainty cannot bypass native policy.
            claims = (
                DatedFactClaim(
                    kind="fact",
                    statement=SOURCE,
                    quote=message.removeprefix("Maybe ") if message == UNCERTAIN else message,
                    quote_start=None,
                    predicate="user.preferred_name",
                    value="Sól" if message == CONFLICT else "Rówan",
                    entity_name=None,
                    modality="FACTUAL_ASSERTION",
                    confidence=0.99,
                    importance=0.8,
                    verdict="supported",
                    date=None,
                ),
            )
        yield NativeFrame(
            message=NativeMessage(content=DatedClaimBatch(claims=claims).model_dump_json())
        )
        yield NativeFrame(done=True, prompt_eval_count=550, eval_count=80)


async def until_idle(runtime: LocalRuntime) -> None:
    async with asyncio.timeout(5):
        while (await runtime.processing_status()).capture_status != "idle":
            await asyncio.sleep(0.01)


def test_http_capture_stages_during_next_reply_and_recalls_exactly_while_connected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        config = configuration(tmp_path).model_copy(
            update={
                "data_root": tmp_path / "data",
                "runtime_root": tmp_path / "runtime",
                "system_prompt_file": tmp_path / "prompts" / "system.md",
            }
        )
        owner = ServiceOwner(config.runtime_root)
        upstream = NativeUpstream()
        gateway = P0Gateway(
            config, upstream, record=owner.record, record_sessions=owner.record_sessions
        )
        endpoint = config.services.gateway.endpoint
        app = create_app(
            gateway,
            owner.token,
            background_token=owner.background_token,
            agent_token=owner.agent_token,
            capture_token=owner.capture_token,
        )

        def client() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=endpoint)

        # Keep the actual NDJSON client, model pin, scoped token and runtime capture wiring.
        def extraction(*args: object, **kwargs: object) -> GatewayExtractionModel:
            kwargs.setdefault("client", client() if "client" not in kwargs else kwargs["client"])
            return GatewayExtractionModel(*args, **kwargs)

        monkeypatch.setattr("ember_agent.runtime.composition.GatewayExtractionModel", extraction)
        runtime = LocalRuntime(config)
        await runtime.model.aclose()
        runtime.model = HttpGatewayClient(endpoint, owner.token, client=client())
        runtime.core.model = runtime.model
        text = DirectTextSession(
            runtime.core, runtime.deep, runtime.investigate, model_available=True
        )
        reservation = uuid4()

        async def reserve(identity: UUID, connected: bool) -> None:
            await runtime.model.session(identity, connected=connected)

        assert IDLE_NATIVE_CAPTURE_FEATURE in (await runtime.model.health()).features
        lifecycle = runtime.bind_capture(
            reservation,
            reserve,
            text.quiesce,
            idle=text.capture_idle,
            permit=runtime.model.capture_idle,
        )
        text.before_turn = lifecycle.before_turn
        text.connection_changed = lifecycle.connection_changed
        text.capture_fence = lifecycle.fence
        text.capture_wake = lifecycle.wake
        lifecycle.start()
        next_turn = None
        try:
            identity = await text.connect()
            first = await text.turn(identity, uuid4(), SOURCE)
            assert first.text == "visible"
            assert runtime.core.pending_delivery is not None
            assert not upstream.extracted
            upstream.capture_release.clear()
            await text.acknowledge(identity, first.request_id)
            async with asyncio.timeout(5):
                await upstream.capture_started.wait()
            assert upstream.extracted == [SOURCE]
            assert gateway.sessions == {reservation}

            next_turn = asyncio.create_task(text.turn(identity, uuid4(), UNCERTAIN))
            async with asyncio.timeout(5):
                while not gateway._foreground_waiters:
                    await asyncio.sleep(0.01)
            assert not next_turn.done()  # No native preemption.
            upstream.capture_release.set()
            second = await asyncio.wait_for(next_turn, 5)
            async with asyncio.timeout(5):
                while (await runtime.processing_status()).counts["paused"] != 1:
                    await asyncio.sleep(0.01)
            assert runtime.core.pending_delivery is not None
            assert upstream.extracted == [SOURCE]
            assert not (await runtime.memory.search(MemoryQuery())).results

            await text.acknowledge(identity, second.request_id)
            await until_idle(runtime)
            assert gateway.sessions == {reservation} and lifecycle.connected
            summary = await asyncio.to_thread(runtime.capture.summary)
            assert summary.accepted == 1 and summary.deferred == 1 and summary.complete == 2
            assert upstream.extracted == [SOURCE, UNCERTAIN]
            (result,) = (
                await runtime.memory.search(MemoryQuery(text="Rówan", mode="lexical"))
            ).results
            (evidence,) = await runtime.memory.get_sources(result.memory.memory_id)
            assert evidence.quote == SOURCE and evidence.availability == "available"
            assert evidence.evidence.span.end_byte == len(SOURCE.encode())
            assert evidence.evidence.span.end_codepoint == len(SOURCE)
            assert result.memory.origin is None

            conflict = await text.turn(identity, uuid4(), CONFLICT)
            await text.acknowledge(identity, conflict.request_id)
            await until_idle(runtime)
            summary = await asyncio.to_thread(runtime.capture.summary)
            assert summary.accepted == 1 and summary.deferred == 2 and summary.complete == 3

            settings = await runtime.capture_settings()
            await runtime.configure_capture(
                CaptureControl(enabled=False, revision=settings.revision)
            )
            third = await text.turn(identity, uuid4(), "current: User preferred name")
            assert third.retained_memories == 1
            assert third.text == "Unresolved conflict in recalled claims.\n\nvisible"
            prompt = "\n".join(message.content for message in upstream.foreground[-1].messages)
            assert "Rówan" in prompt and "unresolved_conflict" in prompt
            assert "Maybe" not in prompt and "Sól" not in prompt
            pending = runtime.core.pending_delivery
            assert pending is not None and pending[0].certificate is not None
            assert pending[0].certificate.current_conflicts[0].unresolved is True
            await text.acknowledge(identity, third.request_id)
            async with asyncio.timeout(5):
                while (await runtime.processing_status()).capture_status != "disabled":
                    await asyncio.sleep(0.01)
            assert upstream.extracted == [SOURCE, UNCERTAIN, CONFLICT]
            assert (await runtime.capture_settings()).enabled is False
        finally:
            upstream.capture_release.set()
            if next_turn is not None:
                await asyncio.gather(next_turn, return_exceptions=True)
            await text.aclose()
            await lifecycle.aclose()
            await runtime.aclose()
            runtime.close_workers()
            await gateway.aclose()
            owner.close()

    asyncio.run(exercise())
