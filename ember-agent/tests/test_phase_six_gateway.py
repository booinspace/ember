"""P3/P4 bounded admission, foreground ownership, buffering and scoped credentials."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from ember_agent.inference.contracts import GatewayError, GatewayRequest
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.ollama import NativeFrame, NativeMessage
from ember_agent.inference.service import ServiceOwner, SessionState, create_app
from ember_agent.runtime.config import DeploymentConfig

from .test_gateway import FakeUpstream, configuration, request


def background(config: DeploymentConfig, workload: str = "P3", **updates: object) -> GatewayRequest:
    return request(config, workload=workload, output="json", **updates)


def upstream() -> FakeUpstream:
    return FakeUpstream(
        (
            NativeFrame(message=NativeMessage(content='{"claims":[]}')),
            NativeFrame(done=True, eval_count=3),
        )
    )


def test_connected_idle_session_blocks_background_until_explicit_release(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        gateway = P0Gateway(config, native)
        identity = uuid4()
        await gateway.session(identity, connected=True)
        job = await gateway.admit(background(config))
        await asyncio.sleep(0.02)
        assert native.count == 0 and (await gateway.health()).queued_p3 == 1
        foreground = await gateway.admit(request(config))
        assert [event async for event in foreground.events()][-1].kind == "end"
        assert native.count == 1 and gateway.active is None
        await gateway.session(identity, connected=False)
        events = [event async for event in job.events()]
        assert events[-1].kind == "end" and native.count == 2
        await gateway.aclose()

    asyncio.run(run())


def test_voice_start_reserves_first_and_waits_for_actual_background_completion(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        native.release.clear()
        gateway = P0Gateway(config, native)
        job = await gateway.admit(background(config))
        await native.started.wait()
        identity = uuid4()
        reserve = asyncio.create_task(gateway.session(identity, connected=True))
        await asyncio.sleep(0)
        assert identity in gateway.sessions and not reserve.done()
        waiting = await gateway.admit(background(config, "P4"))
        with pytest.raises(GatewayError, match="busy"):
            await gateway.admit(request(config))
        native.release.set()
        await reserve
        assert native.count == 1 and gateway.active is None
        assert [event async for event in job.events()][-1].kind == "end"
        await gateway.session(identity, connected=False)
        assert [event async for event in waiting.events()][-1].kind == "end"
        assert native.count == 2
        await gateway.aclose()

    asyncio.run(run())


def test_bounded_queues_expire_without_starting_and_disconnect_removes_pending(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        gateway = P0Gateway(config, native, background_queue_limit=1)
        await gateway.session(uuid4(), connected=True)
        deadline = (
            (datetime.now(UTC) + timedelta(milliseconds=50)).isoformat().replace("+00:00", "Z")
        )
        job = await gateway.admit(background(config, deadline_at=deadline))
        with pytest.raises(GatewayError, match="background_queue_full"):
            await gateway.admit(background(config))
        events = [event async for event in job.events()]
        assert events[-1].code == "deadline_exceeded" and native.count == 0
        detached = await gateway.admit(background(config))
        stream = detached.events()
        await anext(stream)
        await stream.aclose()
        assert len(gateway.pending["P3"]) == 0 and native.count == 0
        await gateway.aclose()

    asyncio.run(run())


def test_background_json_is_published_only_after_schema_validated_completion(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = FakeUpstream(
            (
                NativeFrame(message=NativeMessage(content='<think>SECRET</think>{"wrong":1}')),
                NativeFrame(done=True),
            )
        )
        gateway = P0Gateway(config, native)
        job = await gateway.admit(
            background(config, output_schema={"type": "object", "required": ["claims"]})
        )
        events = [event async for event in job.events()]
        assert events[-1].code == "structured_schema_mismatch"
        assert not any(event.kind == "text" for event in events)
        assert all("SECRET" not in event.model_dump_json() for event in events)
        await gateway.aclose()

    asyncio.run(run())


def test_background_credentials_cannot_submit_p0_or_change_sessions(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        gateway = P0Gateway(config, upstream())
        app = create_app(gateway, "f" * 32, background_token="b" * 32)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            foreground = {"Authorization": "Bearer " + "f" * 32}
            bulk = {"Authorization": "Bearer " + "b" * 32}
            response = await client.post(
                "/v1/generate", headers=foreground, content=background(config).model_dump_json()
            )
            assert response.status_code == 403
            response = await client.post(
                "/v1/generate", headers=bulk, content=request(config).model_dump_json()
            )
            assert response.status_code == 403
            response = await client.put(
                "/v1/realtime/" + str(uuid4()), headers=bulk, json={"connected": True}
            )
            assert response.status_code == 401
            response = await client.post(
                "/v1/generate", headers=bulk, content=background(config).model_dump_json()
            )
            assert response.status_code == 200
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[-1]["kind"] == "end"
        await gateway.aclose()

    asyncio.run(run())


def test_session_ownership_survives_gateway_restart_until_operator_quiescence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gateway"
    identity = uuid4()
    owner = ServiceOwner(root)
    owner.write_sessions(SessionState(sessions=(identity,)))
    owner.close()
    reopened = ServiceOwner(root)
    assert reopened.sessions == (identity,)
    reopened.close()
    acknowledged = ServiceOwner(root, acknowledge_quiescence=True)
    assert acknowledged.sessions == ()
    acknowledged.close()


def test_p3_and_p4_queues_preserve_fifo_without_starving_bulk(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()
        gateway = P0Gateway(config, native)
        identity = uuid4()
        await gateway.session(identity, connected=True)
        jobs = [await gateway.admit(background(config, kind)) for kind in ("P3", "P3", "P4", "P4")]
        await gateway.session(identity, connected=False)
        for job in jobs:
            assert [event async for event in job.events()][-1].kind == "end"
        assert native.count == 4 and gateway.active is None
        await gateway.aclose()

    asyncio.run(run())


def test_realtime_arriving_during_metadata_admission_prevents_native_start(tmp_path: Path) -> None:
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
        gateway = P0Gateway(config, native)
        job = await gateway.admit(background(config))
        await verifying.wait()
        identity = uuid4()
        reservation = asyncio.create_task(gateway.session(identity, connected=True))
        await asyncio.sleep(0)
        release.set()
        await reservation
        assert native.count == 0 and len(gateway.pending["P3"]) == 1
        await gateway.session(identity, connected=False)
        assert [event async for event in job.events()][-1].kind == "end"
        assert native.count == 1
        await gateway.aclose()

    asyncio.run(run())


def test_failed_session_journal_keeps_background_admission_closed(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        native = upstream()

        async def record_sessions(sessions: tuple) -> None:
            raise OSError("synthetic-journal-failure")

        gateway = P0Gateway(config, native, record_sessions=record_sessions)
        identity = uuid4()
        with pytest.raises(GatewayError, match="session_journal_failed"):
            await gateway.session(identity, connected=True)
        assert identity in gateway.sessions and gateway.journal_failed
        with pytest.raises(GatewayError, match="journal_unavailable"):
            await gateway.admit(background(config))
        assert native.count == 0
        assert (await gateway.health()).journal_status == "failed"
        await gateway.aclose()

    asyncio.run(run())


def test_weighted_agent_queue_preserves_background_progress(tmp_path: Path) -> None:
    from collections.abc import AsyncGenerator

    class RecordingUpstream(FakeUpstream):
        def __init__(self) -> None:
            super().__init__(
                (
                    NativeFrame(message=NativeMessage(content='{"claims":[]}')),
                    NativeFrame(done=True),
                )
            )
            self.order: list[str] = []

        async def generate(self, value: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            self.order.append(value.workload)
            async for frame in super().generate(value):
                yield frame

    async def run() -> None:
        config = configuration(tmp_path)
        native = RecordingUpstream()
        gateway = P0Gateway(config, native)
        session = uuid4()
        await gateway.session(session, connected=True)
        try:
            jobs = [await gateway.admit(request(config, workload="P2")) for _ in range(4)]
            jobs += [await gateway.admit(background(config, workload)) for workload in ("P3", "P4")]
            await gateway.session(session, connected=False)
            async with asyncio.timeout(5):
                for job in jobs:
                    assert [event async for event in job.events()][-1].kind == "end"
            assert native.order == ["P2", "P2", "P2", "P2", "P3", "P4"]
        finally:
            await gateway.aclose()

    asyncio.run(run())
