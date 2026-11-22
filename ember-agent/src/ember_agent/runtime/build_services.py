"""Runtime-owned static Build preview: GET/HEAD without queries, script or forms.

The proxy does not redirect or forward browser credentials. The provider owns the fixed-port
relay and container isolation. The proxy uses
an out-of-band relay key; the relay strips it before dialing the workload.
Neither the relay address nor its key is published.
"""

import asyncio
import logging
import math
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

import h11
import httpx

from .task_store import TaskConflict
from .worker_process import settled

_LOG = logging.getLogger(__name__)
_LOOPBACK = "127.0.0.1"
_MAX_TTL = 30 * 60
_MAX_CLIENTS = 8
_HEADER_BYTES = 8192
_PATH_BYTES = 2048
_BODY_BYTES = 4 * 1024 * 1024
_REQUEST_SECONDS = 5.0
_STATIC_HEADERS = [
    (b"Cache-Control", b"no-store"),
    (b"Connection", b"close"),
    (
        b"Content-Security-Policy",
        b"sandbox allow-same-origin; default-src 'none'; style-src 'self' 'unsafe-inline'; "
        b"img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'none'; "
        b"frame-ancestors 'none'",
    ),
    (b"Cross-Origin-Resource-Policy", b"same-origin"),
    (b"Referrer-Policy", b"no-referrer"),
    (b"X-Content-Type-Options", b"nosniff"),
]
_RELAY = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", re.ASCII)
_RELAY_KEY = re.compile(r"[A-Za-z0-9_-]{32,128}", re.ASCII)
_RELAY_HEADER = "X-Ember-Build-Relay"
_MEDIA = re.compile(rb"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+(?:;\s*charset=utf-8)?", re.I)


@dataclass(frozen=True, slots=True)
class BuildService:
    """Ephemeral local preview receipt; never includes private transport details."""

    task_id: UUID
    url: str
    expires_at: float  # Unix seconds for display; enforcement uses the monotonic deadline.


@dataclass(slots=True)
class _Preview:
    receipt: BuildService
    deadline: float
    host: bytes
    backend: httpx.URL
    relay_key: str = field(repr=False)
    server: asyncio.Server
    cleanup: Callable[[], Awaitable[None]]
    clients: dict[asyncio.Task[None], asyncio.StreamWriter] = field(default_factory=lambda: {})
    unavailable: asyncio.Event = field(default_factory=asyncio.Event)
    active: bool = False
    expiry: asyncio.Task[None] | None = None


@dataclass(frozen=True, slots=True)
class _Page:
    status: int
    body: bytes = b""
    content_type: bytes = b"text/plain; charset=utf-8"
    length: int = 0


async def _fetch(backend: httpx.URL, relay_key: str, method: bytes, target: bytes) -> _Page:
    # No shared cookie jar, proxy environment, browser headers or DNS resolution.
    try:
        async with (
            httpx.AsyncClient(trust_env=False, follow_redirects=False) as client,
            client.stream(
                method.decode("ascii"),
                backend.copy_with(raw_path=target),
                headers={
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    _RELAY_HEADER: relay_key,
                },
            ) as response,
        ):
            if not 200 <= response.status_code < 300:
                return _Page(404 if response.status_code == 404 else 502)
            if (
                sum(len(key) + len(value) + 4 for key, value in response.headers.raw)
                > _HEADER_BYTES
            ):
                return _Page(502)
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                return _Page(502)
            declared = response.headers.get("content-length")
            if declared is not None and (
                not declared.isascii()
                or not declared.isdecimal()
                or len(declared) > 10
                or int(declared) > _BODY_BYTES
            ):
                return _Page(502)
            media = response.headers.get("content-type", "").encode("ascii", "replace")
            if len(media) > 120 or not _MEDIA.fullmatch(media):
                media = b"text/plain; charset=utf-8"
            if method == b"HEAD":
                return _Page(response.status_code, content_type=media, length=int(declared or 0))
            buffer = bytearray()
            async for chunk in response.aiter_raw():
                if len(buffer) + len(chunk) > _BODY_BYTES:
                    return _Page(502)
                buffer.extend(chunk)
            if response.status_code == 205 and buffer:
                return _Page(502)
            return _Page(response.status_code, bytes(buffer), media, len(buffer))
    except (OSError, httpx.HTTPError, httpx.InvalidURL):
        return _Page(502)


