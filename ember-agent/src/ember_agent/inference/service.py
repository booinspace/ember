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

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from ..runtime.config import DeploymentConfig, RuntimeModel
from .contracts import GatewayError, GatewayRequest
from .gateway import P0Gateway
from .ollama import OllamaUpstream


class RequestState(RuntimeModel):
    """Content-free crash marker; a stale running job requires verified operator quiescence."""

    status: Literal["running", "complete", "uncertain"]
    request_id: str


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
            if len(self.token) < 32 or len(self.token) > 128:
                raise GatewayError("invalid_gateway_credentials")
            path = root / "gateway-state.json"
            state = RequestState.model_validate_json(path.read_bytes()) if path.exists() else None
            self.uncertain = bool(state and state.status != "complete")
            if acknowledge_quiescence:
                # This flag is an operator assertion, not a proof inferred from HTTP cancellation.
                self.write(RequestState(status="complete", request_id="operator-quiescence"))
                self.uncertain = False
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
    gateway: P0Gateway, token: str, shutdown: Callable[[], None] | None = None
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
        if not authorized(request):
            return JSONResponse({"code": "unauthorized"}, status_code=401)
        return JSONResponse((await gateway.health()).model_dump(mode="json"))

    @app.post("/v1/generate", response_model=None)
    async def generate(request: Request) -> JSONResponse | StreamingResponse:
        if not authorized(request):
            return JSONResponse({"code": "unauthorized"}, status_code=401)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                return JSONResponse({"code": "request_too_large"}, status_code=413)
        try:
            value = GatewayRequest.model_validate_json(body)
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
            config, OllamaUpstream(config), record=owner.record, uncertain=owner.uncertain
        )
        origin = urlsplit(config.services.gateway.endpoint)
        server: uvicorn.Server

        def shutdown() -> None:
            server.should_exit = True

        server = uvicorn.Server(
            uvicorn.Config(
                create_app(gateway, owner.token, shutdown),
                host=origin.hostname or "127.0.0.1",
                port=origin.port or 11435,
                access_log=False,
                log_level="warning",
            )
        )
        server.run()
    finally:
        owner.close()
