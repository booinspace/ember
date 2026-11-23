"""Outbound MCP sessions. Transport policy is separate from tool authorization."""

import asyncio
import os
import re
import signal
import subprocess
import tempfile
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import Context
from types import TracebackType
from typing import Self, cast

import anyio
import httpx
import httpx2
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import Client, ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._stream_protocols import ReadStream, WriteStream
from mcp.shared.message import SessionMessage
from pydantic import JsonValue, TypeAdapter

from ..web_evidence import WebFailure
from .mcp_contracts import MCPServerSettings
from .task_store import TaskConflict

WIRE_BYTES = 65536
CREATE_SUSPENDED = 0x00000004  # Win32 flag not exported by Python's subprocess module.
_OBJECT = TypeAdapter(dict[str, JsonValue])


class _LimitedBody(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream) -> None:
        self.stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        total = 0
        async for chunk in self.stream:
            total += len(chunk)
            if total > WIRE_BYTES:
                raise TaskConflict("mcp_wire_limit")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


async def _http_response(response: httpx2.Response) -> None:
    if 300 <= response.status_code < 400:
        raise TaskConflict("mcp_redirect_denied")
    if response.headers.get("Content-Encoding", "identity").strip().casefold() != "identity":
        raise TaskConflict("mcp_compression_unsupported")
    response.stream = _LimitedBody(cast(httpx2.AsyncByteStream, response.stream))


async def _finish_owned[T](future: asyncio.Future[T]) -> tuple[T, bool]:
    """Retain resource ownership through repeated asyncio and AnyIO cancellation."""
    interrupted = False
    while True:
        with anyio.CancelScope(shield=True):
            try:
                return await asyncio.shield(future), interrupted
            except asyncio.CancelledError:
                if future.cancelled():
                    raise
                interrupted = True


async def _stop(process: asyncio.subprocess.Process, job: object | None) -> None:
    if process.stdin is not None:
        process.stdin.close()
    transport = cast(asyncio.SubprocessTransport, vars(process)["_transport"])
    for descriptor in (0, 1, 2):
        pipe = transport.get_pipe_transport(descriptor)
        if pipe is not None:
            pipe.close()
    if job is not None:
        from .windows_job import WindowsJob

        assert isinstance(job, WindowsJob)
        await asyncio.to_thread(job.terminate_and_wait, 3.0)
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.returncode is None:
        process.kill()
    async with asyncio.timeout(3):
        await process.wait()
        if process.stdin is not None:
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass


def _process_handle(process: asyncio.subprocess.Process) -> int:
    # asyncio exposes no public original process handle; retain its native transport.
    transport = cast(asyncio.SubprocessTransport, vars(process)["_transport"])
    native_process: object = transport.get_extra_info("subprocess")
    native: object = getattr(native_process, "_handle", None)
    if not isinstance(native, int):
        raise TaskConflict("mcp_process_ownership_unavailable")
    return native


def _owned_job(process: asyncio.subprocess.Process) -> object | None:
    if os.name != "nt":
        return None
    from .windows_job import WindowsJob

    return WindowsJob(_process_handle(process))


