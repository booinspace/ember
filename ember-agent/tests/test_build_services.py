import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from ember_agent.runtime.build_container import (
    _protected_request,  # pyright: ignore[reportPrivateUsage]
)
from ember_agent.runtime.build_services import BuildServices
from ember_agent.runtime.task_store import TaskConflict

_KEY = "a" * 43


@asynccontextmanager
async def backend(status: bytes = b"200 OK") -> AsyncGenerator[tuple[str, list[bytes]]]:
    received: list[bytes] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await _protected_request(reader, _KEY.encode(), 8080)
            received.append(request)
            method, path, _ = request.split(b" ", 2)
            text = b"hello"
            code = status
            if path == b"/redirect":
                code = b"302 Found"
            elif path == b"/auth":
                code = b"401 Unauthorized"
            elif path == b"/large":
                text = b"x" * (4 * 1024 * 1024 + 1)
            elif path == b"/large-header":
                writer.write(b"HTTP/1.1 200 OK\r\nX-Large: " + b"x" * 8192 + b"\r\n\r\n")
                return
            writer.write(
                b"HTTP/1.1 " + code + b"\r\n"
                b"Content-Type: text/html; charset=utf-8\r\n"
                b"Location: http://127.0.0.1:42/\r\n"
                b"Set-Cookie: injected=secret\r\n"
                b'WWW-Authenticate: Basic realm="test"\r\n'
                b"Access-Control-Allow-Origin: *\r\n"
                b"Content-Length: "
                + str(len(text)).encode()
                + b"\r\n\r\n"
                + (text if method == b"GET" else b"")
            )
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, TaskConflict):
            return
        finally:
            writer.close()

    async with await asyncio.start_server(serve, "127.0.0.1", 0) as server:
        yield f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", received


def test_verified_static_preview_proxy() -> None:
    async def run() -> None:
        cleaned: list[str] = []

        async def close() -> None:
            cleaned.append("stopped")

        async with backend() as (origin, requests):
            services = BuildServices()
            task_id = uuid4()
            try:
                url = await services.retain(task_id, 8080, origin, close, relay_key=_KEY)
                assert url is not None
                assert len(requests) == 1 and requests[0].startswith(b"GET / HTTP/1.1")
                preview = urlsplit(url)
                assert preview.scheme == "http" and preview.hostname is not None
                assert preview.hostname.startswith("build-") and preview.hostname.endswith(
                    ".localhost"
                )
                assert services.current is None
                services.activate(uuid4())
                assert services.current is None

                async with httpx.AsyncClient(trust_env=False) as client:
                    with pytest.raises(httpx.RemoteProtocolError):
                        await client.get(origin)
                    proxy = f"http://127.0.0.1:{preview.port}"
                    with pytest.raises((httpx.RemoteProtocolError, httpx.ReadError)):
                        await client.get(proxy, headers={"Host": preview.netloc})
                    assert len(requests) == 1
                    services.activate(task_id)
                    receipt = services.current
                    assert receipt is not None and receipt.task_id == task_id
                    assert origin not in receipt.url and isinstance(receipt.expires_at, float)
                    assert (await client.get(proxy)).status_code == 421
                    assert len(requests) == 1
                    client.headers.update(
                        {
                            "Host": preview.netloc,
                            "Cookie": "private=cookie",
                            "Authorization": "Bearer secret",
                            "Referer": "http://host/private",
                            "Origin": "http://host",
                            "X-Forwarded-For": "private",
                            "Range": "bytes=1-2",
                        }
                    )
                    response = await client.get(proxy)
                    assert response.status_code == 200 and response.text == "hello"
                    assert "sandbox " in response.headers["content-security-policy"]
                    for header in (
                        "set-cookie",
                        "location",
                        "www-authenticate",
                        "access-control-allow-origin",
                    ):
                        assert header not in response.headers
                    for header in (
                        b"cookie:",
                        b"authorization:",
                        b"referer:",
                        b"origin:",
                        b"x-forwarded-for:",
                        b"range:",
                    ):
                        assert header not in requests[-1].lower()
                    head = await client.head(proxy)
                    assert head.status_code == 200 and not head.content
                    assert head.headers["content-length"] == "5"
                    assert (await client.post(proxy)).status_code == 405
                    assert (await client.get(proxy + "?secret=x")).status_code == 400
                    assert (await client.request("GET", proxy, content=b"x")).status_code == 400
                    for path in ("/redirect", "/auth", "/large", "/large-header"):
                        response = await client.get(proxy + path)
                        assert response.status_code == 502
                        assert "location" not in response.headers
                    assert requests[-1].startswith(b"GET /large-header HTTP/1.1")
                await services.discard(uuid4())
                assert services.current is not None
                assert await services.stop(task_id)
                assert cleaned == ["stopped"] and services.current is None
                assert not await services.stop(task_id)
                with pytest.raises(OSError):
                    await asyncio.open_connection("127.0.0.1", preview.port)
            finally:
                await services.aclose()

    asyncio.run(run())


