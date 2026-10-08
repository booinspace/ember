"""Local P0 service lifecycle, durable uncertainty and single process ownership."""

import asyncio
import hmac
import os
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import BoundedSemaphore
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import UUID4, ValidationError

from ..runtime.config import DeploymentConfig, RuntimeModel
from .contracts import GatewayError, GatewayRequest
from .gateway import P0Gateway
from .ollama import OllamaUpstream


class RequestState(RuntimeModel):
    """Content-free crash marker; a stale running job requires verified operator quiescence."""

    status: Literal["running", "complete", "uncertain"]
    request_id: str


class SessionState(RuntimeModel):
    """Durable connected ownership; gateway restart never infers disconnection."""

    sessions: tuple[UUID4, ...] = ()


class SessionInput(RuntimeModel):
    """Trusted foreground lifecycle instruction, never prompt/model metadata."""

    connected: bool


class ServiceOwner:
    """Acquire an OS lock before writing credentials/state; never terminate another service."""

    def __init__(self, root: Path, *, acknowledge_quiescence: bool = False) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.lock = (root / "gateway.lock").open("a+b")
        try:
            self.lock.seek(0)
            if os.name == "nt":
                import msvcrt

                if self.lock.read(1) == b"":
                    self.lock.write(b"0")
                    self.lock.flush()
                self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.lock.close()
            raise GatewayError("gateway_already_owned") from error
        self.slots = BoundedSemaphore(4)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ember-gateway-state")
        try:
            token_path = root / "gateway.token"
            if not token_path.exists():
                with token_path.open("x", encoding="utf-8") as stream:
                    stream.write(secrets.token_urlsafe(32))
            self.token = token_path.read_text(encoding="utf-8").strip()
            background_path = root / "gateway-background.token"
            if not background_path.exists():
                with background_path.open("x", encoding="utf-8") as stream:
                    stream.write(secrets.token_urlsafe(32))
            self.background_token = background_path.read_text(encoding="utf-8").strip()
            if not 32 <= len(self.background_token) <= 128 or self.background_token == self.token:
                raise GatewayError("invalid_gateway_credentials")
            sessions_path = root / "gateway-sessions.json"
            self.sessions = (
                SessionState.model_validate_json(sessions_path.read_bytes()).sessions
                if sessions_path.exists()
                else ()
            )
            if len(self.token) < 32 or len(self.token) > 128:
                raise GatewayError("invalid_gateway_credentials")
            path = root / "gateway-state.json"
            state = RequestState.model_validate_json(path.read_bytes()) if path.exists() else None
            self.uncertain = bool(state and state.status != "complete")
            if acknowledge_quiescence:
                # This flag is an operator assertion, not a proof inferred from HTTP cancellation.
                self.write(RequestState(status="complete", request_id="operator-quiescence"))
                self.uncertain = False
                self.write_sessions(SessionState())
                self.sessions = ()
        except Exception:
            self.close()
            raise

    def write(self, state: RequestState) -> None:
        """Atomically replace metadata only; never store model context or output."""
        temporary = self.root / "gateway-state.next"
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(state.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.root / "gateway-state.json")

    def write_sessions(self, state: SessionState) -> None:
        """Publish only session identities, retaining conservative ownership across restart."""
        temporary = self.root / "gateway-sessions.next"
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(state.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.root / "gateway-sessions.json")

    async def record_sessions(self, sessions: tuple[UUID, ...]) -> None:
        """Serialize lifecycle journaling on the existing bounded worker off the loop."""
        state = SessionState(sessions=sessions)
        if not self.slots.acquire(blocking=False):
            raise GatewayError("journal_busy")
        try:
            future = self.pool.submit(self.write_sessions, state)
        except RuntimeError as error:
            self.slots.release()
            raise GatewayError("journal_closed") from error
        future.add_done_callback(lambda _: self.slots.release())
        await asyncio.shield(asyncio.wrap_future(future))

    async def record(self, status: str, request: GatewayRequest) -> None:
        """One request owns this bounded journal worker, away from the realtime event loop."""
        state = RequestState.model_validate(
            {"status": status, "request_id": str(request.request_id)}
        )
        if not self.slots.acquire(blocking=False):
            raise GatewayError("journal_busy")
        try:
            future = self.pool.submit(self.write, state)
        except RuntimeError as error:
            self.slots.release()
            raise GatewayError("journal_closed") from error
        future.add_done_callback(lambda _: self.slots.release())
        await asyncio.shield(asyncio.wrap_future(future))

    def close(self) -> None:
        """Drain the journal worker, then release this process's lock."""
        self.pool.shutdown(wait=True)
        self.lock.close()


