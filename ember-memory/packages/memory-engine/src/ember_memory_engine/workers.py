"""Owned bounded executors: timed-out native work retains its admission slot until completion."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import BoundedSemaphore, Lock

from .errors import EngineBusyError, EngineClosedError


class OwnedWorkers:
    """Bound accepted/running work independently of caller cancellation and executor queue size."""

    def __init__(self, workers: int, *, max_pending: int, name: str) -> None:
        if workers < 1 or max_pending < workers:
            raise ValueError("Worker/pending limits must be positive and internally consistent.")
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self._slots = BoundedSemaphore(max_pending)
        self._guard = Lock()
        self._closed = False
        self._pending = 0

    @property
    def pending(self) -> int:
        """Accepted native jobs, including work whose caller has timed out or cancelled."""
        with self._guard:
            return self._pending

    def submit[T](self, work: Callable[[], T]) -> Future[T]:
        """Accept work nonblockingly, or fail before execution when draining/full."""
        with self._guard:
            if self._closed:
                raise EngineClosedError()
            if not self._slots.acquire(blocking=False):
                raise EngineBusyError()
            self._pending += 1
            try:
                future = self.executor.submit(work)
            except RuntimeError as error:
                self._pending -= 1
                self._slots.release()
                raise EngineClosedError() from error
        future.add_done_callback(self._completed)
        return future

    def _completed[T](self, _: Future[T]) -> None:
        with self._guard:
            self._pending -= 1
            self._slots.release()

    def stop_admission(self) -> None:
        """Reject new work; existing jobs remain owned until completion/queued cancellation."""
        with self._guard:
            self._closed = True

    def shutdown(self) -> None:
        """Drain all accepted work and join worker threads; call off the realtime loop."""
        self.stop_admission()
        self.executor.shutdown(wait=True, cancel_futures=False)
