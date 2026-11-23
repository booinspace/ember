"""One event-driven native capture owner for disconnected and connected-idle chat."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from ember_memory_abi.errors import MemoryError

from ..inference.contracts import GatewayError
from .worker_process import settled


class CaptureLifecycle:
    """Keep realtime reserved; grant only native P3 access to verified idle gaps."""

    def __init__(
        self,
        identity: UUID,
        reserve: Callable[[UUID, bool], Awaitable[None]],
        quiesce: Callable[[], Awaitable[None]],
        consolidate: Callable[[Callable[[], bool], UUID | None], Awaitable[int]],
        *,
        idle: Callable[[], bool] | None = None,
        permit: Callable[[UUID, bool], Awaitable[None]] | None = None,
        enabled: Callable[[], bool] | None = None,
        acceptance_lock: asyncio.Lock | None = None,
    ) -> None:
        if permit is not None and idle is None:
            raise ValueError("Connected capture requires a transport idle check.")
        self.identity = identity
        self.reserve = reserve
        self.quiesce = quiesce
        self.consolidate = consolidate
        self.idle = idle
        self.permit = permit
        self.enabled = enabled
        self.acceptance_lock = acceptance_lock or asyncio.Lock()
        self.connected = False
        self.closed = False
        self.status = "idle"
        self.pending = 0
        self.changed = asyncio.Event()
        self.lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None
        self._reserved_epoch: int | None = None
        self._permitted = False
        self._connection_epoch = 0
        self._drained_epoch: int | None = None

    def start(self) -> None:
        """Start after the transport is ready, including with a restart backlog."""
        if self.task is not None:
            raise RuntimeError("Capture lifecycle already started.")
        self.task = asyncio.create_task(self._run(), name="ember-capture-lifecycle")
        self.wake()

    def connection_changed(self, connected: bool) -> None:
        """Coalesce room and browser connection changes on the runtime loop."""
        if not self.closed:
            if self.connected != connected:
                self.connected = connected
                self._connection_epoch += 1
            self.wake()

    def wake(self) -> None:
        """Recheck on durable input, capture policy or transport activity changes."""
        if not self.closed:
            self.changed.set()

    async def _reserve(self, connected: bool) -> None:
        epoch = self._connection_epoch
        self._reserved_epoch = None
        await self.reserve(self.identity, connected)
        self._reserved_epoch = epoch
        self._permitted = False

    async def _fence_permit(self) -> None:
        if self.permit is not None and self._permitted:
            try:
                await self.permit(self.identity, False)
            except GatewayError:
                # A trusted reservation update still fences before P0. Do not lose
                # original input because the optional early fence call failed.
                self.status = "blocked"
            else:
                self._permitted = False

    async def fence(self) -> None:
        """Close queued P3 admission and finish any already-submitted acceptance write."""
        if self.permit is not None:
            async with self.lock:
                await self._fence_permit()
        async with self.acceptance_lock:
            pass

    async def before_turn(self) -> None:
        """Reserve foreground after durable input; older gateways may need a drain."""
        if self.closed:
            raise GatewayError("runtime_closing")
        if not self.connected:
            self.connection_changed(True)
        epoch = self._connection_epoch
        async with self.lock:
            if self.closed:
                raise GatewayError("runtime_closing")
            if self._connection_epoch != epoch:
                raise GatewayError("live_reservation_released")
            await self._reserve(True)
        async with self.acceptance_lock:
            if self.closed or self._connection_epoch != epoch:
                raise GatewayError("live_reservation_released")

    def _allowed(self, epoch: int) -> bool:
        return (
            not self.closed
            and self._connection_epoch == epoch
            and (self.enabled is None or self.enabled())
            and (self.idle is None or self.idle())
        )

    async def _run(self) -> None:
        while not self.closed:
            await self.changed.wait()
            self.changed.clear()
            if self.closed:
                break
            connected = self.connected
            epoch = self._connection_epoch

            def allowed(epoch: int = epoch) -> bool:
                return self._allowed(epoch)

            try:
                if not connected and self._drained_epoch != epoch:
                    self.status = "draining_transport"
                    await self.quiesce()
                    self._drained_epoch = epoch
                async with self.lock:
                    if self.closed or self._connection_epoch != epoch:
                        continue
                    if self._reserved_epoch != epoch:
                        await self._reserve(connected)
                    if self.closed or self._connection_epoch != epoch:
                        continue
                    if self.enabled is not None and not self.enabled():
                        await self._fence_permit()
                        if not self._permitted:
                            self.status = "disabled"
                        continue
                    if connected and self.permit is None:
                        self.status = "realtime_reserved"
                        continue
                    if not allowed():
                        await self._fence_permit()
                        if not self._permitted:
                            self.status = "waiting_for_idle"
                        continue
                    if connected and self.permit is not None:
                        # Treat a lost response as possibly applied; fence on the next turn.
                        self._permitted = True
                        await self.permit(self.identity, True)
                if not allowed():
                    await self.fence()
                    continue
                self.status = "processing"
                self.pending = await self.consolidate(allowed, self.identity if connected else None)
                self.status = "pending" if self.pending else "idle"
                if self.pending and allowed():
                    self.wake()  # Process successive bounded batches only while idle.
                else:
                    async with self.lock:
                        await self._fence_permit()
            except GatewayError as error:
                self.status = (
                    "waiting_for_idle" if error.code == "idle_capture_deferred" else "blocked"
                )
            except (MemoryError, TimeoutError):
                self.status = "blocked"

    async def aclose(self) -> None:
        """Fence new work and drain the bounded batch before releasing this reservation."""
        self.closed = True
        self.changed.set()

        async def drain_and_release() -> None:
            try:
                await self.fence()
                if self.task is not None:
                    await self.task
            finally:
                async with self.lock:
                    await self._reserve(False)

        await settled(asyncio.create_task(drain_and_release(), name="ember-capture-close"))
