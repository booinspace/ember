"""Conservative release, preflight, and realtime races through the existing gateway."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope

from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import GatewayError, GatewayRequest
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.ollama import NativeFrame, NativeMessage
from ember_agent.inference.service import GenerationResponse, ServiceOwner, create_app
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


@pytest.mark.parametrize("coding_has_started", [False, True])
def test_foreground_preserves_owned_coding_and_precedes_queued_work(
    tmp_path: Path, coding_has_started: bool
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
                if verify_count == 1 and not coding_has_started:
                    paused.set()
                    await release.wait()

        async def generate(value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            trace.append(value.workload)
            if trace == ["P5"]:
                paused.set()
                await release.wait()
            yield NativeFrame(message=NativeMessage(content="done"), done=True)

        native.verify = verify
        native.generate = generate
        gateway = P0Gateway(config, native)
        try:
            session = uuid4()
            await gateway.session(session, connected=True)
            first = await gateway.admit(request(config, workload="P5"))
            await paused.wait()
            second = await gateway.admit(request(config, workload="P5"))
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
            expected = ["P5", "P0", "P5"] if coding_has_started else ["P0", "P5", "P5"]
            assert trace == expected
        finally:
            release.set()
            await gateway.aclose()

    async def bounded() -> None:
        async with asyncio.timeout(5):
            await run()

    asyncio.run(bounded())


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
