"""Conservative release, preflight, and realtime races through the existing gateway."""

import asyncio
import json
import sys
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
import pytest
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope

from ember_agent.inference import gateway as gateway_module
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import GatewayError, GatewayRequest
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.ollama import NativeFrame, NativeMessage
from ember_agent.inference.service import GenerationResponse, ServiceOwner, create_app
from ember_agent.runtime.config import InferenceSettings, StrataSettings
from ember_agent.runtime.livekit_runtime import _reserve_gateway_startup

from .test_gateway import FakeUpstream, configuration, request
from .test_phase_six_gateway import background, upstream


@pytest.mark.parametrize("workload", ["P2", "P3", "P4"])
@pytest.mark.parametrize("trail", ["eof", "error", "frame", "aborted"])
def test_terminal_frame_keeps_owner_until_stream_exits(
    tmp_path: Path, workload: str, trail: str
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        release = asyncio.Event()
        terminal = asyncio.Event()
        native = FakeUpstream()

        async def generate(value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            native.count += 1
            if value.workload != "P0":
                yield NativeFrame(message=NativeMessage(content='{"claims":[]}'))
                yield NativeFrame(done=True, done_reason="abort" if trail == "aborted" else None)
                terminal.set()
                await release.wait()
                if trail == "error":
                    raise GatewayError("upstream_transport_failed")
                if trail == "frame":
                    yield NativeFrame(message=NativeMessage(content='{"late":true}'))
            else:
                yield NativeFrame(message=NativeMessage(content="ready"), done=True)

        native.generate = generate
        owner = ServiceOwner(tmp_path / "state")
        gateway = P0Gateway(
            config, native, record=owner.record, record_sessions=owner.record_sessions
        )
        try:
            task = await gateway.admit(background(config, workload))
            await terminal.wait()
            session = uuid4()
            reservation = asyncio.create_task(gateway.session(session, connected=True))
            await asyncio.sleep(0)
            live = await gateway.admit(request(config, workload="P1", context_id=session))
            assert not reservation.done() and task.queue.qsize() == 1
            assert (await gateway.health()).active_request_id == task.request.request_id
            with pytest.raises(GatewayError, match="busy"):
                await gateway.admit(request(config))
            release.set()
            events = [event async for event in task.events()]
            if trail == "eof":
                await reservation
                assert [event.kind for event in events] == ["start", "text", "end"]
                assert [event async for event in live.events()][-1].kind == "end"
                assert native.count == 2
            else:
                with pytest.raises(GatewayError, match="quiescence_required"):
                    await reservation
                assert [event.kind for event in events] == ["start", "error"]
                assert [event async for event in live.events()][-1].code == "quiescence_required"
                assert native.count == 1
        finally:
            release.set()
            await gateway.aclose()
            owner.close()

        recovered = ServiceOwner(tmp_path / "state")
        try:
            assert recovered.uncertain == (trail != "eof")
        finally:
            recovered.close()

    async def bounded() -> None:
        async with asyncio.timeout(5):
            await run()

    asyncio.run(bounded())


@pytest.mark.parametrize("workload", ["P0", "P3"])
def test_shutdown_joins_preflight_and_never_submits_after_draining(
    tmp_path: Path, workload: str
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        journaling = asyncio.Event()
        release = asyncio.Event()
        records: list[str] = []

        async def record(status: str, value: GatewayRequest) -> None:
            records.append(status)
            if status == "running":
                journaling.set()
                await release.wait()

        gateway = P0Gateway(config, native, record=record)
        submit = asyncio.create_task(
            gateway.admit(request(config, workload=workload, output="json"))
        )
        await asyncio.wait_for(journaling.wait(), 2)
        shutdown = asyncio.create_task(gateway.aclose())
        await asyncio.sleep(0)
        assert not shutdown.done() and not native.closed
        release.set()
        if workload == "P0":
            with pytest.raises(GatewayError, match="draining"):
                await submit
        else:
            job = await submit
            assert [event async for event in job.events()][-1].code == "draining"
        await shutdown
        assert native.closed and native.count == 0
        assert records == ["running", "complete"]

    asyncio.run(run())


def test_cancelled_foreground_admission_releases_only_after_journal_settles(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        journaling = asyncio.Event()
        release = asyncio.Event()
        records: list[tuple[str, str]] = []

        async def record(status: str, value: GatewayRequest) -> None:
            records.append((value.workload, status))
            if value.workload == "P0" and status == "running":
                journaling.set()
                await release.wait()

        gateway = P0Gateway(config, native, record=record)
        submit = asyncio.create_task(gateway.admit(request(config)))
        await asyncio.wait_for(journaling.wait(), 2)
        submit.cancel()
        with pytest.raises(asyncio.CancelledError):
            await submit
        queued = await gateway.admit(background(config))
        assert (await gateway.health()).status == "busy" and native.count == 0
        release.set()
        async with asyncio.timeout(2):
            assert [event async for event in queued.events()][-1].kind == "end"
        assert records == [
            ("P0", "running"),
            ("P0", "complete"),
            ("P3", "running"),
            ("P3", "complete"),
        ]
        assert native.count == 1 and not gateway.uncertain
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("expire", [True, False])
def test_preflight_keeps_queue_capacity_and_rechecks_deadline(tmp_path: Path, expire: bool) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        verifying = asyncio.Event()
        release = asyncio.Event()

        async def verify(*, loaded: bool = False) -> None:
            if not loaded:
                verifying.set()
                await release.wait()

        native.verify = verify
        gateway = P0Gateway(config, native, background_queue_limit=1)
        deadline = datetime.now(UTC) + timedelta(seconds=0.1 if expire else 5)
        job = await gateway.admit(
            background(config, deadline_at=deadline.isoformat().replace("+00:00", "Z"))
        )
        await asyncio.wait_for(verifying.wait(), 2)
        with pytest.raises(GatewayError, match="background_queue_full"):
            await gateway.admit(background(config))
        session = uuid4()
        reservation = asyncio.create_task(gateway.session(session, connected=True))
        await asyncio.sleep(max(0, (deadline - datetime.now(UTC)).total_seconds()) if expire else 0)
        release.set()
        await asyncio.wait_for(reservation, 2)
        assert native.count == 0
        assert (await gateway.health()).queued_p3 == int(not expire)
        await gateway.session(session, connected=False)
        async with asyncio.timeout(2):
            last = [event async for event in job.events()][-1]
        assert last.kind == ("error" if expire else "end")
        assert last.code == ("deadline_exceeded" if expire else None)
        await gateway.aclose()

    asyncio.run(run())


def test_reconnect_during_durable_disconnect_keeps_background_parked(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        writing = asyncio.Event()
        release = asyncio.Event()
        identity = uuid4()

        async def record(sessions: tuple) -> None:
            if not sessions:
                writing.set()
                await release.wait()

        gateway = P0Gateway(config, native, record_sessions=record)
        await gateway.session(identity, connected=True)
        queued = await gateway.admit(background(config))
        first = asyncio.create_task(gateway.session(identity, connected=False))
        await asyncio.wait_for(writing.wait(), 2)
        second = asyncio.create_task(gateway.session(identity, connected=True))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        health = await gateway.health()
        assert health.realtime_sessions == (identity,) and health.queued_p3 == 1
        assert native.count == 0
        await gateway.session(identity, connected=False)
        async with asyncio.timeout(2):
            assert [event async for event in queued.events()][-1].kind == "end"
        await gateway.aclose()

    asyncio.run(run())


def test_client_disconnect_before_stream_iteration_withdraws_queued_job(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        gateway = P0Gateway(config, native)
        session = uuid4()
        await gateway.session(session, connected=True)
        queued = await gateway.admit(background(config))

        async def receive() -> dict[str, object]:
            raise AssertionError("ASGI 2.4 reports disconnect through send")

        async def send(message: dict[str, object]) -> None:
            assert message["type"] == "http.response.start"
            raise OSError("closed")

        with pytest.raises(ClientDisconnect):
            await GenerationResponse(queued)(
                {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
            )
        assert queued.detached and (await gateway.health()).queued_p3 == 0
        await gateway.session(session, connected=False)
        await gateway.aclose()
        assert native.count == 0

    asyncio.run(run())


@pytest.mark.parametrize("subscribe", [True, False])
def test_live_frame_burst_yields_to_subscriber_without_releasing_runner(
    tmp_path: Path, subscribe: bool
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = FakeUpstream()
        release = asyncio.Event()
        burst_sent = asyncio.Event()

        async def generate(value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            for _ in range(64):
                yield NativeFrame(message=NativeMessage(content="burst "))
            burst_sent.set()
            await release.wait()
            yield NativeFrame(done=True, done_reason="stop")

        native.generate = generate
        gateway = P0Gateway(config, native)
        job = await gateway.admit(request(config))

        async def consume() -> list:
            return [event async for event in job.events()]

        reader = asyncio.create_task(consume()) if subscribe else None
        try:
            await asyncio.wait_for(burst_sent.wait(), 2)
            assert gateway.active is job
            with pytest.raises(GatewayError, match="busy"):
                await gateway.admit(request(config))
            release.set()
            events = await reader if reader is not None else await consume()
            if subscribe:
                assert events[-1].kind == "end"
                assert sum(event.text.count("burst ") for event in events) == 64
            else:
                assert job.detached and events[-1].code == "slow_consumer"
        finally:
            release.set()
            await gateway.aclose()
        assert not gateway.uncertain

    asyncio.run(run())


def test_http_shutdown_ends_queued_response_before_lifespan_shutdown(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        gateway = P0Gateway(config, native)
        await gateway.session(uuid4(), connected=True)
        called = asyncio.Event()
        foreground = {"Authorization": "Bearer foreground"}
        background_headers = {"Authorization": "Bearer background"}
        app = create_app(gateway, "foreground", called.set, background_token="background")
        try:
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client,
                asyncio.timeout(2),
            ):
                pending = asyncio.create_task(
                    client.post(
                        "/v1/generate",
                        headers=background_headers,
                        content=background(config).model_dump_json(),
                    )
                )
                try:
                    while (await gateway.health()).queued_p3 != 1:
                        await asyncio.sleep(0)
                    result = await client.post("/v1/shutdown", headers=foreground)
                    assert result.json() == {"code": "draining"} and called.is_set()
                    response = await pending
                    assert response.status_code == 200 and '"code":"draining"' in response.text
                    assert native.count == 0 and not native.closed
                finally:
                    if not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
        finally:
            await gateway.aclose()

    asyncio.run(run())


def test_http_disconnect_during_preflight_never_starts_native_generation(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def verify(*, loaded: bool = False) -> None:
            entered.set()
            await release.wait()

        native.verify = verify
        gateway = P0Gateway(config, native)
        app = create_app(gateway, "foreground")
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/generate",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer foreground")],
        }
        inbox: asyncio.Queue[Message] = asyncio.Queue()
        inbox.put_nowait(
            {"type": "http.request", "body": request(config).model_dump_json().encode()}
        )
        sent: list[Message] = []

        async def send(message: Message) -> None:
            sent.append(message)

        handler = asyncio.create_task(app(scope, inbox.get, send))
        try:
            async with asyncio.timeout(2):
                await entered.wait()
                inbox.put_nowait({"type": "http.disconnect"})
                await handler
            assert b"subscriber_detached" in sent[-1]["body"]
        finally:
            release.set()
            await gateway.aclose()
        assert native.count == 0 and not gateway.uncertain

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_failed_realtime_startup_releases_committed_reservation(
    tmp_path: Path, cancel: bool
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        native.release.clear()
        persisted: list[tuple] = []
        connected = asyncio.Event()
        owner = uuid4()

        async def journal(sessions: tuple) -> None:
            persisted.append(sessions)
            if sessions == (owner,):
                connected.set()

        gateway = P0Gateway(config, native, record_sessions=journal)
        await gateway.admit(background(config))
        await asyncio.wait_for(native.started.wait(), 2)
        transport = httpx.ASGITransport(app=create_app(gateway, "foreground"))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            client = HttpGatewayClient("http://test", "foreground", client=http)
            startup = asyncio.create_task(
                _reserve_gateway_startup(client, owner, 2 if cancel else 0.05)
            )
            try:
                await asyncio.wait_for(connected.wait(), 2)
                if cancel:
                    startup.cancel()
                else:
                    await asyncio.sleep(0.1)
                assert not startup.done() and (await gateway.health()).realtime_sessions == (owner,)
                native.release.set()
                with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
                    await asyncio.wait_for(startup, 2)
                assert persisted == [(owner,), ()]
                assert (await gateway.health()).realtime_sessions == ()
            finally:
                native.release.set()
                await gateway.aclose()

    asyncio.run(run())


def test_expired_client_does_not_cancel_completion_journal(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        settling = asyncio.Event()
        release = asyncio.Event()
        deadline = datetime.now(UTC) + timedelta(milliseconds=200)

        async def record(status: str, value: GatewayRequest) -> None:
            if status == "complete":
                settling.set()
                await release.wait()

        gateway = P0Gateway(config, native, record=record)
        try:
            job = await gateway.admit(
                background(config, deadline_at=deadline.isoformat().replace("+00:00", "Z"))
            )
            await asyncio.wait_for(settling.wait(), 2)
            await asyncio.sleep(max(0, (deadline - datetime.now(UTC)).total_seconds()) + 0.01)
            health = await gateway.health()
            assert health.status == "busy" and health.journal_status == "available"
            release.set()
            assert [event async for event in job.events()][-1].code == "deadline_exceeded"
            next_job = await gateway.admit(request(config))
            assert [event async for event in next_job.events()][-1].kind == "end"
            assert not gateway.journal_failed and not gateway.uncertain
        finally:
            release.set()
            await gateway.aclose()

    asyncio.run(run())


def test_preflight_metadata_cannot_hold_slot_past_request_deadline(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        original_verify = native.verify
        never = asyncio.Event()

        async def verify(*, loaded: bool = False) -> None:
            native.verify = original_verify
            await never.wait()

        native.verify = verify
        gateway = P0Gateway(config, native)
        try:
            deadline = datetime.now(UTC) + timedelta(milliseconds=100)
            job = await gateway.admit(
                background(config, deadline_at=deadline.isoformat().replace("+00:00", "Z"))
            )
            async with asyncio.timeout(2):
                assert [event async for event in job.events()][-1].code == "deadline_exceeded"
                next_job = await gateway.admit(request(config))
                assert [event async for event in next_job.events()][-1].kind == "end"
            assert native.count == 1 and not gateway.uncertain
        finally:
            never.set()
            await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("pin_matches", [True, False])
def test_invalid_output_still_requires_loaded_profile_verification(
    tmp_path: Path, pin_matches: bool
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = FakeUpstream(
            (
                NativeFrame(message=NativeMessage(content="<think>unfinished")),
                NativeFrame(done=True),
            )
        )
        verified = asyncio.Event()

        async def verify(*, loaded: bool = False) -> None:
            if loaded:
                verified.set()
                if not pin_matches:
                    raise GatewayError("model_pin_mismatch")

        native.verify = verify
        gateway = P0Gateway(config, native)
        try:
            job = await gateway.admit(request(config, workload="P2"))
            events = [event async for event in job.events()]
            assert [event.kind for event in events] == ["start", "error"]
            assert verified.is_set()
            assert (events[-1].code == "model_pin_mismatch") is not pin_matches
            assert gateway.uncertain is not pin_matches
        finally:
            await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("workload", ["P2", "P5"])
@pytest.mark.parametrize("step_has_started", [False, True])
def test_foreground_preserves_owned_step_and_precedes_queued_work(
    tmp_path: Path, step_has_started: bool, workload: Literal["P2", "P5"]
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config = config.model_copy(
            update={"inference": config.inference.model_copy(update={"coding_api": True})}
        )
        native = FakeUpstream()
        paused = asyncio.Event()
        release = asyncio.Event()
        trace: list[str] = []
        verify_count = 0

        async def verify(*, loaded: bool = False) -> None:
            nonlocal verify_count
            if not loaded:
                verify_count += 1
                if verify_count == 1 and not step_has_started:
                    paused.set()
                    await release.wait()

        async def generate(value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            trace.append(value.workload)
            if trace == [workload]:
                paused.set()
                await release.wait()
            yield NativeFrame(message=NativeMessage(content="done"), done=True)

        native.verify = verify
        native.generate = generate
        gateway = P0Gateway(config, native)
        try:
            session = uuid4()
            await gateway.session(session, connected=True)
            first = await gateway.admit(
                request(config, workload=workload), idle_managed_task=workload == "P2"
            )
            await paused.wait()
            second = await gateway.admit(
                request(config, workload=workload), idle_managed_task=workload == "P2"
            )
            deadline = datetime.now(UTC) + timedelta(milliseconds=75)
            with pytest.raises(GatewayError, match="deadline_exceeded"):
                await gateway.admit(
                    request(config, deadline_at=deadline.isoformat().replace("+00:00", "Z"))
                )
            assert (await gateway.health()).active_request_id == first.request.request_id
            foreground = asyncio.create_task(gateway.admit(request(config)))
            await asyncio.sleep(0)
            assert not foreground.done()
            release.set()
            voice = await foreground
            assert [event async for event in voice.events()][-1].kind == "end"
            for coding in (first, second):
                assert [event async for event in coding.events()][-1].kind == "end"
            expected = (
                [workload, "P0", workload] if step_has_started else ["P0", workload, workload]
            )
            assert trace == expected
        finally:
            release.set()
            await gateway.aclose()

    async def bounded() -> None:
        async with asyncio.timeout(5):
            await run()

    asyncio.run(bounded())


def test_idle_managed_lane_waits_for_voice_but_not_connection(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = FakeUpstream()
        gateway = P0Gateway(config, native)
        session = uuid4()
        try:
            await gateway.session(session, connected=True)
            gateway.voice(session, reserved=True)
            regular = await gateway.admit(request(config, workload="P2"))
            managed = await gateway.admit(request(config, workload="P2"), idle_managed_task=True)
            foreground = await gateway.admit(request(config))
            assert [event async for event in foreground.events()][-1].kind == "end"
            assert native.count == 1
            gateway.voice(session, reserved=False)
            assert [event async for event in managed.events()][-1].kind == "end"
            assert native.count == 2
            assert (await gateway.health()).realtime_sessions == (session,)
            assert regular in gateway.pending["P2"]
        finally:
            await gateway.aclose()

    async def bounded() -> None:
        async with asyncio.timeout(5):
            await run()

    asyncio.run(bounded())


def test_idle_managed_lane_requires_p2_agent_auth(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        gateway = P0Gateway(config, FakeUpstream())
        app = create_app(gateway, "foreground", background_token="background", agent_token="agent")
        try:
            async with httpx.AsyncClient(
                base_url="http://test", transport=httpx.ASGITransport(app=app)
            ) as client:
                for token, workload in (
                    ("foreground", "P0"),
                    ("background", "P3"),
                    ("agent", "P1"),
                    ("agent", "P5"),
                ):
                    response = await client.post(
                        "/v1/generate",
                        content=request(config, workload=workload).model_dump_json(),
                        headers={
                            "Authorization": f"Bearer {token}",
                            "X-Ember-Managed-Task": "idle",
                        },
                    )
                    assert response.status_code == 403
        finally:
            await gateway.aclose()

    asyncio.run(run())


def test_native_deadline_reports_expiry_and_fences_its_slot(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = FakeUpstream()
        native.release.clear()
        owner = ServiceOwner(tmp_path / "state")
        gateway = P0Gateway(config, native, record=owner.record)
        try:
            deadline = datetime.now(UTC) + timedelta(milliseconds=200)
            job = await gateway.admit(
                request(config, deadline_at=deadline.isoformat().replace("+00:00", "Z"))
            )
            await asyncio.wait_for(native.started.wait(), 1)
            assert [event async for event in job.events()][-1].code == "deadline_exceeded"
            with pytest.raises(GatewayError, match="quiescence_required"):
                await gateway.admit(request(config))
            assert native.count == 1
        finally:
            native.release.set()
            await gateway.aclose()
            owner.close()
        recovered = ServiceOwner(tmp_path / "state")
        try:
            assert recovered.uncertain
        finally:
            recovered.close()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["drained", "missing_eof", "missing_done", "changed_runner"])
def test_strata_coding_deadline_keeps_slot_until_verified_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    monkeypatch.setattr(gateway_module, "CODING_DRAIN_SECONDS", 0.5)

    async def run() -> None:
        config = configuration(tmp_path).model_copy(
            update={
                "inference": InferenceSettings(
                    backend="strata",
                    coding_api=True,
                    strata=StrataSettings(
                        endpoint="http://127.0.0.1:18110",
                        python_executable=sys.executable,
                        server_script=str(tmp_path / "server.py"),
                        runner_config=str(tmp_path / "runner.json"),
                        runner_config_sha256="a" * 64,
                    ),
                )
            }
        )
        native = FakeUpstream()
        release = asyncio.Event()
        terminal = asyncio.Event()
        eof = asyncio.Event()
        settle = asyncio.Event()
        settling = asyncio.Event()
        calls: list[str] = []
        owner = ServiceOwner(tmp_path / "state")
        state = tmp_path / "state" / "gateway-state.json"

        async def generate(value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            calls.append(value.workload)
            if value.workload == "P5":
                native.started.set()
                await release.wait()
                yield NativeFrame(message=NativeMessage(content="late coding output"))
                if outcome == "missing_done":
                    return
                yield NativeFrame(done=True, done_reason="stop")
                terminal.set()
                await eof.wait()
            else:
                yield NativeFrame(message=NativeMessage(content="voice ready"), done=True)

        async def verify(*, loaded: bool = False) -> None:
            if loaded and outcome == "changed_runner":
                raise GatewayError("upstream_instance_changed")

        async def record(status: str, value: GatewayRequest) -> None:
            if value.workload == "P5" and status == "complete":
                settling.set()
                await settle.wait()
            await owner.record(status, value)

        native.generate = generate
        native.verify = verify
        gateway = P0Gateway(config, native, record=record)
        voice = None
        try:
            deadline = datetime.now(UTC) + timedelta(milliseconds=200)
            job = await gateway.admit(
                request(
                    config, workload="P5", deadline_at=deadline.isoformat().replace("+00:00", "Z")
                )
            )
            await native.started.wait()
            events = [event async for event in job.events()]
            assert [(event.kind, event.code) for event in events] == [
                ("start", None),
                ("error", "deadline_exceeded"),
            ]
            assert (await gateway.health()).status == "busy"
            assert json.loads(state.read_bytes())["status"] == "running"
            voice = asyncio.create_task(gateway.admit(request(config)))
            release.set()
            if outcome != "missing_done":
                await terminal.wait()
                assert calls == ["P5"] and not voice.done()
            if outcome != "missing_eof":
                eof.set()
            if outcome == "drained":
                await settling.wait()
                assert calls == ["P5"] and not voice.done()
                assert json.loads(state.read_bytes())["status"] == "running"
                settle.set()
                reply = await voice
                assert [event async for event in reply.events()][-1].kind == "end"
                assert calls == ["P5", "P0"] and job.queue.empty()
            else:
                with pytest.raises(GatewayError, match="quiescence_required"):
                    await voice
                assert calls == ["P5"]
                assert json.loads(state.read_bytes())["status"] == "uncertain"
        finally:
            release.set()
            eof.set()
            settle.set()
            if voice is not None:
                await asyncio.gather(voice, return_exceptions=True)
            await gateway.aclose()
            owner.close()
        recovered = ServiceOwner(tmp_path / "state")
        try:
            assert recovered.uncertain == (outcome != "drained")
        finally:
            recovered.close()

    async def bounded() -> None:
        async with asyncio.timeout(5):
            await run()

    asyncio.run(bounded())


@pytest.mark.parametrize("proof", [True, False])
def test_voice_preempts_only_owned_coding_and_waits_for_durable_release(
    tmp_path: Path, proof: bool
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        config = config.model_copy(
            update={
                "inference": config.inference.model_copy(update={"coding_api": True}),
            }
        )
        native = FakeUpstream()
        terminal = asyncio.Event()
        eof = asyncio.Event()
        play = asyncio.Event()
        receipt = asyncio.Event()
        interrupted = asyncio.Event()
        attempts = []
        generations = []
        owner = ServiceOwner(tmp_path / "state")
        first, second = (request(config, workload="P5") for _ in range(2))

        async def generate(value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            generations.append(value.request_id)
            if value.request_id == first.request_id:
                yield NativeFrame(message=NativeMessage(content="partial coding answer"))
                play.set()
                await interrupted.wait()
                yield NativeFrame(message=NativeMessage(content="late coding text"))
                yield NativeFrame(done=True, done_reason="stop")
                terminal.set()
                await eof.wait()
            else:
                yield NativeFrame(message=NativeMessage(content="ready"), done=True)

        async def stop(identity: object) -> None:
            attempts.append(identity)
            interrupted.set()
            await receipt.wait()
            if not proof:
                raise GatewayError("upstream_preemption_unknown")

        native.generate = generate
        gateway = P0Gateway(config, native, record=owner.record, preempt=stop)
        app = create_app(gateway, "voice", agent_token="agent")
        session = uuid4()
        route = f"/v1/realtime/{session}/voice"
        voice = request(config)
        foreground = None
        try:
            async with httpx.AsyncClient(
                base_url="http://test", transport=httpx.ASGITransport(app=app)
            ) as client:
                job = await gateway.admit(first)
                await play.wait()
                queued = await gateway.admit(second)
                assert (
                    await client.put(
                        route, headers={"Authorization": "Bearer agent"}, json={"connected": True}
                    )
                ).status_code == 401
                assert not interrupted.is_set()
                await gateway.session(session, connected=True)
                assert (
                    await client.put(
                        route, headers={"Authorization": "Bearer voice"}, json={"connected": True}
                    )
                ).status_code == 200
                await terminal.wait()
                foreground = asyncio.create_task(gateway.admit(voice))
                await asyncio.sleep(0)
                assert not foreground.done() and gateway.active is job
                eof.set()
                await asyncio.sleep(0)
                assert not foreground.done() and gateway.active is job
                assert attempts == [first.request_id]
                receipt.set()
                events = [event async for event in job.events()]
                assert [event.kind for event in events] == ["start", "text", "error"]
                if proof:
                    assert events[-1].code == "voice_preempted"
                    reply = await foreground
                    assert [event async for event in reply.events()][-1].kind == "end"
                    assert generations == [first.request_id, voice.request_id]
                    gateway.voice(session, reserved=False)
                    assert [event async for event in queued.events()][-1].kind == "end"
                else:
                    assert events[-1].code == "upstream_preemption_unknown"
                    with pytest.raises(GatewayError, match="quiescence_required"):
                        await foreground
                    assert [event async for event in queued.events()][
                        -1
                    ].code == "quiescence_required"
                    assert generations == [first.request_id]
        finally:
            eof.set()
            receipt.set()
            interrupted.set()
            if foreground is not None:
                await asyncio.gather(foreground, return_exceptions=True)
            await gateway.aclose()
            owner.close()
        recovered = ServiceOwner(tmp_path / "state")
        try:
            assert recovered.uncertain == (not proof)
        finally:
            recovered.close()

    async def bounded() -> None:
        async with asyncio.timeout(5):
            await run()

    asyncio.run(bounded())
