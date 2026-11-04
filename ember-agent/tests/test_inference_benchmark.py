"""Benchmark measurements must preserve completion, release and metric provenance."""

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
import pytest
from livekit import rtc

from ember_agent.inference.benchmark import measure, summarize, synthetic_request
from ember_agent.inference.benchmark_voice import PacedOutput
from ember_agent.inference.contracts import (
    GatewayError,
    GatewayEvent,
    GatewayHealth,
    GatewayRequest,
)

from .test_character import FakeModel
from .test_gateway import FakeUpstream, configuration


class Client(FakeModel):
    def __init__(self) -> None:
        super().__init__()
        self.checks = 0
        self.uncertain = False

    async def health(self) -> GatewayHealth:
        self.checks += 1
        return GatewayHealth(
            status="quiescence_required"
            if self.uncertain
            else "busy"
            if self.checks < 3
            else "ready",
            model="synthetic",
            digest="a" * 64,
            context_tokens=8192,
            active_request_id=uuid4() if self.checks < 3 else None,
        )


def test_measure_complete_and_cancel_waits_for_release_before_probe(tmp_path: Path) -> None:
    async def exercise() -> None:
        client = Client()
        request = synthetic_request(configuration(tmp_path), 1, 64)
        complete = await measure(client, request)
        assert complete.outcome == "complete"
        assert complete.usage is not None and complete.usage.tokens_per_second == 30
        assert complete.first_visible_seconds is not None
        detached = await measure(client, request, cancel=True)
        assert detached.outcome == "consumer_detached_and_drained"
        assert detached.usage is None  # A detached stream did not receive native completion usage.
        assert detached.cancellation_to_release_seconds >= 0.04
        assert client.checks == 3
        assert len(client.requests) == 3
        assert client.requests[-1].request_id != request.request_id
        assert detached.next_p0_first_visible_seconds is not None
        client.uncertain = True
        failed = await measure(client, request, cancel=True)
        assert failed.outcome == "benchmark_release_unknown"
        assert len(client.requests) == 4  # Never probe unknown capacity.

    asyncio.run(exercise())


@pytest.mark.parametrize("probe", ["failed", "incomplete", "empty"])
def test_cancel_probe_failure_never_claims_next_turn_recovered(tmp_path: Path, probe: str) -> None:
    class ProbeFailure(Client):
        async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
            if not self.requests:
                async for event in super().stream(request):
                    yield event
                return
            self.requests.append(request)
            if probe == "failed":
                raise GatewayError("upstream_unavailable")
            if probe == "empty":
                async for event in FakeModel().stream(request):
                    if event.kind != "text":
                        yield event

    async def exercise() -> None:
        client = ProbeFailure()
        sample = await measure(
            client, synthetic_request(configuration(tmp_path), 1, 64), cancel=True
        )
        assert sample.outcome == "benchmark_next_p0_failed"
        assert (
            sample.next_p0_outcome
            == {
                "failed": "upstream_unavailable",
                "incomplete": "incomplete_stream",
                "empty": "complete",
            }[probe]
        )
        assert sample.cancellation_to_release_seconds is not None
        assert sample.next_p0_first_visible_seconds is None
        assert client.checks == 3 and len(client.requests) == 2

    asyncio.run(exercise())


def test_incomplete_and_failed_trials_are_not_successes(tmp_path: Path) -> None:
    class Broken(Client):
        async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
            yield GatewayEvent(
                request_id=request.request_id,
                context_id=request.context_id,
                sequence=0,
                kind="start",
            )

    class Failed(Broken):
        async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
            async for event in super().stream(request):
                yield event
            raise GatewayError("upstream_unavailable")

    async def exercise() -> None:
        request = synthetic_request(configuration(tmp_path), 0, 64)
        assert (await measure(Broken(), request)).outcome == "incomplete_stream"
        assert (await measure(Failed(), request)).outcome == "upstream_unavailable"
        assert summarize([]) is None
        summary = summarize([3, 1, 2])
        assert summary == {"samples": 3, "p50": 2, "p95": 3, "p99_exploratory": 3}

    asyncio.run(exercise())


def test_completed_text_without_pcm_is_a_failed_voice_trial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ember_agent.inference import benchmark_voice

    async def silent(text: AsyncGenerator[str, None], *args: object, **kwargs: object) -> tuple:
        async for _ in text:
            pass
        return None, None, None

    monkeypatch.setattr(benchmark_voice, "play", silent)

    async def exercise() -> None:
        config = configuration(tmp_path)
        sample = await measure(Client(), synthetic_request(config, 0, 64), voice=config)
        assert sample.outcome == "voice_no_pcm" and sample.usage is not None

    asyncio.run(exercise())


def test_pcm_sink_paces_frames_and_reports_clear_without_claiming_device_silence() -> None:
    async def exercise() -> None:
        sink = PacedOutput()
        frame = rtc.AudioFrame.create(sample_rate=24000, num_channels=1, samples_per_channel=4800)
        started = perf_counter()
        await sink.capture_frame(frame)
        assert perf_counter() - started >= 0.17
        assert sink.first.is_set() and sink.first_at is not None
        sink.clear_buffer()
        event = await sink.wait_for_playout()
        assert event.interrupted and sink.cleared_at is not None
        assert event.playback_position == pytest.approx(0.2)

    asyncio.run(exercise())


