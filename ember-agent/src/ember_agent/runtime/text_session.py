"""Bounded HTTP fallback over the sole character core and realtime reservation."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from time import monotonic
from uuid import UUID, uuid4

from pydantic import Field

from ..character.core import CharacterCore, TurnObservation
from ..character.recall_intent import deep_recall_query
from ..inference.contracts import GatewayError
from ..memory.deep_contracts import DeepRecallResult
from ..memory.deep_recall import DeepRecallService
from .config import RuntimeModel


class TextReply(RuntimeModel):
    """Buffered fallback output is not delivered history until explicitly acknowledged."""

    request_id: UUID
    text: str = Field(max_length=131072, repr=False)
    retained_memories: int


class DirectTextSession:
    """One expiring browser lease and one generation, with no storage/model construction."""

    def __init__(
        self,
        core: CharacterCore,
        deep: DeepRecallService,
        investigate: Callable[[str, TurnObservation], Awaitable[DeepRecallResult]],
        *,
        model_available: bool,
        lease_seconds: float = 45,
    ) -> None:
        self.core = core
        self.deep = deep
        self.investigate = investigate
        self.model_available = model_available
        self.lease_seconds = lease_seconds
        self.before_turn: Callable[[], Awaitable[None]] | None = None
        self.connection_changed: Callable[[bool], None] | None = None
        self.capture_fence: Callable[[], Awaitable[None]] | None = None
        self.capture_wake: Callable[[], None] | None = None
        self.identity: UUID | None = None
        self.deadline = 0.0
        self.renewed = asyncio.Event()
        self.lease: asyncio.Task[None] | None = None
        self.active: asyncio.Task[TextReply] | None = None
        self.seen: deque[UUID] = deque(maxlen=128)
        self.closed = False
        self.connecting = False
        self.delivery_lock = asyncio.Lock()

    def capture_idle(self) -> bool:
        """A buffered reply is still foreground work until its receipt settles."""
        return (
            not self.closed
            and not self.connecting
            and self.active is None
            and not self.core.busy
            and self.core.pending_delivery is None
            and not self.delivery_lock.locked()
            and (self.identity is not None or self.lease is None or self.lease.done())
        )

    async def connect(self) -> UUID:
        """Reserve realtime before accepting text; a crashed browser expires and drains."""
        if self.closed or not self.model_available:
            raise GatewayError("gateway_unavailable")
        if self.identity is not None or self.connecting:
            raise GatewayError("character_busy")
        if self.before_turn is None or self.connection_changed is None:
            raise GatewayError("text_session_unconfigured")
        self.connecting = True
        try:
            if self.lease is not None:
                await self.lease
            await self.before_turn()
            if self.closed:
                raise GatewayError("runtime_closing")
            self.identity = uuid4()
            self.deadline = monotonic() + self.lease_seconds
            self.renewed.clear()
            self.connection_changed(True)
            self.lease = asyncio.create_task(self._watch_lease(), name="ember-http-session")
            if self.capture_wake is not None:
                wake = self.capture_wake
                self.lease.add_done_callback(lambda _: wake())
            return self.identity
        finally:
            self.connecting = False
            if self.identity is None:
                self.connection_changed(False)
            if self.capture_wake is not None:
                self.capture_wake()

    def heartbeat(self, identity: UUID) -> None:
        """Only this current authenticated transport lease can keep realtime connected."""
        if identity != self.identity or monotonic() >= self.deadline:
            raise GatewayError("text_session_expired")
        self.deadline = monotonic() + self.lease_seconds
        self.renewed.set()

    async def _watch_lease(self) -> None:
        try:
            while self.identity is not None:
                remaining = self.deadline - monotonic()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self.renewed.wait(), remaining)
                    self.renewed.clear()
                except TimeoutError:
                    break
        finally:
            self.identity = None
            await self.quiesce()
            if self.connection_changed is not None:
                self.connection_changed(False)

    async def disconnect(self, identity: UUID) -> None:
        """Drain before the connection lifecycle can release its gateway reservation."""
        if self.identity is not None and identity != self.identity:
            raise GatewayError("text_session_expired")
        self.identity = None
        self.renewed.set()
        if self.lease is not None:
            await asyncio.shield(self.lease)

    async def quiesce(self) -> None:
        """Cancel/drain the owned generation and discard every unacknowledged suffix."""
        if self.active is not None:
            if not self.active.done():
                self.active.cancel()
            await asyncio.gather(self.active, return_exceptions=True)
        async with self.delivery_lock:
            pending = self.core.pending_delivery
            if pending is not None:
                await self.core.acknowledge(pending[0].request.request_id, "", complete=False)

    async def turn(
        self, identity: UUID, request_id: UUID, text: str, investigation_id: UUID | None = None
    ) -> TextReply:
        """No automatic retries, overlapping turns, or delivery inferred from generation."""
        self.heartbeat(identity)
        if self.active is not None or self.core.pending_delivery is not None:
            raise GatewayError("character_awaiting_delivery")
        if request_id in self.seen:
            raise GatewayError("duplicate_request")
        self.seen.append(request_id)
        task = asyncio.create_task(
            self._generate(request_id, text, investigation_id), name="ember-http-turn"
        )
        self.active = task
        try:
            async with asyncio.timeout(190):
                return await asyncio.shield(task)
        except BaseException:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self.quiesce()
            raise
        finally:
            self.active = None
            if self.capture_wake is not None:
                self.capture_wake()

    async def _generate(
        self, request_id: UUID, text: str, investigation_id: UUID | None
    ) -> TextReply:
        if self.capture_fence is not None:
            await self.capture_fence()
        observation = await self.core.observe_input(text, request_id=request_id)
        if self.before_turn is None:
            raise GatewayError("text_session_unconfigured")
        await self.before_turn()
        investigation = None
        if investigation_id is not None:
            investigation = await self.deep.inspect(investigation_id, self.core.context_id)
            if investigation is None:
                raise GatewayError("deep_context_invalidated")
        else:
            query = deep_recall_query(
                text, natural=self.core.recall_enabled and self.core.natural_deep_recall
            )
            if query:
                investigation = await self.investigate(query, observation)
        prepared = await self.core.prepare(
            text, observation=observation, investigation=investigation
        )
        output: list[str] = []
        size = 0
        complete = False
        async with aclosing(self.core.stream(prepared, defer_delivery=True)) as events:
            async for event in events:
                if event.kind == "text":
                    size += len(event.text.encode("utf-8"))
                    if size > 131072:
                        raise GatewayError("output_bound")
                    output.append(event.text)
                if event.kind == "end":
                    complete = True
        if not complete or not await self.core.current(prepared.dependencies, prepared.certificate):
            raise GatewayError("context_invalidated")
        return TextReply(
            request_id=request_id,
            text="".join(output),
            retained_memories=prepared.retained_memories,
        )

    async def acknowledge(self, identity: UUID, request_id: UUID) -> None:
        """Browser receipt binds the exact visible response to the pending core turn."""
        self.heartbeat(identity)
        try:
            async with self.delivery_lock:
                pending = self.core.pending_delivery
                if pending is None or not await self.core.current(
                    pending[0].dependencies, pending[0].certificate
                ):
                    if pending is not None:
                        await self.core.acknowledge(
                            pending[0].request.request_id, "", complete=False
                        )
                    raise GatewayError("context_invalidated")
                await self.core.acknowledge(request_id, pending[1], complete=True)
        finally:
            if self.capture_wake is not None:
                self.capture_wake()

    async def aclose(self) -> None:
        """Stop admission and join the lease and its generation before closing memory."""
        self.closed = True
        if self.identity is not None:
            await self.disconnect(self.identity)
        elif self.lease is not None:
            await asyncio.shield(self.lease)
        await self.quiesce()