async def _request(reader: asyncio.StreamReader, protocol: h11.Connection) -> h11.Request | None:
    remaining = _HEADER_BYTES
    while remaining:
        data = await reader.read(min(remaining, 4096))
        if not data:
            return None
        remaining -= len(data)
        protocol.receive_data(data)
        event = protocol.next_event()
        if isinstance(event, h11.Request):
            return event
        if event is not h11.NEED_DATA:
            return None
    return None


def _reject(request: h11.Request, host: bytes) -> int | None:
    if [value for key, value in request.headers if key == b"host"] != [host]:
        return 421
    if request.method not in {b"GET", b"HEAD"}:
        return 405
    if any(
        key in {b"transfer-encoding", b"upgrade", b"expect"}
        or (key == b"content-length" and value != b"0")
        for key, value in request.headers
    ):
        return 400
    # ponytail: short origin-form paths only. No queries, request bodies, upgrades or ranges.
    target = request.target
    if (
        len(target) > _PATH_BYTES
        or not target.startswith(b"/")
        or target.startswith(b"//")
        or any(char < 0x21 or char > 0x7E for char in target)
        or any(char in target for char in (b"#", b"?", b"\\"))
    ):
        return 400
    return None


async def _serve(
    preview: _Preview, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    protocol = h11.Connection(h11.SERVER, max_incomplete_event_size=_HEADER_BYTES)
    checking_root = False
    unavailable = False
    try:
        async with asyncio.timeout_at(
            min(preview.deadline, asyncio.get_running_loop().time() + _REQUEST_SECONDS)
        ):
            request = await _request(reader, protocol)
            if request is None:
                return
            rejection = _reject(request, preview.host)
            if rejection is None:
                checking_root = request.method == b"GET" and request.target == b"/"
                page = await _fetch(
                    preview.backend, preview.relay_key, request.method, request.target
                )
                unavailable = checking_root and not 200 <= page.status < 300
                checking_root = False
            else:
                page = _Page(rejection)
            headers = [*_STATIC_HEADERS, (b"Content-Type", page.content_type)]
            if page.status != 204:
                headers.append((b"Content-Length", str(page.length).encode("ascii")))
            writer.write(
                protocol.send(h11.Response(status_code=page.status, headers=headers)) or b""
            )
            if page.body:
                writer.write(protocol.send(h11.Data(data=page.body)) or b"")
            writer.write(protocol.send(h11.EndOfMessage()) or b"")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
    except TimeoutError:
        unavailable = unavailable or checking_root
        writer.transport.abort()
    except (OSError, h11.ProtocolError):
        writer.transport.abort()
    finally:
        writer.close()
        if unavailable:
            preview.unavailable.set()


class BuildServices:
    """Own at most one operator-granted, task-bound Build preview per runtime."""

    def __init__(self, *, ttl_seconds: float = _MAX_TTL) -> None:
        if not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= _MAX_TTL:
            raise ValueError("Build service TTL must be positive and at most 30 minutes.")
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._opening = 0
        self._openings_drained = asyncio.Event()
        self._openings_drained.set()
        self._closed = False
        self._preview: _Preview | None = None

    @property
    def current(self) -> BuildService | None:
        """Return retained preview, not a continuously healthy status claim."""
        preview = self._preview
        if (
            not self._closed
            and preview
            and preview.active
            and not preview.unavailable.is_set()
            and asyncio.get_running_loop().time() < preview.deadline
        ):
            return preview.receipt
        return None

    def snapshot(self) -> BuildService | None:
        """Read the current receipt without exposing the backend."""
        return self.current

    async def retain(
        self,
        task_id: UUID,
        service_port: int,
        relay_origin: str,
        cleanup: Callable[[], Awaitable[None]],
        *,
        relay_key: str,
    ) -> str | None:
        """Own cleanup on all outcomes; stage URL after GET / passes, without serving yet."""
        self._opening += 1
        self._openings_drained.clear()
        retained = False
        try:
            async with self._lock:
                relay = _RELAY.fullmatch(relay_origin)
                if (
                    self._closed
                    or self._preview is not None
                    or relay is None
                    or not 1024 <= int(relay[1]) <= 65535
                    or type(service_port) is not int
                    or not 1024 <= service_port <= 65535
                    or not _RELAY_KEY.fullmatch(relay_key)
                ):
                    return None
                backend = httpx.URL(relay_origin)
                try:
                    async with asyncio.timeout(_REQUEST_SECONDS):
                        root = await _fetch(backend, relay_key, b"GET", b"/")
                        if not 200 <= root.status < 300:
                            return None
                    if self._closed:
                        return None
                    preview = await self._listen(task_id, backend, relay_key, cleanup)
                except (OSError, TimeoutError):
                    return None
                if self._closed:
                    await self._close_listener(preview)
                    return None
                self._preview = preview
                preview.expiry = asyncio.create_task(
                    self._expire(preview), name="ember-build-expiry"
                )
                retained = True
                return preview.receipt.url
        finally:
            try:
                if not retained:
                    await settled(asyncio.create_task(self._cleanup(cleanup)))
            finally:
                self._opening -= 1
                if not self._opening:
                    self._openings_drained.set()

    def activate(self, task_id: UUID) -> None:
        """Publish the matching staged preview after its successful task commit."""
        preview = self._preview
        if not self._closed and preview is not None and preview.receipt.task_id == task_id:
            preview.active = True

    async def _listen(
        self,
        task_id: UUID,
        backend: httpx.URL,
        relay_key: str,
        cleanup: Callable[[], Awaitable[None]],
    ) -> _Preview:
        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            if (
                self._preview is not preview
                or self.current is None
                or len(preview.clients) >= _MAX_CLIENTS
            ):
                writer.transport.abort()
                return
            task = asyncio.create_task(_serve(preview, reader, writer), name="ember-build-preview")
            preview.clients[task] = writer

            def done(client: asyncio.Task[None]) -> None:
                preview.clients.pop(client, None)
                if not client.cancelled() and client.exception() is not None:
                    _LOG.warning("build_service_request_failed")

            task.add_done_callback(done)

        server = await asyncio.start_server(accept, _LOOPBACK, 0, backlog=8, start_serving=False)
        try:
            port = server.sockets[0].getsockname()[1]
            host = f"build-{secrets.token_hex(16)}.localhost:{port}"
            receipt = BuildService(task_id, f"http://{host}/", time.time() + self._ttl)
            preview = _Preview(
                receipt,
                asyncio.get_running_loop().time() + self._ttl,
                host.encode("ascii"),
                backend,
                relay_key,
                server,
                cleanup,
            )
            await server.start_serving()
        except BaseException:
            server.close()
            await server.wait_closed()
            raise
        return preview

    async def _expire(self, preview: _Preview) -> None:
        # ponytail: revoke on failed root fetch; add process-exit signal if idle health is needed.
        try:
            async with asyncio.timeout_at(preview.deadline):
                await preview.unavailable.wait()
        except TimeoutError:
            pass
        async with self._lock:
            if self._preview is preview:
                try:
                    await self._remove(preview)
                except TaskConflict:
                    _LOG.warning("build_service_cleanup_failed")

    async def _cleanup(self, cleanup: Callable[[], Awaitable[None]]) -> None:
        try:
            await cleanup()
        except (Exception, asyncio.CancelledError) as error:
            # No new preview if ownership of an earlier container could not be released.
            self._closed = True
            raise TaskConflict("build_service_cleanup_failed") from error

    async def _close_listener(self, preview: _Preview) -> None:
        preview.server.close()
        clients = tuple(preview.clients.items())
        for client, writer in clients:
            writer.transport.abort()
            client.cancel()
        await asyncio.gather(*(client for client, _ in clients), return_exceptions=True)
        await preview.server.wait_closed()

    async def _remove(self, preview: _Preview) -> None:
        self._preview = None
        if preview.expiry is not None and preview.expiry is not asyncio.current_task():
            preview.expiry.cancel()
        try:
            await self._close_listener(preview)
        finally:
            await self._cleanup(preview.cleanup)

    async def _stop(self, task_id: UUID | None) -> bool:
        async with self._lock:
            preview = self._preview
            if preview is None or (task_id is not None and preview.receipt.task_id != task_id):
                return False
            # Ownership stays inside the lock until cleanup has settled, even on cancellation.
            operation = asyncio.create_task(self._remove(preview), name="ember-build-stop")
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                await settled(operation)
                raise
            return True

    async def stop(self, task_id: UUID) -> bool:
        """Stop only this task's preview; caller must already authorize the task ID."""
        return await self._stop(task_id)

    async def discard(self, task_id: UUID) -> None:
        """Revoke a preview after task cancellation or failed commit; mismatch is a no-op."""
        await self.stop(task_id)

    async def _shutdown(self) -> None:
        try:
            await self._stop(None)
        finally:
            await self._openings_drained.wait()

    async def aclose(self) -> None:
        """Close preview admission and drain the retained task on runtime shutdown."""
        self._closed = True
        operation = asyncio.create_task(self._shutdown(), name="ember-build-close")
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            await settled(operation)
            raise
