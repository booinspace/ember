"""Synthetic checks for the gateway's session-scoped native P3 idle lane."""

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

import httpx
import pytest

from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import (
    IDLE_NATIVE_CAPTURE_FEATURE,
    GatewayError,
    GatewayEvent,
    GatewayRequest,
)
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.service import ServiceOwner, create_app
from ember_agent.inference.upstream import NativeFrame, NativeMessage, Upstream

from .test_gateway import configuration, request
from .test_phase_six_gateway import background, upstream


def test_idle_capture_http_is_scoped_to_p3_and_sole_realtime_owner(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        gateway = P0Gateway(config, cast(Upstream, native))
        app = create_app(
            gateway,
            "foreground",
            background_token="background",
            agent_token="agent",
            capture_token="capture",
        )
        identity, other = uuid4(), uuid4()
        value = background(config, context_id=identity)
        try:
            async with (
                asyncio.timeout(5),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as http,
            ):

                async def post(token: str, payload: GatewayRequest) -> httpx.Response:
                    return await http.post(
                        "/v1/generate",
                        headers={"Authorization": f"Bearer {token}"},
                        content=payload.model_dump_json(),
                    )

                foreground = HttpGatewayClient("http://test", "foreground", client=http)
                capture = HttpGatewayClient("http://test", "capture", client=http)
                assert IDLE_NATIVE_CAPTURE_FEATURE in (await foreground.health()).features
                await foreground.session(identity, connected=True)
                for token in ("capture", "background", "agent"):
                    response = await http.put(
                        f"/v1/realtime/{identity}/capture",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"connected": True},
                    )
                    assert response.status_code == 401
                with pytest.raises(GatewayError, match="idle_capture_deferred"):
                    async for _ in capture.stream(value):
                        pytest.fail("Capture without an idle permit must not stream.")

                with pytest.raises(GatewayError, match="idle_capture_deferred"):
                    await foreground.capture_idle(other, True)
                await foreground.session(other, connected=True)
                with pytest.raises(GatewayError, match="idle_capture_deferred"):
                    await foreground.capture_idle(identity, True)
                await foreground.session(other, connected=False)
                await foreground.capture_idle(identity, True)

                denied_pairs = [
                    ("capture", workload) for workload in ("P0", "P1", "P2", "P4", "P5")
                ]
                for token, workload in (*denied_pairs, ("foreground", "P3"), ("agent", "P3")):
                    denied = request(config, workload=workload, context_id=identity, output="json")
                    response = await post(token, denied)
                    assert (response.status_code, response.json()) == (
                        403,
                        {"code": "workload_not_authorized"},
                    )
                response = await post("capture", background(config, context_id=other))
                assert (response.status_code, response.json()) == (
                    409,
                    {"code": "idle_capture_deferred"},
                )
                assert native.count == 0

                async with asyncio.TaskGroup() as tasks:
                    ordinary = [
                        tasks.create_task(
                            post(
                                token,
                                request(
                                    config, workload=workload, context_id=identity, output="json"
                                ),
                            )
                        )
                        for token, workload in (
                            ("agent", "P2"),
                            ("background", "P3"),
                            ("background", "P4"),
                        )
                    ]
                    # ASGITransport buffers the response until the stream ends. Wait for enqueue.
                    while any(len(gateway.pending[lane]) != 1 for lane in ("P2", "P3", "P4")):
                        assert not any(job.done() for job in ordinary)
                        await asyncio.sleep(0)
                    events = [event async for event in capture.stream(value)]
                    assert [event.kind for event in events] == ["start", "text", "end"]
                    assert events[1].text == '{"claims":[]}'
                    health = await capture.health()
                    assert health.realtime_sessions == (identity,)
                    assert (health.queued_p2, health.queued_p3, health.queued_p4) == (1, 1, 1)
                    assert native.count == 1
                    await foreground.session(identity, connected=False)
                for job in ordinary:
                    response = job.result()
                    assert response.status_code == 200
                    assert (
                        GatewayEvent.model_validate_json(response.text.splitlines()[-1]).kind
                        == "end"
                    )
                assert native.count == 4
        finally:
            await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("trigger", ["P0", "P1", "speech"])
