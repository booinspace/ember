"""Authenticated application controls share the runtime owner, never the inference token."""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import UUID4, Field

from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.selection import RecallSelection

from ..character.core import CharacterCore
from ..inference.contracts import GatewayError
from ..memory.deep_contracts import DeepRecallRequest, DeepRecallResult
from ..memory.deep_recall import DeepRecallService
from ..realtime.contracts import RoomBinding
from .config import RuntimeModel
from .controls import CaptureControl, RuntimeControls

COOKIE = "ember_session"


class Bootstrap(RuntimeModel):
    token: str = Field(min_length=32, max_length=128, repr=False)


class InvestigationInput(RuntimeModel):
    request_id: Annotated[UUID4, Field(strict=False)]
    text: str = Field(min_length=1, max_length=1600, repr=False)


class BrowserControl:
    """One character/session-bound local operator surface and bounded task owner.

    Bootstrap uses a private activation file / URL fragment rather than query
    credentials. The browser receives a short-lived room capability, never the
    gateway token or LiveKit server secret. No operation starts a second engine.
    """

    def __init__(
        self,
        *,
        origin: str,
        assets: Path,
        binding: RoomBinding,
        core: CharacterCore,
        deep: DeepRecallService,
        controls: RuntimeControls,
        room_token: Callable[[], str],
        answer: Callable[[DeepRecallResult], Awaitable[str]],
        voice_ready: bool = False,
        voice_status: str = "disabled",
    ) -> None:
        self.origin = origin.rstrip("/")
        self.assets = assets
        self.binding = binding
        self.core = core
        self.deep = deep
        self.controls = controls
        self.room_token = room_token
        self.answer = answer
        self.voice_ready = voice_ready
        self.voice_status = voice_status
        self.activation = secrets.token_urlsafe(48)
        self._session = secrets.token_urlsafe(48)
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._statuses: dict[UUID, str] = {}
        self._closed = False
        self.worker_failures = 0
        self.app = self._app()

    async def aclose(self) -> None:
        """Stop admission, cancel owned requests and drain before the runtime engine closes."""
        self._closed = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._statuses.clear()
        self.activation = ""
        self._session = ""

    def _app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.middleware("http")
        async def operator_boundary(
            request: Request, next_handler: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            host = request.headers.get("host", "")
            if host != self.origin.split("://", 1)[1]:
                return JSONResponse({"error": "invalid_host"}, status_code=403)
            if request.url.path.startswith("/api/"):
                if self._closed:
                    return JSONResponse({"error": "runtime_closing"}, status_code=503)
                if request.method != "GET":
                    if request.headers.get("origin") != self.origin:
                        return JSONResponse({"error": "invalid_origin"}, status_code=403)
                    size = request.headers.get("content-length", "")
                    if len(size) > 5 or not size.isdecimal() or int(size) > 32768:
                        return JSONResponse({"error": "control_body_bound"}, status_code=413)
                if request.url.path != "/api/bootstrap" and not secrets.compare_digest(
                    request.cookies.get(COOKIE, ""), self._session
                ):
                    return JSONResponse({"error": "operator_session_required"}, status_code=401)
            response = await next_handler(request)
            return response

        @app.exception_handler(RequestValidationError)
        async def invalid_input(_: Request, __: RequestValidationError) -> JSONResponse:
            return JSONResponse({"error": "invalid_control_input"}, status_code=400)

        @app.exception_handler(MemoryError)
        async def memory_failure(_: Request, error: MemoryError) -> JSONResponse:
            return JSONResponse({"error": error.info.code}, status_code=409)

        @app.exception_handler(GatewayError)
        async def inference_failure(_: Request, error: GatewayError) -> JSONResponse:
            return JSONResponse({"error": error.code}, status_code=409)

        @app.post("/api/bootstrap")
        async def bootstrap(value: Bootstrap) -> JSONResponse:
            if not self.activation or not secrets.compare_digest(value.token, self.activation):
                raise HTTPException(status_code=401, detail="activation_required")
            response = JSONResponse({"ready": True})
            response.set_cookie(
                COOKIE,
                self._session,
                httponly=True,
                samesite="strict",
                max_age=28800,
                path="/api/",
            )
            return response

        @app.get("/api/state")
        async def state() -> dict[str, object]:
            return {
                "binding": self.binding.model_dump(mode="json"),
                "context_id": str(self.core.context_id),
                "recall": (await self.controls.recall_settings()).model_dump(mode="json"),
                "capture": (await self.controls.capture_settings()).model_dump(mode="json"),
                "libraries": [
                    library.model_dump(mode="json") for library in await self.controls.libraries()
                ],
                "processing": (await self.controls.processing_status()).model_dump(mode="json"),
                "deep": "available",
                "voice": self.voice_ready,
                "voice_status": self.voice_status,
            }

        @app.post("/api/room-token")
        async def token() -> dict[str, str]:
            return {"token": self.room_token(), "endpoint": self.binding.endpoint}

        @app.post("/api/recall")
        async def recall(value: RecallSelection) -> RecallSelection:
            return await self.controls.configure_recall(value)

        @app.post("/api/capture")
        async def capture(value: CaptureControl) -> CaptureControl:
            return await self.controls.configure_capture(value)

        @app.post("/api/deep")
        async def investigate(value: InvestigationInput) -> dict[str, str]:
            if value.request_id in self._statuses or len(self._tasks) >= 3:
                raise HTTPException(status_code=409, detail="investigation_busy_or_duplicate")
            self._statuses[value.request_id] = "running"
            request = DeepRecallRequest(
                request_id=value.request_id,
                character_id=self.binding.character_id,
                context_id=self.core.context_id,
                query=value.text,
                reference_at=timestamp_from_datetime(self.core.reference_clock()),
                timezone=self.core.calendar.timezone,
                previous_scope=self.core.last_scope,
            )

            async def work() -> None:
                try:
                    result = await self.deep.investigate(request)
                    self._statuses[value.request_id] = result.outcome
                except asyncio.CancelledError:
                    self._statuses[value.request_id] = "cancelled"
                    raise
                finally:
                    self._tasks.pop(value.request_id, None)
                    while len(self._statuses) > 16:
                        identity = next(
                            (key for key in self._statuses if key not in self._tasks), None
                        )
                        if identity is None:
                            break
                        self._statuses.pop(identity)

            task = asyncio.create_task(work(), name="ember-browser-deep")
            self._tasks[value.request_id] = task

            def observed(completed: asyncio.Task[None]) -> None:
                if not completed.cancelled() and completed.exception() is not None:
                    self.worker_failures += 1
                    self._statuses[value.request_id] = "deep_worker_failed"

            task.add_done_callback(observed)
            return {"request_id": str(value.request_id), "status": "running"}

        @app.get("/api/deep/{identity}")
        async def inspect(identity: UUID) -> dict[str, object]:
            result = await self.deep.inspect(identity, self.core.context_id)
            return {
                "status": self._statuses.get(identity, "expired_or_unavailable"),
                "result": result.model_dump(mode="json") if result else None,
            }

        @app.post("/api/deep/{identity}/cancel")
        async def cancel(identity: UUID) -> dict[str, bool]:
            task = self._tasks.get(identity)
            if task is not None:
                # One cancellation reaches the owned runner through admission.submit.
                if not task.cancelling():
                    task.cancel()
                return {"cancelled": True}
            return {"cancelled": self.deep.cancel(identity)}

        @app.post("/api/deep/{identity}/answer")
        async def answer(identity: UUID) -> dict[str, str]:
            result = await self.deep.inspect(identity, self.core.context_id)
            if result is None:
                raise HTTPException(status_code=409, detail="investigation_expired_or_invalidated")
            return {"receipt": await self.answer(result)}

        index = self.assets / "index.html"
        if index.is_file():
            app.mount("/assets", StaticFiles(directory=self.assets / "assets"), name="assets")

            @app.get("/")
            async def frontend() -> FileResponse:
                return FileResponse(index)
        else:

            @app.get("/")
            async def unavailable() -> JSONResponse:
                return JSONResponse({"error": "frontend_setup_required"}, status_code=503)

        return app