@asynccontextmanager
async def _stdio(
    settings: MCPServerSettings, environment: dict[str, str], directory: str
) -> AsyncGenerator[
    tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
    ],
    None,
]:
    """Use SDK messages over owned bounded pipes; no inherited shell or stderr logging."""
    creation = asyncio.create_task(
        asyncio.create_subprocess_exec(
            settings.command,
            *settings.arguments,
            cwd=directory,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            limit=WIRE_BYTES,
            start_new_session=os.name != "nt",
            creationflags=(CREATE_SUSPENDED | subprocess.CREATE_NEW_PROCESS_GROUP)
            if os.name == "nt"
            else 0,
        )
    )
    try:
        process = await asyncio.shield(creation)
    except asyncio.CancelledError:
        process, _ = await _finish_owned(creation)
        try:
            job = _owned_job(process)
        except BaseException:
            # Windows is still suspended here, so it cannot have forked an unowned child.
            await _finish_owned(asyncio.create_task(_stop(process, None)))
            raise
        await _finish_owned(asyncio.create_task(_stop(process, job)))
        raise
    incoming, read = anyio.create_memory_object_stream[SessionMessage | Exception](1)
    write, outgoing = anyio.create_memory_object_stream[SessionMessage](1)
    job: object | None = None
    tasks: list[asyncio.Task[None]] = []
    wire_failure: TaskConflict | None = None
    try:
        job = _owned_job(process)
        if job is not None:
            from .windows_job import WindowsJob

            assert isinstance(job, WindowsJob)
            job.resume_suspended(_process_handle(process))

        async def receive() -> None:
            nonlocal wire_failure
            assert process.stdout is not None
            try:
                async with incoming:
                    for _ in range(128):
                        try:
                            line = await process.stdout.readline()
                            if not line:
                                return
                            if len(line) > WIRE_BYTES:
                                raise ValueError()
                            message = types.jsonrpc_message_adapter.validate_json(
                                line, by_name=False
                            )
                        except (ValueError, RecursionError):
                            wire_failure = TaskConflict("mcp_invalid_frame")
                            await incoming.send(wire_failure)
                            return
                        await incoming.send(SessionMessage(message))
                    wire_failure = TaskConflict("mcp_message_limit")
                    await incoming.send(wire_failure)
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                pass

        async def send() -> None:
            assert process.stdin is not None
            async with outgoing:
                async for message in outgoing:
                    data = message.message.model_dump_json(
                        by_alias=True, exclude_none=True
                    ).encode()
                    if len(data) > WIRE_BYTES:
                        raise TaskConflict("mcp_request_too_large")
                    process.stdin.write(data + b"\n")
                    await process.stdin.drain()

        tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
        yield read, write
    except asyncio.CancelledError:
        raise
    except BaseException:
        if wire_failure is not None:
            raise wire_failure from None
        raise
    else:
        if wire_failure is not None:
            raise wire_failure
    finally:

        async def cleanup() -> None:
            try:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await read.aclose()
                await write.aclose()
                await incoming.aclose()
                await outgoing.aclose()
            finally:
                await _stop(process, job)

        _, interrupted = await _finish_owned(asyncio.create_task(cleanup()))
        if interrupted:
            raise asyncio.CancelledError()


type Inbound = SessionMessage | Exception


class _DirectResults:
    """Reject nonterminal results before legacy SDK validation can strip their marker."""

    def __init__(self, stream: ReadStream[Inbound]) -> None:
        self._stream = stream

    @property
    def last_context(self) -> Context | None:
        context = getattr(self._stream, "last_context", None)
        return context if isinstance(context, Context) else None

    async def receive(self) -> Inbound:
        item = await self._stream.receive()
        if (
            isinstance(item, SessionMessage)
            and isinstance(message := item.message, types.JSONRPCResponse)
            and message.result.get("resultType", "complete") != "complete"
        ):
            return SessionMessage(
                types.JSONRPCError(
                    jsonrpc="2.0",
                    id=message.id,
                    error=types.ErrorData(code=types.INTERNAL_ERROR, message="Nonterminal result"),
                ),
                metadata=item.metadata,
            )
        return item

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def __aenter__(self) -> Self:
        await self._stream.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        return await self._stream.__aexit__(exc_type, exc_val, exc_tb)

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Inbound:
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None


_PUBLIC_WIRE_LIMIT = 131_072
_HEADERS = {"accept": "application/json, text/event-stream", "accept-encoding": "identity"}
_FRAME_END = re.compile(rb"\r?\n\r?\n")
_SESSION_ID = re.compile(r"[\x21-\x7e]{1,1024}")


class MCPWireError(Exception):
    """Public transport failure with no upstream text."""

    def __init__(self, code: WebFailure) -> None:
        self.code: WebFailure = code
        super().__init__(code)


