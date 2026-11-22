"""Authenticated application controls share the runtime owner, never the inference token."""

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import UUID4, Field, model_validator

from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.selection import RecallSelection

from ..character.core import CharacterCore
from ..character.prompts import PromptEdit, PromptKind
from ..inference.contracts import GatewayError
from ..memory.deep_contracts import DeepRecallRequest, DeepRecallResult
from ..memory.deep_recall import DeepRecallService
from ..realtime.contracts import RoomBinding
from .build_services import BuildService
from .capabilities import capability_catalog
from .config import RuntimeModel, private_lan_address
from .controls import CaptureControl, GraphInput, PromptControls, RuntimeControls
from .dashboard import Dashboard
from .mcp_contracts import MCPDiscovery
from .skills import Skills
from .skills_contracts import SkillBundle, SkillCatalog, SkillDocument, SkillSelection
from .speech_settings import SpeechOptions, SpeechSelection
from .task_artifacts import TaskArtifact
from .task_router import TaskNotifications, TaskReceiptView, TaskRouter
from .task_store import (
    Confirmation,
    ResearchProvider,
    TaskConflict,
    TaskHandle,
    TaskOperation,
    TaskSnapshot,
    ToolInvocation,
)
from .text_session import DirectTextSession, TextReply

COOKIE = "ember_session"


class Bootstrap(RuntimeModel):
    token: str = Field(min_length=32, max_length=128, repr=False)


class ThinkingSetting(RuntimeModel):
    """A display/generation choice for subsequent turns; never stored as a memory."""

    enabled: bool


class InvestigationInput(RuntimeModel):
    request_id: Annotated[UUID4, Field(strict=False)]
    text: str = Field(min_length=1, max_length=1600, repr=False)


class TextLeaseInput(RuntimeModel):
    """Bind a control request to the current authenticated browser transport lease."""

    session_id: Annotated[UUID4, Field(strict=False)]


class TextAcknowledgement(TextLeaseInput):
    """Identify the exact pending response whose delivery the browser confirms."""

    request_id: Annotated[UUID4, Field(strict=False)]


class TextTurnInput(TextAcknowledgement):
    """Bounded original input; an optional investigation ID grants no new source access."""

    text: str = Field(min_length=1, max_length=1600, repr=False)
    investigation_id: Annotated[UUID4, Field(strict=False)] | None = None


class ConfirmationDecision(RuntimeModel):
    """Decision binds to the displayed action hash."""

    action_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approve: bool


