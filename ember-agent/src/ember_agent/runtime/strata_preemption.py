"""Private request-specific STOP receipts for the pinned single-slot Strata runner."""

# pyright: reportPrivateUsage=false

import re
from dataclasses import dataclass, field
from threading import Event, Lock, local
from typing import Protocol

ROUTE = "/v1/ember-preemption"
_ID = re.compile(r"[0-9a-f]{32}")
_FINISHED = frozenset({"stop", "length", "cancel"})


class _Engine(Protocol):
    proc: object
    can_stop: bool
    batch: int

    def _parse_done(self, line: str) -> None: ...


class _Service(Protocol):
    instance_id: str
    api_key: str
    engine: _Engine
    admission_reserved: int

    def loaded(self) -> bool: ...


class _Handler(Protocol):
    path: str
    headers: dict[str, str]

    def do_GET(self) -> None: ...
    def do_POST(self) -> None: ...
    def _openai(self, req: dict[str, object]) -> None: ...
    def _watch_client(self, cancel: Event) -> None: ...
    def _authorized(self) -> bool: ...
    def _json(self, status: int, obj: dict[str, str]) -> None: ...


class StrataServer(Protocol):
    """Pinned server seam used only by the managed runner bootstrap."""

    StrataEngine: type[_Engine]

    def make_handler(self, svc: _Service) -> type[_Handler]: ...


@dataclass
class _Request:
    identity: str
    process: object
    cancel: Event | None = None
    requested: bool = False
    native_finish: str | None = None
    result: str = "unknown"
    released: Event = field(default_factory=Event)


class _Current(local):
    request: _Request | None = None


def install(server: StrataServer) -> None:
    """Attach each stop and native DONE to the same HTTP handler and engine process."""
    current = _Current()
    original_done = server.StrataEngine._parse_done

    def parse_done(engine: _Engine, line: str) -> None:
        original_done(engine, line)
        request = current.request
        if request is not None and engine.proc is request.process:
            request.native_finish = line.split()[5]

    server.StrataEngine._parse_done = parse_done  # type: ignore[method-assign]
    original_handler = server.make_handler

    def make_handler(svc: _Service) -> type[_Handler]:
        handler = original_handler(svc)
        original_openai = handler._openai
        original_watch = handler._watch_client
        original_post = handler.do_POST
        original_get = handler.do_GET
        lock = Lock()
        tracked: _Request | None = None

        def supported() -> bool:
            engine = svc.engine
            return (
                bool(svc.api_key)
                and isinstance(engine, server.StrataEngine)
                and engine.can_stop
                and not engine.batch
                and engine.proc is not None
                and svc.loaded()
            )

        def openai(peer: _Handler, req: dict[str, object]) -> None:
            nonlocal tracked
            identity = req.get("ember_preemption_id")
            if identity is None:
                original_openai(peer, req)
                return
            if not isinstance(identity, str) or _ID.fullmatch(identity) is None or not supported():
                peer._json(409, {"outcome": "preemption_unavailable"})
                return
            with lock:
                if tracked is not None and not tracked.released.is_set():
                    peer._json(409, {"outcome": "preemption_busy"})
                    return
                tracked = current.request = _Request(identity, svc.engine.proc)
            original_openai(peer, req)

        def watch(peer: _Handler, cancel: Event) -> None:
            request = current.request
            if request is not None:
                with lock:
                    request.cancel = cancel
                    if request.requested:
                        cancel.set()
            original_watch(peer, cancel)

        def get(peer: _Handler) -> None:
            if peer.path != ROUTE:
                original_get(peer)
                return
            if svc.api_key and peer._authorized():
                ready = supported()
                peer._json(200 if ready else 503, {"instance": svc.instance_id, "outcome": "ready"})
            elif not svc.api_key:
                peer._json(503, {"outcome": "preemption_unavailable"})

        def preempt(peer: _Handler, identity: str) -> None:
            if not svc.api_key:
                peer._json(503, {"outcome": "preemption_unavailable"})
                return
            if not peer._authorized():
                return
            if (
                _ID.fullmatch(identity) is None
                or peer.headers.get("Origin") is not None
                or peer.headers.get("Transfer-Encoding") is not None
                or peer.headers.get("Content-Length", "0") != "0"
            ):
                peer._json(400, {"outcome": "invalid_request"})
                return
            with lock:
                request = tracked if tracked is not None and tracked.identity == identity else None
                if request is not None and not request.released.is_set():
                    request.requested = True
                    if request.cancel is not None:
                        request.cancel.set()
            if request is None:
                status, result = 404, "not_registered"
            elif not request.released.wait(5):
                status, result = 503, "unknown"
            else:
                status, result = 200, request.result
            peer._json(status, {"instance": svc.instance_id, "id": identity, "outcome": result})

        def post(peer: _Handler) -> None:
            if peer.path.startswith(ROUTE + "/"):
                preempt(peer, peer.path[len(ROUTE) + 1 :])
                return
            try:
                original_post(peer)
            finally:
                request = current.request
                current.request = None
                if request is not None:
                    # The original POST has left its FIFO and HTTP admission finally blocks.
                    with lock:
                        if (
                            request.native_finish in _FINISHED
                            and svc.engine.proc is request.process
                            and svc.loaded()
                            and svc.admission_reserved == 0
                        ):
                            request.result = (
                                "aborted" if request.native_finish == "cancel" else "completed"
                            )
                        request.released.set()

        handler._openai = openai  # type: ignore[method-assign]
        handler._watch_client = watch  # type: ignore[method-assign]
        handler.do_GET = get  # type: ignore[method-assign]
        handler.do_POST = post  # type: ignore[method-assign]
        return handler

    server.make_handler = make_handler