class _SingleCallHTTP:
    """SDK write stream with POST-only transport; no unsolicited traffic or replay."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        endpoint: str,
        responses: MemoryObjectSendStream[SessionMessage | Exception],
        bearer: str,
    ) -> None:
        self.http, self.endpoint, self.responses = http, endpoint, responses
        self.bearer = bearer
        self.headers = dict(_HEADERS)
        self.step = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.responses.aclose()

    async def send(self, item: SessionMessage, /) -> None:
        message = item.message
        if (
            isinstance(message, types.JSONRPCNotification)
            and message.method == "notifications/cancelled"
        ):
            return  # Local cancellation closes the POST. Never issue more public requests.
        methods = ("initialize", "notifications/initialized", "tools/call")
        if (
            not isinstance(message, types.JSONRPCRequest | types.JSONRPCNotification)
            or self.step >= len(methods)
            or message.method != methods[self.step]
        ):
            raise MCPWireError("invalid_response")
        self.step += 1
        payload = _OBJECT.validate_json(message.model_dump_json(by_alias=True, exclude_none=True))
        if isinstance(params := payload.get("params"), dict):
            params.pop("_meta", None)  # No SDK trace or model metadata at public egress.
        self.http.cookies.clear()
        async with self.http.stream(
            "POST", self.endpoint, headers=self.headers, json=payload
        ) as response:
            if isinstance(message, types.JSONRPCNotification):
                _require_status(response, 202)
                return
            result = await _result(response, message.id)
            if message.method == "initialize":
                version = result.get("protocolVersion")
                caps = result.get("capabilities")
                ids = response.headers.get_list("mcp-session-id")
                if (
                    version != "2025-06-18"
                    or not isinstance(caps, dict)
                    or not isinstance(caps.get("tools"), dict)
                    or len(ids) > 1
                    or (ids and not _SESSION_ID.fullmatch(ids[0]))
                ):
                    raise MCPWireError("invalid_response")
                assert isinstance(version, str)
                self.headers["mcp-protocol-version"] = version
                if ids:
                    self.headers["mcp-session-id"] = ids[0]
            else:
                if not _terminal(result) or type(result.get("isError", False)) is not bool:
                    raise MCPWireError("invalid_response")
                fields = (result.get("content"), result.get("structuredContent"))
                if self.bearer and any(_contains(field, self.bearer) for field in fields):
                    raise MCPWireError("invalid_response")
                if session_id := self.headers.get("mcp-session-id"):
                    content, structured = fields
                    blocks = (
                        [
                            {key: value for key, value in block.items() if key != "type"}
                            if isinstance(block, dict)
                            else block
                            for block in content
                        ]
                        if isinstance(content, list)
                        else content
                    )
                    if _session_echo(blocks, session_id) or _session_echo(structured, session_id):
                        raise MCPWireError("invalid_response")
        await self.responses.send(
            SessionMessage(
                types.JSONRPCResponse(
                    jsonrpc="2.0",
                    id=message.id,
                    result=result,
                )
            )
        )


class MCPClient:
    """Shared outbound client; fresh session per operation with explicit transport policy."""

    @asynccontextmanager
    async def direct(
        self, settings: MCPServerSettings, environment: dict[str, str], bearer: str | None
    ) -> AsyncGenerator[Client, None]:
        async with AsyncExitStack() as stack:
            if settings.transport == "stdio":
                temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="ember-mcp-"))
                transport = _stdio(settings, environment, settings.working_directory or temporary)
            else:
                headers = {"Accept-Encoding": "identity"}
                if bearer:
                    headers["Authorization"] = "Bearer " + bearer
                http = await stack.enter_async_context(
                    httpx2.AsyncClient(
                        headers=headers,
                        trust_env=False,
                        follow_redirects=False,
                        timeout=settings.timeout_seconds,
                        limits=httpx2.Limits(max_connections=2, max_keepalive_connections=0),
                        event_hooks={"response": [_http_response]},
                    )
                )
                transport = streamable_http_client(
                    settings.url,
                    http_client=http,
                    max_sse_event_size=WIRE_BYTES,
                )

            @asynccontextmanager
            async def checked_transport() -> AsyncGenerator[
                tuple[ReadStream[Inbound], WriteStream[SessionMessage]], None
            ]:
                async with transport as (read, write):
                    yield _DirectResults(read), write

            async with Client(
                checked_transport(),
                read_timeout_seconds=settings.timeout_seconds,
                cache=None,
                client_info=types.Implementation(name="ember-direct-tools", version="0.1.0"),
            ) as client:
                yield client

    async def public_call(
        self,
        endpoint: str,
        name: str,
        arguments: dict[str, JsonValue],
        *,
        bearer: str = "",
        ipv4: bool = False,
        timeout: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> dict[str, JsonValue]:
        """Initialize and invoke one pinned tool. No discovery, extra HTTP methods or callbacks."""
        if bearer and (bearer in endpoint or bearer in name or _contains(arguments, bearer)):
            raise MCPWireError("input_rejected")
        headers = {"Authorization": "Bearer " + bearer} if bearer else {}
        async with (
            asyncio.timeout(timeout),
            httpx.AsyncClient(
                transport=transport
                if transport is not None
                else httpx.AsyncHTTPTransport(
                    local_address="0.0.0.0" if ipv4 else None,
                    trust_env=False,
                ),
                headers=headers,
                follow_redirects=False,
                trust_env=False,
                timeout=timeout,
            ) as http,
        ):
            send, read = anyio.create_memory_object_stream[SessionMessage | Exception](1)
            failure: Exception | None = None
            async with (
                send,
                read,
                ClientSession(read, _SingleCallHTTP(http, endpoint, send, bearer)) as session,
            ):
                try:
                    initial = await session.send_request(
                        types.InitializeRequest(
                            params=types.InitializeRequestParams(
                                protocol_version="2025-06-18",
                                capabilities=types.ClientCapabilities(),
                                client_info=types.Implementation(
                                    name="ember-public-web", version="1"
                                ),
                            )
                        ),
                        types.InitializeResult,
                    )
                    session.adopt(initial)
                    await session.send_notification(types.InitializedNotification())
                    return await call_once(session, name, arguments)
                except Exception as error:
                    # Exit SDK task group cleanly before mapping content-free failures.
                    failure = error
            assert failure is not None
            raise failure


async def call_once(
    session: ClientSession,
    name: str,
    arguments: dict[str, JsonValue],
    timeout: float | None = None,
) -> dict[str, JsonValue]:
    """One SDK request, bypassing auto-listing, SDK schema validation and input-required retries."""
    result = await session.send_request(
        types.CallToolRequest(params=types.CallToolRequestParams(name=name, arguments=arguments)),
        types.CallToolResult,
        request_read_timeout_seconds=timeout,
    )
    raw = _OBJECT.validate_json(result.model_dump_json(by_alias=True, exclude_none=True))
    if not _terminal(raw):
        raise TaskConflict("mcp_tool_failed")
    return raw


def _terminal(result: dict[str, JsonValue]) -> bool:
    return result.get("resultType", "complete") == "complete"


def _contains(value: JsonValue, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(secret in key or _contains(child, secret) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains(child, secret) for child in value)
    return value is not None and secret in str(value)


def _session_echo(value: JsonValue, session_id: str) -> bool:
    if isinstance(value, str):
        if len(session_id) >= 8:
            return session_id in value
        # ponytail: short IDs match standalone tokens, not punctuation inside URLs.
        wrappers = r"[\"'`()\[\]{}<>,;:.!?]*"
        pattern = wrappers + re.escape(session_id) + wrappers
        return any(re.fullmatch(pattern, word) for word in value.split())
    if isinstance(value, dict):
        return any(_session_echo(child, session_id) for child in value.values())
    if isinstance(value, list):
        return any(_session_echo(child, session_id) for child in value)
    return False


def _require_status(response: httpx.Response, expected: int) -> None:
    if response.status_code in {401, 403}:
        raise MCPWireError("access_denied")
    if response.status_code == 429:
        raise MCPWireError("rate_limited")
    if response.status_code != expected:
        raise MCPWireError("unavailable")


async def _result(response: httpx.Response, request_id: int | str) -> dict[str, JsonValue]:
    _require_status(response, 200)
    mime = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if (
        mime not in {"application/json", "text/event-stream"}
        or response.headers.get("content-encoding", "identity").lower() != "identity"
    ):
        raise MCPWireError("invalid_response")
    raw = bytearray()
    scanned = offset = 0
    async for chunk in response.aiter_raw():
        if len(raw) + len(chunk) > _PUBLIC_WIRE_LIMIT:
            raise MCPWireError("response_bound")
        raw.extend(chunk)
        if mime == "text/event-stream":
            while match := _FRAME_END.search(raw, scanned):
                fields = raw[offset : match.start()].splitlines()
                data = [line[5:].removeprefix(b" ") for line in fields if line.startswith(b"data:")]
                if data and any(data):
                    # Stop on the one JSON-RPC response, not when the server closes its stream.
                    return _message(b"\n".join(data), request_id)
                scanned = offset = match.end()
            scanned = max(offset, len(raw) - 3)
    if mime == "application/json":
        return _message(bytes(raw), request_id)
    raise MCPWireError("invalid_response")


def _message(payload: bytes, request_id: int | str) -> dict[str, JsonValue]:
    message = _OBJECT.validate_json(payload)
    received_id = message.get("id")
    if (
        message.get("jsonrpc") != "2.0"
        or type(received_id) is not type(request_id)
        or received_id != request_id
    ):
        raise MCPWireError("invalid_response")
    if "error" in message and "result" not in message:
        raise MCPWireError("unavailable")
    result = message.get("result")
    if "error" in message or not isinstance(result, dict):
        raise MCPWireError("invalid_response")
    return result
