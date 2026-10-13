"""Disconnected background work pauses on reconnect without interpreting silence as permission."""

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

        async def consolidate(allowed: Callable[[], bool]) -> int:
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


def test_connected_session_never_runs_capture_in_an_idle_gap() -> None:
    async def exercise() -> None:
        reservation = asyncio.Event()

        async def reserve(_: UUID, connected: bool) -> None:
            if connected:
                reservation.set()

        async def quiesce() -> None:
            raise AssertionError("A connected session cannot enter background capture.")

        async def consolidate(_: Callable[[], bool]) -> int:
            raise AssertionError("Silence is not permission to invoke P3.")

        lifecycle = CaptureLifecycle(uuid4(), reserve, quiesce, consolidate)
        lifecycle.connection_changed(True)
        lifecycle.start()
        await asyncio.wait_for(reservation.wait(), 2)
        assert lifecycle.status == "realtime_reserved"
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

        async def consolidate(allowed: Callable[[], bool]) -> int:
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

        async def consolidate(allowed: Callable[[], bool]) -> int:
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
