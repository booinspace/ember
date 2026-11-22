"""Bearer-only OpenAI ingress for the existing gateway and optional memory tools."""

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import ClientDisconnect

from ..inference.contracts import GatewayError, ModelClient
from ..inference.openai_bridge import HostedTools, OpenAIModelBridge, openai_error
from .config import ConfigurationError, DeploymentConfig

TOKEN_FILE = "coding-api.token"


def coding_token(root: Path) -> str:
    """Persist a separate bearer without printing it or sharing gateway credentials."""
    path = root / TOKEN_FILE
    try:
        if not path.exists():
            path.touch(mode=0o600, exist_ok=False)
            path.write_text(secrets.token_urlsafe(32), encoding="ascii")
        token = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise ConfigurationError("Coding API token unavailable.") from error
    if len(token) != 43 or not all(c.isalnum() or c in "-_" for c in token):
        raise ConfigurationError("Coding API token is invalid.")
    return token


def coding_routes(
    config: DeploymentConfig,
    client: ModelClient,
    token: str,
    *,
    hosted_tools: HostedTools | None = None,
) -> APIRouter:
    """Expose only models and completions beneath the browser's HTTPS boundary."""
    router = APIRouter(prefix="/coding/v1")
    active = 0
    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}

    def allowed(request: Request) -> bool:
        credentials = request.headers.getlist("authorization")
        return len(credentials) == 1 and secrets.compare_digest(
            credentials[0].encode(), b"Bearer " + token.encode()
        )

    def error(code: str, status: int) -> JSONResponse:
        return JSONResponse(openai_error(code), status_code=status, headers=headers)

    def bridge() -> OpenAIModelBridge:
        return OpenAIModelBridge(
            client,
            config,
            context_id=uuid4(),
            deadline_at=(datetime.now(UTC) + timedelta(seconds=180)).isoformat(),
            coding=True,
            hosted_tools=hosted_tools,
        )

    @router.get("/models")
    async def models(request: Request) -> JSONResponse:
        if not allowed(request):
            return error("unauthorized", 401)
        return JSONResponse(bridge().models(), headers=headers)

    @router.post("/chat/completions", response_model=None)
    async def completions(request: Request) -> JSONResponse | StreamingResponse:
        nonlocal active
        if not allowed(request):
            return error("unauthorized", 401)
        if active >= 4:
            return error("busy", 429)
        active += 1
        streaming = False
        try:
            body = bytearray()
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 1024 * 1024:
                        return error("request_too_large", 413)
                    body.extend(chunk)
            result = bridge().response(bytes(body))
            if isinstance(result, Awaitable):
                return JSONResponse(await result, headers=headers)
            streaming = True

            async def events() -> AsyncIterator[bytes]:
                nonlocal active
                try:
                    async with aclosing(result) as source:
                        async for frame in source:
                            yield frame
                finally:
                    active -= 1

            return StreamingResponse(events(), media_type="text/event-stream", headers=headers)
        except TimeoutError:
            return error("deadline_exceeded", 408)
        except ClientDisconnect:
            return error("client_disconnected", 400)
        except GatewayError as exc:
            if exc.code in {"busy", "background_queue_full"}:
                return error(exc.code, 429)
            if exc.code == "request_too_large":
                return error(exc.code, 413)
            if exc.code in {
                "quiescence_required",
                "gateway_transport_failed",
                "draining",
                "upstream_vision_unavailable",
            }:
                return error(exc.code, 503)
            return error(exc.code, 400)
        finally:
            if not streaming:
                active -= 1

    return router