def create_app(
    gateway: P0Gateway,
    token: str,
    shutdown: Callable[[], None] | None = None,
    *,
    background_token: str | None = None,
) -> FastAPI:
    """Expose authenticated metadata/NDJSON; reject invalid requests without echoing their body."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        del app
        yield
        await gateway.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(request: Request) -> bool:
        return hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token)

    def background_authorized(request: Request) -> bool:
        return background_token is not None and hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + background_token
        )

    @app.put("/v1/realtime/{session_id}")
    async def session(session_id: UUID4, request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse({"code": "unauthorized"}, status_code=401)
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024:
                    return JSONResponse({"code": "request_too_large"}, status_code=413)
            state = SessionInput.model_validate_json(body)
            await gateway.session(session_id, connected=state.connected)
        except ValidationError:
            return JSONResponse({"code": "invalid_request"}, status_code=422)
        except GatewayError as error:
            return JSONResponse({"code": error.code}, status_code=409)
        return JSONResponse({"code": "reserved" if state.connected else "released"})

    @app.post("/v1/shutdown")
    async def stop(request: Request) -> JSONResponse:
        if not authorized(request):
            return JSONResponse({"code": "unauthorized"}, status_code=401)
        if shutdown is None:
            return JSONResponse({"code": "shutdown_unavailable"}, status_code=409)
        gateway.closed = True
        shutdown()
        return JSONResponse({"code": "draining"})

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        if not (authorized(request) or background_authorized(request)):
            return JSONResponse({"code": "unauthorized"}, status_code=401)
        return JSONResponse((await gateway.health()).model_dump(mode="json"))

    @app.post("/v1/generate", response_model=None)
    async def generate(request: Request) -> JSONResponse | StreamingResponse:
        if not (authorized(request) or background_authorized(request)):
            return JSONResponse({"code": "unauthorized"}, status_code=401)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                return JSONResponse({"code": "request_too_large"}, status_code=413)
        try:
            value = GatewayRequest.model_validate_json(body)
            permitted = (
                value.workload == "P0" if authorized(request) else value.workload in {"P3", "P4"}
            )
            if not permitted:
                return JSONResponse({"code": "workload_not_authorized"}, status_code=403)
            job = await gateway.admit(value)
        except ValidationError:
            return JSONResponse({"code": "invalid_request"}, status_code=422)
        except GatewayError as error:
            return JSONResponse({"code": error.code}, status_code=409)

        async def lines() -> AsyncIterator[bytes]:
            async for event in job.events():
                yield (event.model_dump_json() + "\n").encode("utf-8")

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    return app


def serve(config: DeploymentConfig, *, acknowledge_quiescence: bool = False) -> None:
    """Run the owned foreground gateway; shutdown drains native work and never stops Ollama."""
    if config.services.gateway.mode != "managed":
        raise GatewayError("gateway_not_managed")
    owner = ServiceOwner(config.runtime_root, acknowledge_quiescence=acknowledge_quiescence)
    try:
        gateway = P0Gateway(
            config,
            OllamaUpstream(config),
            record=owner.record,
            uncertain=owner.uncertain,
            sessions=owner.sessions,
            record_sessions=owner.record_sessions,
        )
        origin = urlsplit(config.services.gateway.endpoint)
        server: uvicorn.Server

        def shutdown() -> None:
            server.should_exit = True

        server = uvicorn.Server(
            uvicorn.Config(
                create_app(gateway, owner.token, shutdown, background_token=owner.background_token),
                host=origin.hostname or "127.0.0.1",
                port=origin.port or 11435,
                access_log=False,
                log_level="warning",
            )
        )
        server.run()
    finally:
        owner.close()