def test_service_verification_and_exclusive_ownership() -> None:
    async def run() -> None:
        cleanups: list[str] = []

        async def first() -> None:
            cleanups.append("first")

        async def rejected() -> None:
            cleanups.append("rejected")

        async with (
            backend(b"302 Found") as (bad_relay, bad_requests),
            backend() as (relay, requests),
        ):
            services = BuildServices()
            assert await services.retain(uuid4(), 8080, bad_relay, rejected, relay_key=_KEY) is None
            assert len(bad_requests) == 1 and services.current is None
            for port, origin in [
                (80, relay),
                (8080, relay.replace("127.0.0.1", "localhost")),
                (8080, relay + "/"),
                (8080, relay + "@example.org"),
            ]:
                assert (
                    await services.retain(uuid4(), port, origin, rejected, relay_key=_KEY) is None
                )
            assert not requests
            task_id = uuid4()
            assert await services.retain(task_id, 8080, relay, first, relay_key=_KEY)
            assert await services.retain(uuid4(), 8080, relay, rejected, relay_key=_KEY) is None
            assert len(requests) == 1 and services.current is None
            services.activate(task_id)
            assert services.current is not None
            assert cleanups == ["rejected"] * 6
            await services.aclose()
            assert cleanups[-1] == "first" and services.current is None
            assert await services.retain(uuid4(), 8080, relay, rejected, relay_key=_KEY) is None

    asyncio.run(run())


def test_expiry_and_cleanup_failure_fail_closed(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> None:
        with pytest.raises(ValueError):
            BuildServices(ttl_seconds=1801)
        async with backend() as (relay, _):
            expired = asyncio.Event()

            async def close() -> None:
                expired.set()
                raise OSError("private details")

            services = BuildServices(ttl_seconds=0.02)
            assert await services.retain(uuid4(), 8080, relay, close, relay_key=_KEY)
            await asyncio.wait_for(expired.wait(), 2)
            assert services.current is None
            with pytest.raises(TaskConflict, match="build_service_cleanup_failed"):
                await services.retain(uuid4(), 8080, relay, close, relay_key=_KEY)
            await services.aclose()

            services = BuildServices()
            task_id = uuid4()
            assert await services.retain(task_id, 8080, relay, close, relay_key=_KEY)
            with pytest.raises(TaskConflict, match="build_service_cleanup_failed"):
                await services.stop(task_id)
            assert services.current is None
            await services.aclose()

    asyncio.run(run())
    assert "build_service_cleanup_failed" in caplog.text and "private details" not in caplog.text


def test_failed_root_revokes_preview() -> None:
    async def run() -> None:
        cleaned = asyncio.Event()

        async def close() -> None:
            cleaned.set()

        services = BuildServices()
        task_id = uuid4()
        try:
            async with backend() as (relay, _):
                url = await services.retain(task_id, 8080, relay, close, relay_key=_KEY)
                assert url is not None
                services.activate(task_id)
            address = urlsplit(url)
            async with httpx.AsyncClient(trust_env=False) as client:
                response = await client.get(
                    f"http://127.0.0.1:{address.port}/", headers={"Host": address.netloc}
                )
                assert response.status_code == 502
            await asyncio.wait_for(cleaned.wait(), 1)
            assert services.current is None
        finally:
            await services.aclose()

    asyncio.run(run())
