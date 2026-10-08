"""Bounded NDJSON bytes for both native and gateway streaming adapters."""

from collections.abc import AsyncGenerator

import httpx

from .contracts import GatewayError


async def bounded_lines(response: httpx.Response) -> AsyncGenerator[str, None]:
    """Reject oversized/incomplete/invalid UTF-8 frames without unbounded line accumulation."""
    pending = bytearray()
    async for chunk in response.aiter_bytes():
        pending.extend(chunk)
        while True:
            position = pending.find(b"\n")
            if position < 0:
                break
            if position > 1024 * 1024:
                raise GatewayError("stream_frame_too_large")
            raw = bytes(pending[:position])
            del pending[: position + 1]
            if raw.strip():
                try:
                    yield raw.decode("utf-8")
                except UnicodeError as error:
                    raise GatewayError("stream_invalid_encoding") from error
        if len(pending) > 1024 * 1024:
            raise GatewayError("stream_frame_too_large")
    if pending.strip():
        raise GatewayError("stream_frame_incomplete")
