"""One capture owner yields to foreground without losing durable backlog."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

from ember_agent.runtime.capture_lifecycle import CaptureLifecycle


def test_reconnect_reserves_foreground_and_prevents_another_background_step() -> None:
    async def exercise() -> None:
        first_step = asyncio.Event()
        release_step = asyncio.Event()
        finished = asyncio.Event()
        events = []
        steps = []

        async def reserve(_: UUID, connected: bool) -> None:
            events.append(("reserve", connected))

        async def quiesce() -> None:
            events.append(("quiesce", False))

        async def consolidate(allowed: Callable[[], bool], _: UUID | None) -> int:
            assert allowed()
            steps.append(1)
            first_step.set()
            await release_step.wait()
            if allowed():
                steps.append(2)
            finished.set()
            return 1

        lifecycle = CaptureLifecycle(uuid4(), reserve, quiesce, consolidate)
        lifecycle.start()
        await asyncio.wait_for(first_step.wait(), 2)
        assert events == [("quiesce", False), ("reserve", False)]
        await lifecycle.before_turn()
        assert lifecycle.connected and events[-1] == ("reserve", True)
        release_step.set()
        await asyncio.wait_for(finished.wait(), 2)
        await lifecycle.aclose()
        assert steps == [1]
        assert events[-1] == ("reserve", False)
        assert lifecycle.task.done()

    asyncio.run(exercise())


def test_legacy_gateway_never_runs_capture_while_connected() -> None:
    async def exercise() -> None:
        reservation = asyncio.Event()

        async def reserve(_: UUID, connected: bool) -> None:
            if connected:
                reservation.set()

        async def quiesce() -> None:
            raise AssertionError("A connected session cannot enter background capture.")

        async def consolidate(_: Callable[[], bool], __: UUID | None) -> int:
            raise AssertionError("Legacy connected capture must not run.")

        lifecycle = CaptureLifecycle(uuid4(), reserve, quiesce, consolidate)
        lifecycle.connection_changed(True)
        lifecycle.start()
        try:
            await asyncio.wait_for(reservation.wait(), 2)
            assert lifecycle.status == "realtime_reserved"
            lifecycle.wake()
            await asyncio.sleep(0)
            assert lifecycle.status == "realtime_reserved"
        finally:
            await lifecycle.aclose()

    asyncio.run(exercise())


def test_capture_policy_resumes_idle_backlog_without_another_connection_event() -> None:
    async def exercise() -> None:
        enabled = False
        reserved = asyncio.Event()
        resumed = asyncio.Event()
        identity = uuid4()

        async def reserve(_: UUID, connected: bool) -> None:
            if connected:
                reserved.set()

        async def quiesce() -> None:
            raise AssertionError("Connected transport must remain connected.")

        async def permit(_: UUID, idle: bool) -> None:
            if idle:
                assert enabled

        async def consolidate(allowed: Callable[[], bool], context: UUID | None) -> int:
            assert enabled and allowed() and context == identity
            resumed.set()
            return 0

        lifecycle = CaptureLifecycle(
            identity,
            reserve,
            quiesce,
            consolidate,
            idle=lambda: True,
            permit=permit,
            enabled=lambda: enabled,
        )
        lifecycle.connection_changed(True)
        lifecycle.start()
        try:
            await asyncio.wait_for(reserved.wait(), 2)
            assert lifecycle.status == "disabled" and not resumed.is_set()
            enabled = True
            lifecycle.wake()
            await asyncio.wait_for(resumed.wait(), 2)
            assert lifecycle.connected
        finally:
            await lifecycle.aclose()

    asyncio.run(exercise())


def test_idle_backlog_continues_in_bounded_batches_without_another_connection_event() -> None:
    async def exercise() -> None:
        idle = asyncio.Event()
        calls = 0

        async def reserve(_: UUID, connected: bool) -> None:
            assert not connected

        async def quiesce() -> None:
            pass

        async def consolidate(allowed: Callable[[], bool], _: UUID | None) -> int:
            nonlocal calls
            assert allowed()
            calls += 1
            if calls == 3:
                idle.set()
                return 0
            return 1

        lifecycle = CaptureLifecycle(uuid4(), reserve, quiesce, consolidate)
        lifecycle.start()
        await asyncio.wait_for(idle.wait(), 2)
        await asyncio.sleep(0)
        assert lifecycle.status == "idle" and calls == 3
        await lifecycle.aclose()

    asyncio.run(exercise())


def test_missing_background_credentials_block_without_repeated_idle_drains(tmp_path: Path) -> None:
    from ember_agent.runtime.composition import LocalRuntime
    from ember_agent.runtime.config import load_config

    from .test_livekit_runtime import EXAMPLE

    config = load_config(EXAMPLE).model_copy(
        update={
            "data_root": tmp_path / "data",
            "runtime_root": tmp_path / "runtime",
            "system_prompt_file": tmp_path / "prompts/system.md",
        }
    )
    config.runtime_root.mkdir()
    (config.runtime_root / "gateway.token").write_text("synthetic", encoding="utf-8")

    async def exercise() -> None:
        runtime = LocalRuntime(config)
        entered = asyncio.Event()
        calls = 0

        async def reserve(_: UUID, connected: bool) -> None:
            assert not connected

        async def quiesce() -> None:
            pass

        async def consolidate(allowed: Callable[[], bool], _: UUID | None) -> int:
            nonlocal calls
            calls += 1
            entered.set()
            return await runtime.consolidate(allowed)

        lifecycle = CaptureLifecycle(uuid4(), reserve, quiesce, consolidate)
        try:
            await runtime.core.observe_input("Milo is my synthetic shark plushie.")
            lifecycle.start()
            await asyncio.wait_for(entered.wait(), 2)
            async with asyncio.timeout(2):
                while lifecycle.status != "blocked":
                    await asyncio.sleep(0)
            await asyncio.sleep(0.02)
            assert calls == 1
            assert runtime.capture.pending()[0].status == "queued"
        finally:
            await lifecycle.aclose()
            await runtime.aclose()

    asyncio.run(exercise())