class TaskInput(RuntimeModel):
    """Bound an authenticated operator task submission."""

    idempotency_key: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8192, repr=False)
    operation: TaskOperation = "inspect"
    tool: ToolInvocation | None = None
    service_port: int | None = Field(default=None, ge=1024, le=65535)
    research_provider: ResearchProvider | None = None

    @model_validator(mode="after")
    def bound_operation(self) -> Self:
        if (self.operation == "tool") != (self.tool is not None):
            raise ValueError("Tool payload must belong to the tool operation")
        if self.service_port is not None and self.operation != "build":
            raise ValueError("Service port must belong to the build operation")
        if (self.research_provider is not None) != (self.operation == "research"):
            raise ValueError("Web Research requires an explicit hosted provider")
        return self


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
        capture_changed: Callable[[], None] | None = None,
        lan_origin: str | None = None,
        trusted_proxy: str = "",
        text_session: DirectTextSession | None = None,
        task_router: TaskRouter | None = None,
        speech_options: Callable[[], SpeechOptions | None] | None = None,
        configure_speech: Callable[[SpeechSelection], SpeechOptions] | None = None,
        prompt_controls: PromptControls | None = None,
        prompt_idle: Callable[[], None] | None = None,
        coding_api: APIRouter | None = None,
        dashboard: Dashboard | None = None,
        memory_graph: Callable[[GraphInput], Awaitable[str]] | None = None,
    ) -> None:
        self.origin = origin.rstrip("/")
        self.dashboard = dashboard
        self.memory_graph = memory_graph
        self.lan_origin = lan_origin
        self.trusted_proxy = trusted_proxy
        self._origins = {
            urlsplit(value).netloc: value for value in (self.origin, lan_origin) if value
        }
        self.assets = assets
        self.binding = binding
        self.core = core
        self.deep = deep
        self.controls = controls
        self.room_token = room_token
        self.answer = answer
        self.voice_ready = voice_ready
        self.voice_status = voice_status
        self.capture_changed = capture_changed
        self.text_session = text_session
        self.task_router = task_router
        self.speech_options = speech_options
        self.configure_speech = configure_speech
        self.prompt_controls = prompt_controls
        self.prompt_idle = prompt_idle
        self.coding_api = coding_api
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
        if self.text_session is not None:
            await self.text_session.aclose()
        tasks = tuple(self._tasks.values())
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._statuses.clear()
        self.activation = ""
        self._session = ""
        self.core.turn_view.clear()

    def _app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.middleware("http")
        async def operator_boundary(
            request: Request, next_handler: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            host = request.headers.get("host", "")
            request_origin = self._origins.get(host)
            if request_origin is None:
                return JSONResponse({"error": "invalid_host"}, status_code=403)
            if self.trusted_proxy and request_origin == self.lan_origin:
                if (
                    request.client is None
                    or request.client.host != self.trusted_proxy
                    or request.headers.get("x-forwarded-proto") != "https"
                ):
                    return JSONResponse({"error": "trusted_proxy_required"}, status_code=403)
            if self.lan_origin and request.client is not None:
                try:
                    peer = ip_address(request.client.host)
                    allowed = peer.is_loopback or bool(private_lan_address(str(peer)))
                except ValueError:
                    allowed = False
                if not allowed:
                    return JSONResponse({"error": "lan_peer_required"}, status_code=403)
            if request.url.path.startswith("/api/"):
                if self._closed:
                    return JSONResponse({"error": "runtime_closing"}, status_code=503)
                if request.headers.get("origin", request_origin) != request_origin:
                    return JSONResponse({"error": "invalid_origin"}, status_code=403)
                if request.method != "GET":
                    if request.headers.get("origin") != request_origin:
                        return JSONResponse({"error": "invalid_origin"}, status_code=403)
                    size = request.headers.get("content-length", "")
                    limit = 262144 if request.url.path == "/api/skills/import" else 32768
                    if len(size) > 6 or not size.isdecimal() or int(size) > limit:
                        return JSONResponse({"error": "control_body_bound"}, status_code=413)
                if request.url.path != "/api/bootstrap" and not secrets.compare_digest(
                    request.cookies.get(COOKIE, ""), self._session
                ):
                    return JSONResponse({"error": "operator_session_required"}, status_code=401)
            if request.url.path.startswith("/coding/"):
                if not self.trusted_proxy or request_origin != self.lan_origin:
                    return JSONResponse({"error": "coding_https_required"}, status_code=403)
                if self._closed:
                    return JSONResponse({"error": "runtime_closing"}, status_code=503)
            response = await next_handler(request)
            if (
                self.trusted_proxy
                and request_origin == self.lan_origin
                and request.method == "GET"
                and response.status_code == 200
                and request.url.path in {"/", "/memory-graph"}
                and not self._closed
            ):
                response.set_cookie(
                    COOKIE,
                    self._session,
                    httponly=True,
                    secure=True,
                    samesite="strict",
                    max_age=28800,
                    path="/api/",
                )
                response.headers["Cache-Control"] = "no-store"
            if request.url.path.startswith(
                (
                    "/api/tasks",
                    "/api/task-notifications",
                    "/api/task-confirmations",
                    "/api/tools",
                    "/api/memory-graph",
                    "/api/skills",
                )
            ):
                response.headers["Cache-Control"] = "no-store"
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

        @app.exception_handler(TaskConflict)
        async def task_failure(_: Request, error: TaskConflict) -> JSONResponse:
            return JSONResponse({"error": str(error)}, status_code=409)

        if self.coding_api is not None:
            app.include_router(self.coding_api)

        if self.task_router is not None:
            router = self.task_router

            @app.post("/api/tasks", status_code=202)
            async def submit_task(value: TaskInput) -> TaskHandle:
                return await router.submit(
                    value.idempotency_key,
                    value.text,
                    operation=value.operation,
                    tool=value.tool,
                    service_port=value.service_port,
                    research_provider=value.research_provider,
                )

            @app.get("/api/tools")
            async def tool_discovery() -> MCPDiscovery:
                return router.mcp.snapshot() if router.mcp else MCPDiscovery(servers=(), tools=())

            @app.post("/api/tools/refresh")
            async def refresh_tools() -> MCPDiscovery:
                if router.mcp:
                    await router.mcp.refresh()
                return await tool_discovery()

            @app.get("/api/tasks/{task_id}/receipt")
            async def tool_receipt(task_id: UUID) -> TaskReceiptView:
                return await router.receipt(task_id)

            @app.get("/api/task-confirmations")
            async def task_confirmations() -> tuple[Confirmation, ...]:
                return await router.confirmations()

            @app.post("/api/task-confirmations/{identity}")
            async def decide_confirmation(
                identity: UUID, value: ConfirmationDecision
            ) -> Confirmation:
                return await router.decide(identity, value.action_sha256, approve=value.approve)

            @app.get("/api/task-notifications")
            async def task_notifications(revision: int | None = None) -> TaskNotifications:
                return await router.notifications(revision)

            @app.post("/api/tasks/{task_id}/acknowledge")
            async def acknowledge_task(task_id: UUID) -> dict[str, bool]:
                await router.acknowledge(task_id)
                return {"accepted": True}

            @app.get("/api/tasks/build-service")
            async def active_build_service() -> BuildService | None:
                return await router.active_build_service()

            @app.get("/api/tasks/{task_id}")
            async def task_status(task_id: UUID) -> TaskSnapshot:
                value = await router.status(task_id)
                if value is None:
                    raise HTTPException(status_code=404, detail="task_not_found")
                return value

            @app.post("/api/tasks/{task_id}/artifact")
            async def save_task_artifact(task_id: UUID) -> TaskArtifact:
                return await router.save_artifact(task_id)

            @app.get("/api/tasks/{task_id}/artifact")
            async def read_task_artifact(task_id: UUID) -> Response:
                return Response(
                    await router.read_artifact(task_id),
                    media_type="text/plain",
                    headers={
                        "Content-Disposition": "attachment; filename=ember-task.txt",
                        "Cache-Control": "no-store",
                    },
                )

            @app.get("/api/tasks/{task_id}/build-artifact")
            async def read_build_artifact(task_id: UUID) -> Response:
                return Response(
                    await router.read_build_artifact(task_id),
                    media_type="application/zip",
                    headers={
                        "Content-Disposition": "attachment; filename=ember-build.zip",
                        "X-Content-Type-Options": "nosniff",
                    },
                )

            @app.post("/api/tasks/{task_id}/stop-service")
            async def stop_build_service(task_id: UUID) -> dict[str, bool]:
                await router.stop_build_service(task_id)
                return {"accepted": True}

            @app.get("/api/tasks/{task_id}/recall")
            async def task_recall(task_id: UUID) -> dict[str, object]:
                snapshot = await router.status(task_id)
                if (
                    snapshot is None
                    or snapshot.operation != "recall"
                    or snapshot.state != "succeeded"
                ):
                    raise HTTPException(status_code=404, detail="task_recall_not_found")
                result = await self.deep.inspect(task_id, task_id)
                return {
                    "status": result.outcome if result else "expired_or_unavailable",
                    "result": result.model_dump(mode="json") if result else None,
                }

            @app.post("/api/tasks/{task_id}/cancel")
            async def cancel_task(task_id: UUID) -> dict[str, bool]:
                await router.cancel(task_id)
                return {"accepted": True}

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

        def browser_binding(request: Request) -> RoomBinding:
            # Host passed the exact allowlist above; never trust forwarded headers.
            if self._origins[request.headers["host"]] != self.lan_origin:
                return self.binding
            address = urlsplit(self.lan_origin or "")
            endpoint = urlsplit(self.binding.endpoint)
            return self.binding.model_copy(
                update={
                    "endpoint": (
                        "wss://" + address.netloc
                        if self.trusted_proxy
                        else f"ws://{address.hostname}:{endpoint.port}"
                    )
                }
            )

        @app.get("/api/state")
        async def state(request: Request) -> dict[str, object]:
            speech = self.speech_options() if self.speech_options is not None else None
            return {
                "binding": browser_binding(request).model_dump(mode="json"),
                "lan_available": self.lan_origin is not None,
                "tasks_available": self.task_router is not None,
                "summary_tasks_available": bool(self.task_router and self.task_router.summary),
                "recall_tasks_available": bool(self.task_router and self.task_router.recall),
                "delegation_available": bool(
                    self.task_router and self.task_router.delegation_available
                ),
                "build_available": bool(self.task_router and self.task_router.build_available),
                "delegation_provider": self.task_router.delegate_label
                if self.task_router
                else None,
                "public_search_available": self.core.web_search_ready,
                "context_id": str(self.core.context_id),
                "prompts_available": self.prompt_controls is not None,
                "recall": (await self.controls.recall_settings()).model_dump(mode="json"),
                "capture": (await self.controls.capture_settings()).model_dump(mode="json"),
                "libraries": [
                    library.model_dump(mode="json") for library in await self.controls.libraries()
                ],
                "processing": (await self.controls.processing_status()).model_dump(mode="json"),
                "deep": "available",
                "voice": self.voice_ready,
                "voice_status": self.voice_status,
                "speech": speech.model_dump(mode="json") if speech is not None else None,
                "transport": "http" if self.text_session is not None else "livekit",
                "text_available": self.text_session.model_available if self.text_session else True,
                "thinking_available": self.core.thinking_available,
                "thinking_enabled": self.core.thinking_enabled,
            }

        @app.post("/api/speech")
        async def speech_settings(value: SpeechSelection) -> SpeechOptions:
            if self.configure_speech is None:
                raise GatewayError("speech_controls_unavailable")
            return self.configure_speech(value)

        def prompt_owner() -> PromptControls:
            if self.prompt_controls is None:
                raise GatewayError("prompt_controls_unavailable")
            return self.prompt_controls

        @app.get("/api/prompts")
        async def prompts() -> JSONResponse:
            value = await prompt_owner().read_prompts()
            return JSONResponse(
                value.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
            )

        @app.post("/api/prompts/preview")
        async def prompt_preview(value: PromptEdit) -> JSONResponse:
            result = await prompt_owner().edit_prompt(value, save=False)
            return JSONResponse(
                result.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
            )

        @app.put("/api/prompts/{kind}")
        async def prompt_save(kind: PromptKind, value: PromptEdit) -> JSONResponse:
            if kind != value.kind:
                raise GatewayError("prompt_scope_mismatch")
            controls = prompt_owner()
            if (
                self.core.prompt_editing
                or self.core.busy
                or self.core.pending_delivery is not None
                or (self.text_session is not None and self.text_session.active is not None)
            ):
                raise GatewayError("prompt_settings_busy")
            if self.prompt_idle is not None:
                self.prompt_idle()
            self.core.prompt_editing = True
            try:
                result = await controls.edit_prompt(value, save=True)
            finally:
                self.core.prompt_editing = False
            return JSONResponse(
                result.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
            )

        @app.post("/api/thinking")
        async def thinking(value: ThinkingSetting) -> dict[str, bool]:
            if value.enabled and not self.core.thinking_available:
                raise GatewayError("thinking_unavailable")
            self.core.thinking_enabled = value.enabled
            return {"enabled": value.enabled}

        def skill_owner() -> Skills:
            if self.task_router is None or self.task_router.skills is None:
                raise HTTPException(status_code=503, detail="skills_provider_unavailable")
            return self.task_router.skills

        @app.get("/api/skills")
        async def skill_catalog() -> SkillCatalog:
            if self.task_router is None or self.task_router.skills is None:
                return SkillCatalog(
                    provider="none",
                    revision="unavailable",
                    unavailable="skills_provider_unconfigured",
                )
            return self.task_router.skills.catalog

        @app.post("/api/skills/refresh")
        async def refresh_skills() -> SkillCatalog:
            return await skill_owner().refresh()

        @app.get("/api/skills/document")
        async def skill_document(identity: str) -> SkillDocument:
            return await skill_owner().inspect(identity)

        @app.post("/api/skills/select")
        async def select_skill(value: SkillSelection) -> SkillCatalog:
            return await skill_owner().select(value)

        @app.post("/api/skills/import")
        async def import_skill(value: SkillBundle) -> SkillCatalog:
            return await skill_owner().install(value)

        @app.get("/api/skills/export")
        async def export_skill(identity: str) -> JSONResponse:
            value = await skill_owner().export(identity)
            return JSONResponse(
                value.model_dump(mode="json"),
                headers={
                    "Content-Disposition": 'attachment; filename="ember-skill.json"',
                    "Cache-Control": "no-store",
                },
            )

        @app.get("/api/capabilities")
        async def capabilities() -> JSONResponse:
            return JSONResponse(
                {
                    "capabilities": [
                        value.model_dump(mode="json")
                        for value in capability_catalog(self.core, self.task_router)
                    ]
                },
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/api/dashboard")
        async def dashboard(after: int = 0) -> JSONResponse:
            if self.dashboard is None:
                raise GatewayError("dashboard_unavailable")
            if after < 0:
                raise GatewayError("invalid_dashboard_cursor")
            return JSONResponse(
                await self.dashboard.snapshot(after), headers={"Cache-Control": "no-store"}
            )

        @app.get("/api/inference/requests/{identity}")
        async def inspect_request(identity: UUID, validate_only: bool = False) -> JSONResponse:
            if self.dashboard is None:
                raise GatewayError("dashboard_unavailable")
            return JSONResponse(
                await self.dashboard.inspect(identity, validate_only=validate_only),
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/api/turn-details")
        async def turn_details(metadata_only: bool = False) -> JSONResponse:
            entry = self.core.turn_view.entry
            if entry is None:
                response = JSONResponse({"turn": None})
            elif metadata_only:
                response = JSONResponse(
                    {
                        "turn": entry.value.model_dump(
                            mode="json", exclude={"thinking", "memories"}
                        )
                        | {
                            "thinking": "",
                            "memories": [],
                            "retained_memory_count": len(entry.value.memories),
                        }
                    }
                )
            else:
                if not await self.core.current(entry.dependencies, entry.certificate):
                    self.core.turn_view.invalidate(entry.value.request_id)
                current = self.core.turn_view.entry
                response = JSONResponse(
                    {"turn": entry.value.model_dump(mode="json") if current is entry else None}
                )
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.post("/api/room-token")
        async def token(request: Request) -> dict[str, str]:
            return {"token": self.room_token(), "endpoint": browser_binding(request).endpoint}

        @app.post("/api/lan-link")
        async def lan_link() -> dict[str, str]:
            if self.lan_origin is None:
                raise HTTPException(status_code=404, detail="lan_not_configured")
            return {"url": self.lan_origin + "/#activate=" + self.activation}

        @app.post("/api/memory-graph")
        async def graph(request: GraphInput) -> Response:
            if self.memory_graph is None:
                raise HTTPException(status_code=503, detail="memory_graph_unavailable")
            return Response(await self.memory_graph(request), media_type="application/json")

        @app.post("/api/recall")
        async def recall(value: RecallSelection) -> RecallSelection:
            return await self.controls.configure_recall(value)

        @app.post("/api/capture")
        async def capture(value: CaptureControl) -> CaptureControl:
            configured = await self.controls.configure_capture(value)
            if self.capture_changed is not None:
                self.capture_changed()
            return configured

        def direct_text() -> DirectTextSession:
            if self.text_session is None:
                raise HTTPException(status_code=409, detail="use_room_transport")
            return self.text_session

        @app.post("/api/text/connect")
        async def text_connect() -> dict[str, str]:
            return {"session_id": str(await direct_text().connect())}

        @app.post("/api/text/heartbeat")
        async def text_heartbeat(value: TextLeaseInput) -> dict[str, bool]:
            direct_text().heartbeat(value.session_id)
            return {"connected": True}

        @app.post("/api/text/disconnect")
        async def text_disconnect(value: TextLeaseInput) -> dict[str, bool]:
            await direct_text().disconnect(value.session_id)
            return {"connected": False}

        @app.post("/api/text/turn")
        async def text_turn(value: TextTurnInput) -> TextReply:
            return await direct_text().turn(
                value.session_id, value.request_id, value.text, value.investigation_id
            )

        @app.post("/api/text/acknowledge")
        async def text_acknowledge(value: TextAcknowledgement) -> dict[str, bool]:
            await direct_text().acknowledge(value.session_id, value.request_id)
            return {"delivered": True}

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
            @app.get("/memory-graph")
            async def frontend() -> FileResponse:
                return FileResponse(index)
        else:

            @app.get("/")
            @app.get("/memory-graph")
            async def unavailable() -> JSONResponse:
                return JSONResponse({"error": "frontend_setup_required"}, status_code=503)

        return app