def test_idle_capture_fences_queue_but_drains_active_window(
    tmp_path: Path, trigger: Literal["P0", "P1", "speech"]
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        identity = uuid4()
        first = background(config, context_id=identity)
        started, finish, terminal, eof, settling, settle = (asyncio.Event() for _ in range(6))
        calls: list[UUID] = []
        stops: list[UUID] = []

        async def generate(value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            calls.append(value.request_id)
            if value.request_id == first.request_id:
                yield NativeFrame(message=NativeMessage(content='{"claims":[]}'))
                started.set()
                await finish.wait()
                yield NativeFrame(done=True)
                terminal.set()
                await eof.wait()
            else:
                yield NativeFrame(message=NativeMessage(content="ready"), done=True)

        async def record(status: str, value: GatewayRequest) -> None:
            if status == "complete" and value.request_id == first.request_id:
                settling.set()
                await settle.wait()

        async def stop(request_id: UUID) -> None:
            stops.append(request_id)

        native.generate = generate
        gateway = P0Gateway(config, cast(Upstream, native), record=record, preempt=stop)
        try:
            async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
                await gateway.session(identity, connected=True)
                gateway.capture_idle(identity, idle=True)
                active = await gateway.admit(first, idle_native_capture=True)
                await started.wait()
                ordinary = await gateway.admit(background(config, context_id=identity))
                queued = await gateway.admit(
                    background(config, context_id=identity), idle_native_capture=True
                )
                value = request(
                    config, workload="P1" if trigger == "P1" else "P0", context_id=identity
                )
                foreground = None
                if trigger == "speech":
                    gateway.voice(identity, reserved=True)
                else:
                    foreground = tasks.create_task(gateway.admit(value))
                    await asyncio.sleep(0)  # Run foreground admission while native P3 is held.
                assert tuple(gateway.pending["P3"]) == (ordinary,)
                assert [(event.kind, event.code) async for event in queued.events()] == [
                    ("start", None),
                    ("error", "idle_capture_deferred"),
                ]
                with pytest.raises(GatewayError, match="idle_capture_deferred"):
                    await gateway.admit(
                        background(config, context_id=identity), idle_native_capture=True
                    )
                if foreground is None:
                    with pytest.raises(GatewayError, match="idle_capture_deferred"):
                        gateway.capture_idle(identity, idle=True)
                    gateway.voice(identity, reserved=False)
                    with pytest.raises(GatewayError, match="idle_capture_deferred"):
                        await gateway.admit(
                            background(config, context_id=identity), idle_native_capture=True
                        )
                    foreground = tasks.create_task(gateway.admit(value))
                assert gateway.active is active and calls == [first.request_id]

                finish.set()
                await terminal.wait()
                assert active.queue.qsize() == 1  # Terminal frame is not release or output proof.
                assert gateway.active is active and calls == [first.request_id]
                eof.set()
                await settling.wait()
                assert active.queue.qsize() == 1
                assert gateway.active is active and calls == [first.request_id]
                if value.workload == "P0":
                    assert not foreground.done()
                settle.set()
                events = [event async for event in active.events()]
                assert [event.kind for event in events] == ["start", "text", "end"]
                assert events[1].text == '{"claims":[]}'
                live = await foreground
                assert [event async for event in live.events()][-1].kind == "end"
                assert calls == [first.request_id, value.request_id] and stops == []
                assert not gateway.uncertain
        finally:
            finish.set()
            eof.set()
            settle.set()
            await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("workload", ["P0", "P1"])
@pytest.mark.parametrize("waiting", [False, True], ids=["active", "waiting"])
def test_idle_capture_cannot_reopen_during_foreground(
    tmp_path: Path, workload: Literal["P0", "P1"], waiting: bool
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        native.release.clear()
        gateway = P0Gateway(config, cast(Upstream, native))
        identity = uuid4()
        try:
            async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
                await gateway.session(identity, connected=True)
                if waiting:
                    gateway.capture_idle(identity, idle=True)
                    await gateway.admit(
                        background(config, context_id=identity), idle_native_capture=True
                    )
                    await native.started.wait()
                foreground = tasks.create_task(
                    gateway.admit(request(config, workload=workload, context_id=identity))
                )
                if waiting:
                    await asyncio.sleep(0)  # Foreground admission waits behind native capture.
                else:
                    await native.started.wait()
                with pytest.raises(GatewayError, match="idle_capture_deferred"):
                    gateway.capture_idle(identity, idle=True)
                native.release.set()
                live = await foreground
                assert [event async for event in live.events()][-1].kind == "end"
                gateway.capture_idle(identity, idle=True)
        finally:
            native.release.set()
            await gateway.aclose()

    asyncio.run(run())


def test_idle_capture_shutdown_drains_and_restart_requires_new_permit(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        root = tmp_path / "gateway"
        identity = uuid4()
        owner = ServiceOwner(root)
        native = upstream()
        native.release.clear()
        gateway = P0Gateway(
            config, native, record=owner.record, record_sessions=owner.record_sessions
        )
        try:
            async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
                await gateway.session(identity, connected=True)
                gateway.capture_idle(identity, idle=True)
                active = await gateway.admit(
                    background(config, context_id=identity), idle_native_capture=True
                )
                await native.started.wait()
                queued = await gateway.admit(
                    background(config, context_id=identity), idle_native_capture=True
                )
                close = tasks.create_task(gateway.aclose())
                assert [(event.kind, event.code) async for event in queued.events()] == [
                    ("start", None),
                    ("error", "draining"),
                ]
                assert not close.done() and gateway.active is active
                native.release.set()
                assert [event async for event in active.events()][-1].kind == "end"
            assert native.count == 1
        finally:
            native.release.set()
            await gateway.aclose()
            owner.close()

        reopened = ServiceOwner(root)
        assert reopened.sessions == (identity,) and not reopened.uncertain
        native = upstream()
        gateway = P0Gateway(config, native, sessions=reopened.sessions)
        try:
            with pytest.raises(GatewayError, match="idle_capture_deferred"):
                await gateway.admit(
                    background(config, context_id=identity), idle_native_capture=True
                )
            assert native.count == 0
        finally:
            await gateway.aclose()
            reopened.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["verify", "journal"])
def test_idle_capture_reopen_does_not_revive_fenced_preflight(tmp_path: Path, phase: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        paused, resume = asyncio.Event(), asyncio.Event()
        identity = uuid4()
        previous = background(config, context_id=identity)
        previous_records: list[str] = []

        async def verify(*, loaded: bool = False) -> None:
            if phase == "verify" and not loaded and not paused.is_set():
                paused.set()
                await resume.wait()

        async def record(status: str, value: GatewayRequest) -> None:
            if value.request_id == previous.request_id:
                previous_records.append(status)
                if phase == "journal" and status == "running":
                    paused.set()
                    await resume.wait()

        native.verify = verify
        gateway = P0Gateway(config, cast(Upstream, native), record=record)
        try:
            async with asyncio.timeout(5):
                await gateway.session(identity, connected=True)
                gateway.capture_idle(identity, idle=True)
                old = await gateway.admit(previous, idle_native_capture=True)
                await paused.wait()
                assert native.count == 0
                gateway.capture_idle(identity, idle=False)
                gateway.capture_idle(identity, idle=True)
                fresh = await gateway.admit(
                    background(config, context_id=identity), idle_native_capture=True
                )
                resume.set()
                assert [(event.kind, event.code) async for event in old.events()] == [
                    ("start", None),
                    ("error", "idle_capture_deferred"),
                ]
                assert [event async for event in fresh.events()][-1].kind == "end"
                assert previous_records == (["running", "complete"] if phase == "journal" else [])
                assert native.count == 1 and not gateway.uncertain
        finally:
            resume.set()
            await gateway.aclose()

    asyncio.run(run())
