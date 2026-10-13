"""One application-owned investigation slot; queued and running native work stay owned."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from .deep_contracts import DeepRecallRequest, DeepRecallResult


@dataclass
class _Pending:
    character_id: UUID
    task: asyncio.Task[DeepRecallResult]


class DeepAdmission:
    """One active investigation and two queued requests, at most one per character.

    Deadlines include queue time. Caller cancellation signals the owned runner,
    whose cleanup drains its native read handle before releasing admission.
    """

    def __init__(self) -> None:
        self._slot = asyncio.Semaphore(1)
        self._pending: dict[UUID, _Pending] = {}
        self._waiting: set[UUID] = set()
        self._closed = False

    async def submit(
        self,
        request: DeepRecallRequest,
        work: Callable[[], Awaitable[DeepRecallResult]],
    ) -> DeepRecallResult:
        """Return explicit saturation/timeout outcomes without creating unowned work."""
        if self._closed:
            return self._empty(request, "unavailable", "deep_service_closed")
        if request.request_id in self._pending:
            return self._empty(request, "deferred", "request_already_running")
        if len(self._waiting) >= 2 or request.character_id in self._waiting:
            return self._empty(request, "deferred", "deep_queue_saturated")
        self._waiting.add(request.character_id)

        async def run() -> DeepRecallResult:
            acquired = False
            try:
                async with asyncio.timeout(request.budget.deadline_ms / 1000):
                    await self._slot.acquire()
                    acquired = True
                    self._waiting.discard(request.character_id)
                    return await work()
            except TimeoutError:
                return self._empty(request, "partial", "deep_deadline")
            except asyncio.CancelledError:
                return self._empty(request, "cancelled", "deep_cancelled")
            finally:
                self._waiting.discard(request.character_id)
                if acquired:
                    self._slot.release()
                self._pending.pop(request.request_id, None)

        task = asyncio.create_task(run(), name="ember-deep-" + str(request.request_id))
        self._pending[request.request_id] = _Pending(request.character_id, task)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelling():
                task.cancel()
            await asyncio.shield(task)
            raise

    def cancel(self, request_id: UUID, character_id: UUID) -> bool:
        """Only the owning character may signal a request; no result text is exposed."""
        pending = self._pending.get(request_id)
        if pending is None or pending.character_id != character_id:
            return False
        if not pending.task.cancelling():
            pending.task.cancel()
        return True

    async def aclose(self) -> None:
        """Stop admission, cancel investigations and await their owned native drain."""
        self._closed = True
        tasks = tuple(item.task for item in self._pending.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _empty(
        request: DeepRecallRequest,
        outcome: str,
        reason: str,
    ) -> DeepRecallResult:
        return DeepRecallResult.model_validate(
            {
                "request_id": request.request_id,
                "character_id": request.character_id,
                "context_id": request.context_id,
                "outcome": outcome,
                "reasons": (reason,),
            }
        )