def test_run_keeps_background_queued_and_removes_only_its_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ember_agent.inference import benchmark
    from ember_agent.inference.gateway import P0Gateway

    async def exercise() -> None:
        config = configuration(tmp_path)
        config.runtime_root.mkdir(parents=True, exist_ok=True)
        for name in ("gateway.token", "gateway-agent.token"):
            (config.runtime_root / name).write_text("synthetic-secret-never-report")
        native = FakeUpstream()
        gateway = P0Gateway(config, native)
        existing = uuid4()
        await gateway.session(existing, connected=True)

        class Direct:
            def __init__(self, *_: object) -> None:
                pass

            async def health(self) -> GatewayHealth:
                return await gateway.health()

            async def session(self, identity: object, *, connected: bool) -> None:
                await gateway.session(identity, connected=connected)

            async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
                generation = await gateway.admit(request)
                async with benchmark.aclosing(generation.events()) as events:
                    async for event in events:
                        yield event

            async def aclose(self) -> None:
                pass

        monkeypatch.setattr(benchmark, "HttpGatewayClient", Direct)
        monkeypatch.setattr(benchmark, "gpu_snapshot", lambda: None)
        monkeypatch.setattr(benchmark, "hardware", lambda: {"gpu": "synthetic"})
        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            benchmark.httpx,
            "AsyncClient",
            lambda **_: real_client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"version": "synthetic"})
                )
            ),
        )
        output = tmp_path / "report.json"
        try:
            await benchmark.run(
                config,
                output,
                samples=2,
                words=0,
                output_tokens=64,
                allow_reserved=True,
                voice=False,
                queued_workload="P2",
            )
            data = json.loads(output.read_text())
            assert data["environment"]["prefill_metric_definition"] == "ollama_prompt_eval_duration"
            assert data["measurements_complete"] and data["cleanup_complete"]
            assert data["queued_health"]["queued_p2"] == 1
            assert data["warm_first_visible_seconds"]["samples"] == 2
            assert len(data["samples"]) == 3  # Warm samples plus one cancellation trial.
            assert native.count == 5  # Warmup, two samples, detach and next-P0 probe; no P2 run.
            assert "synthetic-secret-never-report" not in output.read_text()
            assert gateway.sessions == {existing}
            assert not gateway.pending["P2"]
        finally:
            await gateway.aclose()

    asyncio.run(exercise())


def test_benchmark_releases_its_reservation_and_preserves_report_when_queue_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ember_agent.inference import benchmark
    from ember_agent.runtime.config import InferenceSettings, VLLMSettings

    clients = []

    class ClientDouble:
        def __init__(self, *args: object) -> None:
            self.checks = 0
            self.sessions = []
            self.closed = False
            clients.append(self)

        async def health(self) -> GatewayHealth:
            self.checks += 1
            if self.checks > 2:
                raise GatewayError("synthetic_health_failure")
            return GatewayHealth(
                status="ready",
                model="synthetic",
                digest="a" * 64,
                context_tokens=8192,
                queued_p2=1 if self.checks == 2 else 0,
            )

        async def session(self, identity: object, *, connected: bool) -> None:
            self.sessions.append(connected)

        async def stream(self, value: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
            await asyncio.Event().wait()
            yield GatewayEvent(
                request_id=value.request_id, context_id=value.context_id, sequence=0, kind="start"
            )

        async def aclose(self) -> None:
            self.closed = True

    async def measured(*args: object, **kwargs: object) -> benchmark.Sample:
        return benchmark.Sample(outcome="complete", elapsed_seconds=0.1)

    monkeypatch.setattr(benchmark, "HttpGatewayClient", ClientDouble)
    monkeypatch.setattr(benchmark, "measure", measured)
    monkeypatch.setattr(benchmark, "gpu_snapshot", lambda: None)
    monkeypatch.setattr(benchmark, "hardware", lambda: {"platform": "synthetic"})
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in ["gateway.token", "gateway-agent.token"]:
        (runtime / name).write_text("synthetic")
    config = configuration(tmp_path).model_copy(
        update={
            "runtime_root": runtime,
            "inference": InferenceSettings(
                backend="vllm",
                vllm=VLLMSettings(
                    endpoint="http://127.0.0.1:8000",
                    artifact_sha256="b" * 64,
                    profile_sha256="c" * 64,
                ),
            ),
        }
    )
    output = tmp_path / "report.json"
    with pytest.raises(GatewayError, match="synthetic_health_failure"):
        asyncio.run(
            benchmark.run(
                config,
                output,
                samples=1,
                words=1,
                output_tokens=64,
                allow_reserved=False,
                voice=False,
                queued_workload="P2",
            )
        )
    assert len(clients) == 2 and all(client.closed for client in clients)
    assert clients[0].sessions == [True, False]
    report = json.loads(output.read_text())
    assert report["measurements_complete"] and not report["cleanup_complete"]
    assert len(report["samples"]) == 2 and report["reservation_id"]
    assert report["environment"]["inference_backend"] == "vllm"
    assert report["environment"]["prefill_metric_definition"] == (
        "vllm_native_prefill_scheduled_to_first_token_including_first_step"
    )
