"""Transport connection state controls P3 admission; conversational silence never does."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from ember_memory_abi.errors import MemoryError

from ..inference.contracts import GatewayError


class CaptureLifecycle:
    """Own one coalesced lifecycle task and an existing gateway session reservation.

    Reconnection first reserves realtime capacity and waits for the currently
    owned P3 step. A continuation predicate prevents another extraction step;
    no preemption, polling scheduler or second memory owner is introduced.
    """

    def __init__(
        self,
        identity: UUID,
        reserve: Callable[[UUID, bool], Awaitable[None]],
        quiesce: Callable[[], Awaitable[None]],
        consolidate: Callable[[Callable[[], bool]], Awaitable[int]],
    ) -> None:
        self.identity = identity
        self.reserve = reserve
        self.quiesce = quiesce
        self.consolidate = consolidate
        self.connected = False
        self.closed = False
        self.status = "idle"
        self.pending = 0
        self.changed = asyncio.Event()
        self.lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start only after the transport is ready; initial disconnected capture is permitted."""
        if self.task is not None:
            raise RuntimeError("Capture lifecycle already started.")
        self.task = asyncio.create_task(self._run(), name="ember-capture-lifecycle")
        self.changed.set()

    def connection_changed(self, connected: bool) -> None:
        """Synchronous room callbacks only coalesce state into the single owned task."""
        if not self.closed:
            self.connected = connected
            self.changed.set()

    async def before_turn(self) -> None:
        """Do not prepare a reply until the gateway confirms background quiescence."""
        if self.closed:
            raise GatewayError("runtime_closing")
        self.connection_changed(True)
        async with self.lock:
            await self.reserve(self.identity, True)

    async def _run(self) -> None:
        while not self.closed:
            await self.changed.wait()
            self.changed.clear()
            if self.closed:
                break
            try:
                if self.connected:
                    async with self.lock:
                        await self.reserve(self.identity, True)
                    self.status = "realtime_reserved"
                    continue
                self.status = "draining_transport"
                await self.quiesce()
                async with self.lock:
                    # A join while draining is not permission to release its reservation.
                    if self.connected:
                        continue
                    await self.reserve(self.identity, False)
                self.status = "processing"
                self.pending = await self.consolidate(
                    lambda: not self.connected and not self.closed
                )
                self.status = "pending" if self.pending else "idle"
                if self.pending and not self.connected and not self.closed:
                    # Continue owned bounded batches without waiting for another disconnect.
                    # Failed checkpoints are excluded by capture until explicitly retried.
                    self.changed.set()
            except (GatewayError, MemoryError, TimeoutError):
                self.status = "blocked"

    async def aclose(self) -> None:
        """Stop new steps and drain the task before releasing only this reservation."""
        self.closed = True
        self.changed.set()
        if self.task is not None:
            await asyncio.shield(self.task)
        async with self.lock:
            await self.reserve(self.identity, False)
