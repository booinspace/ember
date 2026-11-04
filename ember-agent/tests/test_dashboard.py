"""Inspect exact, revocable input without recording prompts or affecting generation."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from ember_agent.inference.contracts import (
    GatewayError,
    GatewayHealth,
    GatewayRequest,
    PromptMessage,
)
from ember_agent.observability import ActivityLog
from ember_agent.runtime.dashboard import Dashboard, SystemSample


def request(context: UUID | None = None) -> GatewayRequest:
    return GatewayRequest(
        request_id=uuid4(),
        context_id=context or uuid4(),
        model="synthetic",
        workload="P0",
        messages=(
            PromptMessage(role="system", content="FULL_SYNTHETIC_SYSTEM"),
            PromptMessage(role="user", content="FULL_SYNTHETIC_INPUT"),
        ),
        max_output_tokens=32,
        deadline_at="2099-01-01T00:00:00.000000Z",
    )


def dashboard() -> Dashboard:
    return Dashboard(
        model="synthetic",
        backend="ollama",
        context_tokens=131072,
        health=AsyncMock(
            return_value=GatewayHealth(
                status="ready", model="synthetic", digest="a" * 64, context_tokens=131072
            )
        ),
    )


def test_capture_is_lazy_exact_bounded_and_replacement_retires_disclosure() -> None:
    async def run() -> None:
        monitor = dashboard()
        step = request()
        monitor.capture(step)
        assert monitor.requests[step.request_id].request is step
        inspected = await monitor.inspect(step.request_id)
        assert inspected["request"] == step.model_dump(mode="json")
        assert "FULL_SYNTHETIC_SYSTEM" in str(inspected)
        assert "FULL_SYNTHETIC" not in str(monitor.activity.since(0))
        replacement = request(step.context_id)
        monitor.capture(replacement)
        with pytest.raises(GatewayError, match="prompt_inspection_expired"):
            await monitor.inspect(step.request_id)
        for _ in range(20):
            monitor.capture(request())
        assert len(monitor.requests) == 8
        await monitor.aclose()
        assert not monitor.requests and not monitor.activity.entries

    asyncio.run(run())


def test_inspection_cannot_return_after_replacement_during_authorization() -> None:
    async def run() -> None:
        monitor = dashboard()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def validate() -> bool:
            entered.set()
            await release.wait()
            return True

        step = request()
        monitor.capture(step, validate=validate)
        inspection = asyncio.create_task(monitor.inspect(step.request_id))
        await entered.wait()
        monitor.retire_context(step.context_id)
        release.set()
        with pytest.raises(GatewayError, match="prompt_inspection_expired"):
            await inspection
        denied = request()
        monitor.capture(denied, validate=AsyncMock(return_value=False))
        with pytest.raises(GatewayError, match="context_invalidated"):
            await monitor.inspect(denied.request_id)
        assert denied.request_id not in monitor.requests

    asyncio.run(run())


def test_closed_inspection_and_expired_snapshot_never_retain_visible_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monitor = dashboard()
        step = request()
        monitor.capture(step)
        monkeypatch.setattr("ember_agent.runtime.dashboard.monotonic", lambda: 1e15)
        with pytest.raises(GatewayError, match="prompt_inspection_expired"):
            await monitor.inspect(step.request_id)
        assert not monitor.requests

    asyncio.run(run())


def test_prompt_expiry_is_rechecked_after_slow_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monitor = dashboard()
        clock = [100.0]
        monkeypatch.setattr("ember_agent.runtime.dashboard.monotonic", lambda: clock[0])
        entered, release = asyncio.Event(), asyncio.Event()

        async def validate() -> bool:
            entered.set()
            await release.wait()
            return True

        step = request()
        monitor.capture(step, validate=validate)
        inspection = asyncio.create_task(monitor.inspect(step.request_id))
        await entered.wait()
        clock[0] += 301
        release.set()
        with pytest.raises(GatewayError, match="prompt_inspection_expired"):
            await inspection
        assert not monitor.requests

    asyncio.run(run())


def test_host_sampling_is_cached_and_does_not_enter_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def sample() -> SystemSample:
        calls.append(1)
        return SystemSample(cpu_percent=12.0)

    monkeypatch.setattr("ember_agent.runtime.dashboard._system_sample", sample)

    async def run() -> None:
        monitor = dashboard()
        monitor.capture(request())
        assert not calls
        first = await monitor.snapshot()
        second = await monitor.snapshot()
        assert len(calls) == 1 and first["system"] == second["system"]
        restarted_client = await monitor.snapshot(after=99999)
        assert restarted_client["events"] == first["events"]

    asyncio.run(run())


def test_activity_coalesces_updates_without_content_and_has_a_hard_bound() -> None:
    log = ActivityLog()
    identity = log.begin("tool", "web_search", request_id=uuid4(), provider="hermes")
    log.update(identity, "succeeded", count=3)
    assert len(log.since(0)) == 1 and log.since(0)[0].status == "succeeded"
    assert not log.since(log.revision)
    for _ in range(300):
        log.begin("system", "synthetic")
    assert len(log.entries) == 256


def test_observer_failures_do_not_lose_core_delivery_or_leave_it_busy(tmp_path: Path) -> None:
    from .test_hermes_chat import application

    async def run() -> None:
        async with application(tmp_path) as env:
            core = env.factory()

            def broken(*args: object) -> None:
                raise RuntimeError("SYNTHETIC_OBSERVER_ERROR")

            core.observe_request = broken
            core.observe_end = broken
            core.observe_input_started = broken
            turn = await core.prepare("Synthetic question")
            assert (
                "".join([event.text async for event in core.stream(turn, defer_delivery=True)])
                == "Synthetic visible reply"
            )
            assert not core.busy and core.pending_delivery is not None
            assert core.observation_failures == 3
            await core.acknowledge(turn.request.request_id, "Synthetic", complete=False)
            assert core.history[-1].assistant == "Synthetic"

    asyncio.run(run())


def test_prepare_keeps_explicit_observation_identity_validation(tmp_path: Path) -> None:
    from .test_hermes_chat import application

    async def run() -> None:
        async with application(tmp_path) as env:
            core = env.factory()
            observed = await core.observe_input("Synthetic question")
            with pytest.raises(GatewayError, match="turn_observation_mismatch"):
                await core.prepare(observed.text, observation=observed, request_id=uuid4())

    asyncio.run(run())


def test_dashboard_api_uses_operator_auth_lazy_disclosure_and_current_authority(
    tmp_path: Path,
) -> None:
    from ember_agent.memory.deep_recall import DeepRecallService
    from ember_agent.realtime.contracts import RoomBinding
    from ember_agent.runtime.browser_control import BrowserControl

    from .test_hermes_chat import application
    from .test_phase_eight_browser import ORIGIN, Controls

    async def run() -> None:
        async with application(tmp_path) as env:
            core = env.factory()
            monitor = dashboard()
            valid = AsyncMock(return_value=True)
            step = request()
            monitor.capture(step, validate=valid)
            deep = DeepRecallService(env.character, lambda _: env.memory, validation=env.memory)
            owner = BrowserControl(
                origin=ORIGIN,
                assets=tmp_path / "unbuilt",
                binding=RoomBinding(
                    instance_id=uuid4(),
                    character_id=env.character,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                core=core,
                deep=deep,
                controls=Controls(),
                dashboard=monitor,
                room_token=lambda: "synthetic",
                answer=AsyncMock(return_value="receipt"),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=owner.app),
                base_url=ORIGIN,
                headers={"origin": ORIGIN},
            ) as http:
                path = "/api/inference/requests/" + str(step.request_id)
                assert (await http.get(path)).status_code == 401
                assert (
                    await http.post("/api/bootstrap", json={"token": owner.activation})
                ).status_code == 200
                metadata = await http.get("/api/dashboard")
                assert (
                    metadata.status_code == 200 and "no-store" in metadata.headers["cache-control"]
                )
                assert "FULL_SYNTHETIC" not in metadata.text
                valid.assert_not_awaited()
                full = await http.get(path)
                assert full.json()["request"] == step.model_dump(mode="json")
                assert full.headers["cache-control"] == "no-store"
                assert (
                    await http.get(path, headers={"origin": "https://other.example"})
                ).status_code == 403
                valid.return_value = False
                denied = await http.get(path + "?validate_only=true")
                assert denied.status_code != 200 and "FULL_SYNTHETIC" not in denied.text
                assert step.request_id not in monitor.requests
                caps = (await http.get("/api/capabilities")).json()["capabilities"]
                assert {item["kind"] for item in caps} == {
                    "native",
                    "mcp",
                    "harness",
                    "public_search",
                }
            await owner.aclose()
            await deep.aclose()
            await monitor.aclose()

    asyncio.run(run())
