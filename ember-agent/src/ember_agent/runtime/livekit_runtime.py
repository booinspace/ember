"""Phase 3 composition: managed services and one explicitly dispatched text room."""

import asyncio
import os
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from livekit import api, rtc
from pydantic import ValidationError

from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.related import ContextDependencies, RelatedRequest

from ..inference.client import HttpGatewayClient
from ..inference.contracts import GatewayError, GatewayRequest
from ..memory.deep_contracts import DeepRecallBudget, DeepRecallRequest, DeepRecallResult
from ..memory.deep_prompt import condense
from ..realtime.client import RoomClient
from ..realtime.contracts import (
    AGENT_IDENTITY,
    USER_IDENTITY,
    RememberInput,
    RoomBinding,
)
from ..realtime.session import HostedRoom
from .browser_control import BrowserControl
from .browser_server import BrowserServer
from .capture_lifecycle import CaptureLifecycle
from .composition import LocalRuntime
from .config import ConfigurationError, DeploymentConfig
from .console import read_line
from .hermes_chat import HermesChats
from .livekit_config import LiveKitCredentials, credentials, server_yaml
from .processes import OwnedProcess, RuntimeLease
from .speech_health import check_voice
from .strata_runtime import await_strata_ready, prepare_strata_config, strata_gateway_environment
from .task_router import (
    RecallTask,
    SummaryTask,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolContext,
    ToolRouter,
)
from .task_store import TaskConflict
from .text_session import DirectTextSession


async def _start_hermes_client(runtime: LocalRuntime) -> HermesChats | None:
    """Bind one optional local client to the existing character, gateway and ABI owner."""
    settings = runtime.config.hermes_client
    if settings is None:
        return None

    def client_token() -> str:
        path = runtime.config.runtime_root / "hermes-client.token"
        if not path.exists():
            with path.open("x", encoding="utf-8") as output:
                output.write(secrets.token_urlsafe(32))
            path.chmod(0o600)
        return read_token(path)

    async def reserve(identity: UUID, connected: bool) -> None:
        await runtime.model.session(identity, connected=connected)

    return HermesChats(
        lambda: runtime.new_text_core(
            input_ceiling=settings.input_ceiling, recall_enabled=settings.private_recall
        ),
        runtime.model,
        client_token=await asyncio.to_thread(client_token),
        model_context_tokens=runtime.config.context_tokens,
        private_recall=settings.private_recall,
        state_root=runtime.config.runtime_root / "hermes-client",
        reserve=reserve,
    )


async def _start_public_search(runtime: LocalRuntime) -> None:
    """Search is a foreground tool capability, independent of a native Desktop client."""
    settings = runtime.config.public_search
    legacy = runtime.config.hermes_client
    command = settings.command if settings else legacy.search_command if legacy else ()
    if command:
        from .native_search import NativeSearch

        if not await asyncio.to_thread(Path(command[0]).is_file):
            runtime.core.search_unavailable = "public_search_executable_unavailable"
            return
        runtime.core.public_search = NativeSearch(
            command, runtime.config.runtime_root / "public-search-homes"
        )
        runtime.core.search_provider = settings.provider if settings else "hermes"


async def _start_tasks(
    runtime: LocalRuntime, binding: RoomBinding, cleanup: AsyncExitStack
) -> TaskRouter | None:
    mcp = None
    if runtime.config.mcp_servers:
        from .mcp_tools import MCPTools

        mcp = MCPTools(runtime.config.mcp_servers, binding.character_id, binding.instance_id)
        cleanup.push_async_callback(mcp.aclose)
        await mcp.start()
    token_path = runtime.config.runtime_root / "gateway-agent.token"
    try:
        token = (await asyncio.to_thread(token_path.read_text, encoding="utf-8")).strip()
    except OSError:
        token = ""
    summary = None
    client: HttpGatewayClient | None = None

    async def validate_task_dependencies(dependencies: ContextDependencies) -> bool:
        checked = await runtime.memory.lookup_related(
            RelatedRequest(
                intent="validate", dependencies=dependencies, row_budget=512, call_budget=8
            )
        )
        return checked.valid is True

    if 32 <= len(token) <= 128:
        client = HttpGatewayClient(
            runtime.config.services.gateway.endpoint, token, observe_event=runtime.dashboard.event
        )
        cleanup.push_async_callback(client.aclose)
        summary = SummaryTask(client, runtime.config.model_tag, observe=runtime.dashboard.capture)

    router = TaskRouter(
        runtime.config.runtime_root,
        binding.character_id,
        binding.instance_id,
        TextInspectionTask(
            ToolRouter(
                TextInspectionProvider(), binding.character_id, activity=runtime.dashboard.activity
            )
        ),
        summary=summary,
        mcp=mcp,
        validate=validate_task_dependencies,
        recall=RecallTask(
            runtime.deep, runtime.core.reference_clock, runtime.core.calendar.timezone
        ),
        activity=runtime.dashboard.activity,
    )
    settings = runtime.config.selected_harness
    if settings is not None:
        from .harness import create_skill_provider, create_task_provider
        from .hermes_tasks import PersonalContext
        from .searx_tools import SearXNGTools

        router.delegate_label = settings.provider.title()
        try:
            router.skills = create_skill_provider(runtime.config)
            if router.skills is not None:
                router.skills.activity = runtime.dashboard.activity
                await router.skills.refresh()
                if "web.read_page" in settings.tools:
                    from .public_page import PublicPageProvider

                    pages = PublicPageProvider(router.skills)
                    router.broker.web = ToolRouter(
                        pages, binding.character_id, activity=runtime.dashboard.activity
                    )

                    async def read_public_page(url: str) -> str:
                        try:
                            return await pages.read(url)
                        except TaskConflict as error:
                            code = str(error)
                            if not re.fullmatch(r"(?:page|skills)_[a-z0-9_]{1,48}", code):
                                code = "public_page_unavailable"
                            raise GatewayError(code) from error
                        except TimeoutError as error:
                            raise GatewayError("page_fetch_timeout") from error

                    runtime.core.public_page = read_public_page
        except (TaskConflict, TimeoutError):
            router.skills = None
        search = SearXNGTools(settings.search_endpoint)
        cleanup.push_async_callback(search.aclose)

        async def recall_context(query: str, context: ToolContext) -> PersonalContext | None:
            result = await runtime.deep.investigate(
                DeepRecallRequest(
                    request_id=context.task_id,
                    context_id=context.task_id,
                    character_id=context.character_id,
                    query=query,
                    reference_at=timestamp_from_datetime(runtime.core.reference_clock()),
                    timezone=runtime.config.recall_timezone,
                    budget=DeepRecallBudget(
                        hops=0,
                        memories=6,
                        archive_chunks=0,
                        excerpts=0,
                        result_bytes=16384,
                    ),
                )
            )
            selected = condense(result)
            if not selected.memories or result.dependencies is None:
                return None
            text = "\n".join(message.content for message in selected.messages)
            return PersonalContext(text, result.dependencies, selected.memory_sources)

        def observe(step: GatewayRequest, validate: Callable[[], Awaitable[bool]]) -> None:
            runtime.dashboard.capture(step, validate=validate)

        if client is None:
            router.delegate_unavailable = "gateway_task_credentials_unavailable"
        else:
            try:
                router.delegate = create_task_provider(
                    runtime.config,
                    client,
                    ToolRouter(search, binding.character_id, activity=runtime.dashboard.activity),
                    broker=router.broker,
                    recall=recall_context,
                    validate=validate_task_dependencies,
                    observe=observe,
                    skills=router.skills,
                )
                router.providers["delegate"] = router.delegate
            except TaskConflict as error:
                router.delegate_unavailable = str(error)
    try:
        await router.start()
    except (OSError, sqlite3.Error):
        print("Tasks unavailable: operational storage could not start.")
        return None
    runtime.core.submit_task = router.submit_turn

    async def summarize(text: str, turn_id: UUID) -> str:
        return await router.submit_turn(text, turn_id, operation="summarize")

    async def investigate(text: str, turn_id: UUID) -> str:
        return await router.submit_turn(text, turn_id, operation="recall")

    runtime.core.task_tools = {
        "inspect_user_text": (
            "When the user requests a word count, queue text quoted exactly by the user.",
            router.submit_turn,
        ),
        "investigate_memory": (
            "When the user requests a deep memory investigation, queue their exact query.",
            investigate,
        ),
    }
    if summary is not None:
        runtime.core.task_tools["summarize_user_text"] = (
            "When the user requests a summary, queue text quoted exactly by the user.",
            summarize,
        )
    if router.delegate is not None:

        async def delegate_task(text: str, turn_id: UUID) -> str:
            return await router.submit_turn(text, turn_id, operation="delegate")

        runtime.core.task_tools["delegate_task"] = (
            "Queue an explicit request with the configured harness and granted broker tools. "
            "Quote the user's goal exactly. Model steps wait while voice is connected.",
            delegate_task,
        )
        if router.skills is not None:

            def update_skills() -> None:
                assert router.skills is not None
                names = "; ".join(
                    item.id + " (" + item.name + "): " + item.description[:160]
                    for item in router.skills.enabled
                )
                runtime.core.task_tools["delegate_task"] = (
                    "Delegate the user's exact explicit goal. Enabled skills: "
                    + (names or "none")
                    + ". Name the selected skill in the goal. Skills grant no tools. "
                    "Background model steps wait until voice disconnects.",
                    delegate_task,
                )

            router.skills.changed = update_skills
            update_skills()

    runtime.core.task_notice = router.turn_notice
    cleanup.push_async_callback(router.aclose)
    return router


def port_open(endpoint: str) -> bool:
    """Check occupation only; this is never evidence of ownership or protocol health."""
    parsed = urlsplit(endpoint)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 0), 0.2):
            return True
    except OSError:
        return False


def publish_room(path: Path, binding: RoomBinding) -> None:
    """Publish complete discovery atomically; readers never see partial JSON."""
    temporary = path.with_suffix(".next")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(binding.model_dump_json())
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_token(path: Path) -> str:
    """Read private gateway credentials outside the realtime loop."""
    return path.read_text(encoding="utf-8").strip()


class RuntimeServices:
    """Start missing managed services, verify attached identities, and stop owned handles only."""

    def __init__(self, config: DeploymentConfig, config_path: Path) -> None:
        self.config = config
        self.config_path = config_path.resolve()
        self.instance = uuid4()
        self.children: list[OwnedProcess] = []
        self.credentials: LiveKitCredentials | None = None
        self.gateway_owned = False
        self.thinking_available = False
        self._strata_directory: tempfile.TemporaryDirectory[str] | None = None

    async def start(self) -> LiveKitCredentials:
        """Authenticate LiveKit and the pinned gateway before exposing a room session."""
        settings = self.config.services.livekit
        if settings.mode == "disabled":
            raise ConfigurationError("LiveKit is disabled; use ember chat for direct text.")
        assert settings.credentials_file is not None
        self.credentials = await asyncio.to_thread(
            credentials, settings.credentials_file, create=settings.mode == "managed"
        )
        endpoint = urlsplit(settings.endpoint)
        if settings.mode == "managed" and not await asyncio.to_thread(port_open, settings.endpoint):
            executable = await asyncio.to_thread(shutil.which, settings.executable)
            if executable is None:
                raise ConfigurationError(
                    "LiveKit executable is missing. Run scripts/setup_livekit.ps1 and "
                    "configure services.livekit.executable."
                )
            config_file = self.config.runtime_root / "livekit-server.yaml"
            value = server_yaml(
                self.credentials,
                port=endpoint.port or 7880,
                tcp_port=settings.tcp_port,
                udp_port=settings.udp_port,
                lan=self.config.browser.lan_address is not None,
            )
            await asyncio.to_thread(config_file.write_text, value, encoding="utf-8")
            await asyncio.to_thread(config_file.chmod, 0o600)
            self.children.append(
                await asyncio.to_thread(
                    OwnedProcess,
                    [executable, "--config", str(config_file)],
                    root=self.config.runtime_root,
                    component="livekit",
                    instance=self.instance,
                )
            )
        url = settings.endpoint.replace("ws://", "http://").replace("wss://", "https://")
        service = api.LiveKitAPI(
            url,
            self.credentials.api_key,
            self.credentials.api_secret,
            failover=False,
        )
        try:
            async with asyncio.timeout(settings.startup_timeout_seconds):
                while True:
                    for child in self.children:
                        child.check()
                    try:
                        await asyncio.wait_for(
                            service.room.list_rooms(api.ListRoomsRequest()), timeout=2
                        )
                        break
                    except (api.TwirpError, OSError, TimeoutError) as error:
                        if settings.mode == "attach":
                            raise ConfigurationError(
                                "Attached LiveKit authentication failed."
                            ) from error
                        await asyncio.sleep(0.1)
        except TimeoutError as error:
            raise ConfigurationError(
                "LiveKit did not become authenticated-ready in time."
            ) from error
        finally:
            await service.aclose()

        await self.start_gateway()
        return self.credentials

    async def _spawn_strata_child(
        self, command: list[str], component: str, environment: dict[str, str]
    ) -> OwnedProcess:
        spawn = asyncio.create_task(
            asyncio.to_thread(
                OwnedProcess,
                command,
                root=self.config.runtime_root,
                component=component,
                instance=self.instance,
                environment=environment,
            )
        )
        try:
            child = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            try:
                child = await asyncio.shield(spawn)
            except (OSError, ConfigurationError):
                pass
            else:
                await asyncio.to_thread(child.stop)
            raise
        self.children.append(child)
        return child

    def _gateway_command(self) -> list[str]:
        return [sys.executable, "-m", "ember_agent", "gateway", "--config", str(self.config_path)]

    async def _start_strata_gateway(self) -> None:
        settings = self.config.inference.strata
        gateway = self.config.services.gateway
        if settings is None or gateway.mode != "managed":
            raise ConfigurationError("Strata requires an Ember-managed gateway.")
        if self.gateway_owned:
            for child in self.children:
                if child.component in {"strata", "gateway"}:
                    child.check()
            await self._await_gateway()
            return
        for endpoint in (settings.endpoint, gateway.endpoint):
            if await asyncio.to_thread(port_open, endpoint):
                raise ConfigurationError("Managed Strata or gateway endpoint is already occupied.")
        first = len(self.children)
        self._strata_directory = await asyncio.to_thread(
            tempfile.TemporaryDirectory, prefix="strata-", dir=self.config.runtime_root
        )
        try:
            private = Path(self._strata_directory.name)
            snapshot = private / "runner.json"
            if settings.verify_model_checksums:
                print(
                    "Verifying Strata model checksums. This can take several minutes.", flush=True
                )
            await asyncio.to_thread(prepare_strata_config, self.config, settings, snapshot)
            if await asyncio.to_thread(port_open, settings.endpoint):
                raise ConfigurationError("Managed Strata endpoint is already occupied.")
            origin = urlsplit(settings.endpoint)
            ready_file = private / "ready.json"
            token = secrets.token_urlsafe(32)
            print("Loading Strata model.", flush=True)
            child = await self._spawn_strata_child(
                [
                    settings.python_executable,
                    "-I",
                    settings.server_script,
                    "--engine",
                    "strata",
                    "--config",
                    str(snapshot),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(origin.port),
                    "--offline",
                    "--ready-file",
                    str(ready_file),
                ],
                "strata",
                {
                    "STRATA_API_KEY": token,
                    "STRATA_NO_PRESET": "1",
                    "STRATA_OFFLINE": "1",
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                },
            )
            identity = await await_strata_ready(self.config, settings, child, ready_file, token)
            if await asyncio.to_thread(port_open, gateway.endpoint):
                raise ConfigurationError("Managed Strata gateway endpoint is already occupied.")
            await self._spawn_strata_child(
                self._gateway_command(),
                "gateway",
                strata_gateway_environment(self.config, settings, token, identity),
            )
            self.gateway_owned = True
            await self._await_gateway()
        except BaseException:
            self.gateway_owned = False
            try:
                for child in reversed(self.children[first:]):
                    await asyncio.to_thread(child.stop)
            finally:
                del self.children[first:]
                await self._remove_strata_directory()
            raise

    async def start_gateway(self) -> None:
        """Verify/start the sole gateway independently of voice transport availability."""
        if self.config.inference.backend == "strata":
            await self._start_strata_gateway()
            return
        gateway = self.config.services.gateway
        if gateway.mode == "disabled":
            raise ConfigurationError(
                "The gateway is disabled; model-backed room chat is unavailable."
            )
        if not self.gateway_owned and not await asyncio.to_thread(port_open, gateway.endpoint):
            if gateway.mode != "managed":
                raise ConfigurationError("Attached gateway is unavailable.")
            self.children.append(
                await asyncio.to_thread(
                    OwnedProcess,
                    self._gateway_command(),
                    root=self.config.runtime_root,
                    component="gateway",
                    instance=self.instance,
                )
            )
            self.gateway_owned = True
        await self._await_gateway()

    async def _await_gateway(self) -> None:
        gateway = self.config.services.gateway
        try:
            async with asyncio.timeout(self.config.services.livekit.startup_timeout_seconds):
                while True:
                    for child in self.children:
                        if child.component in {"gateway", "strata"}:
                            child.check()
                    try:
                        token = await asyncio.to_thread(
                            read_token, self.config.runtime_root / "gateway.token"
                        )
                        client = HttpGatewayClient(gateway.endpoint, token)
                        try:
                            health = await client.health()
                        finally:
                            await client.aclose()
                        if health.model != self.config.model_tag or health.digest.removeprefix(
                            "sha256:"
                        ) != self.config.model_digest.removeprefix("sha256:"):
                            raise ConfigurationError("Existing gateway model pin does not match.")
                        if health.context_tokens != self.config.context_tokens:
                            raise ConfigurationError("Existing gateway context does not match.")
                        self.thinking_available = "live_thinking_v1" in health.features
                        if (
                            health.status == "quiescence_required"
                            and self.config.inference.backend == "strata"
                        ):
                            raise ConfigurationError(
                                "Gateway journal requires operator quiescence."
                            )
                        if (
                            health.status not in {"ready", "busy"}
                            or health.journal_status != "available"
                        ):
                            raise ConfigurationError(
                                "Gateway is not ready; inspect its health/state."
                            )
                        break
                    except (OSError, GatewayError) as error:
                        if not self.gateway_owned:
                            raise ConfigurationError(
                                "Attached gateway authentication failed."
                            ) from error
                        await asyncio.sleep(0.1)
        except TimeoutError as error:
            raise ConfigurationError(
                "Gateway did not become authenticated-ready in time."
            ) from error

    async def start_phonon(self) -> bool:
        """Start configured CPU Phonon offline and verify its identity before use."""
        settings = self.config.audio
        if not settings.phonon_endpoint or settings.phonon_executable is None:
            return True
        from ..audio.phonon import PhononSTT

        child: OwnedProcess | None = None
        try:
            occupied = await asyncio.to_thread(port_open, settings.phonon_endpoint)
            if not occupied:
                executable = settings.phonon_executable
                cache = settings.phonon_cache_directory
                if cache is None or not await asyncio.to_thread(executable.is_file):
                    return False
                if not await asyncio.to_thread(cache.is_dir):
                    return False
                spawn = asyncio.create_task(
                    asyncio.to_thread(
                        OwnedProcess,
                        [
                            str(executable),
                            "serve",
                            "phonon-2",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            "8010",
                            "--threads",
                            str(settings.stt_threads),
                            "--device",
                            "cpu",
                        ],
                        root=self.config.runtime_root,
                        component="phonon-stt",
                        instance=self.instance,
                        environment={
                            "CUDA_VISIBLE_DEVICES": "-1",
                            "HF_HUB_OFFLINE": "1",
                            "TRANSFORMERS_OFFLINE": "1",
                            "FERMION_CACHE_DIR": str(cache),
                            "OMP_NUM_THREADS": str(settings.stt_threads),
                            "MKL_NUM_THREADS": str(settings.stt_threads),
                        },
                    )
                )
                try:
                    child = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    try:
                        spawned = await asyncio.shield(spawn)
                    except Exception:
                        pass
                    else:
                        await asyncio.to_thread(spawned.stop)
                    raise
                assert child is not None
                self.children.append(child)
            async with asyncio.timeout(60):
                while True:
                    if child is not None:
                        child.check()
                    try:
                        probe = await asyncio.to_thread(
                            PhononSTT.load, settings.phonon_endpoint, language=settings.language
                        )
                        await probe.aclose()
                        return True
                    except (GatewayError, OSError, ValueError):
                        if child is None:
                            raise
                        await asyncio.sleep(0.2)
        except (ConfigurationError, GatewayError, OSError, ValueError, TimeoutError):
            if child is not None:
                await asyncio.to_thread(child.stop)
                self.children.remove(child)
            return False
        except BaseException:
            if child is not None:
                await asyncio.to_thread(child.stop)
                self.children.remove(child)
            raise

    async def start_speech(self) -> str:
        """Start/attach installed TTS independently; provider failures leave text available."""
        settings = self.config.services.tts
        if not self.config.audio.enabled or settings.mode == "disabled":
            return "disabled"
        child = None
        try:
            occupied = await asyncio.to_thread(port_open, settings.endpoint)
            if settings.mode == "managed" and not occupied:
                executable = await asyncio.to_thread(shutil.which, settings.executable)
                if executable is None:
                    return "tts_executable_not_installed"
                if settings.working_directory is not None and not await asyncio.to_thread(
                    settings.working_directory.is_dir
                ):
                    return "tts_working_directory_unavailable"
                child = await asyncio.to_thread(
                    OwnedProcess,
                    [executable, *settings.arguments],
                    root=self.config.runtime_root,
                    component="tts",
                    instance=self.instance,
                    working_directory=settings.working_directory,
                    environment={
                        "CUDA_VISIBLE_DEVICES": "-1",
                        "USE_GPU": "false",
                        "API_LOG_LEVEL": "WARNING",
                        "PYTHONUTF8": "1",
                        "OMP_NUM_THREADS": str(self.config.audio.stt_threads),
                        "MKL_NUM_THREADS": str(self.config.audio.stt_threads),
                        "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1",
                    },
                )
                self.children.append(child)
            async with asyncio.timeout(settings.startup_timeout_seconds):
                while True:
                    if child is not None:
                        child.check()
                    try:
                        await check_voice(settings.endpoint, self.config.audio.tts_voice)
                        return "ready"
                    except (httpx.HTTPError, GatewayError, ValidationError, ValueError):
                        if child is None:
                            raise
                        await asyncio.sleep(0.1)
        except (
            ConfigurationError,
            OSError,
            httpx.HTTPError,
            GatewayError,
            ValidationError,
            ValueError,
            TimeoutError,
        ):
            if child is not None:
                await asyncio.to_thread(child.stop)
                self.children.remove(child)
            return "tts_unavailable_text_output"
        except BaseException:
            # Cancellation must not leak a child before the normal cleanup stack owns it.
            if child is not None:
                await asyncio.to_thread(child.stop)
                self.children.remove(child)
            raise

    async def _remove_strata_directory(self) -> None:
        if self._strata_directory is not None:
            await asyncio.to_thread(self._strata_directory.cleanup)
            self._strata_directory = None

    async def aclose(self) -> None:
        """Drain gateway before stopping its owned runner; never stop an attached process."""
        if self.gateway_owned:
            try:
                token = await asyncio.to_thread(
                    read_token, self.config.runtime_root / "gateway.token"
                )
                async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                    await client.post(
                        self.config.services.gateway.endpoint + "/v1/shutdown",
                        headers={"Authorization": "Bearer " + token},
                    )
                gateway_child = next(
                    child for child in self.children if child.component == "gateway"
                )
                await asyncio.to_thread(
                    gateway_child.wait,
                    self.config.services.livekit.shutdown_timeout_seconds,
                )
            except (OSError, httpx.HTTPError):
                # Owned-process cleanup below remains mandatory; journal uncertainty is preserved.
                print("Gateway shutdown request failed; stopping only the owned child.")
        for child in reversed(self.children):
            await asyncio.to_thread(child.stop)
        self.children.clear()
        self.gateway_owned = False
        await self._remove_strata_directory()


async def run_runtime(
    config: DeploymentConfig,
    config_path: Path,
    *,
    stop: asyncio.Event | None = None,
    open_browser: bool = False,
) -> None:
    """Host one text room until Ctrl+C; unwind room, memory and services in reverse order."""
    if open_browser and not config.browser.enabled:
        raise ConfigurationError("Enable browser.enabled before requesting browser launch.")
    from .network import validate_listener

    await asyncio.to_thread(validate_listener, config)
    async with AsyncExitStack() as cleanup:
        lease = await asyncio.to_thread(RuntimeLease, config.runtime_root)
        cleanup.push_async_callback(asyncio.to_thread, lease.close)
        services = RuntimeServices(config, config_path)
        cleanup.push_async_callback(services.aclose)
        gateway_available = True
        try:
            value = await services.start()
        except (ConfigurationError, OSError, api.TwirpError, TimeoutError):
            # Managed Strata startup already cleans up its children on failure.
            # Retrying here can mask the original error while listeners unwind.
            if not config.browser.enabled or config.inference.backend == "strata":
                raise
            value = None
            try:
                await services.start_gateway()
            except (ConfigurationError, OSError, GatewayError, TimeoutError):
                gateway_available = False
        foreground_token = (
            await asyncio.to_thread(read_token, config.runtime_root / "gateway.token")
            if gateway_available
            else ""
        )
        reservation = HttpGatewayClient(config.services.gateway.endpoint, foreground_token)
        cleanup.push_async_callback(reservation.aclose)
        if gateway_available:
            try:
                async with asyncio.timeout(config.services.livekit.startup_timeout_seconds):
                    await reservation.session(services.instance, connected=True)
            except (GatewayError, OSError, TimeoutError):
                if not config.browser.enabled:
                    raise
                gateway_available = False
                value = None
            else:
                cleanup.push_async_callback(reservation.session, services.instance, connected=False)
        runtime = await asyncio.to_thread(
            LocalRuntime,
            config,
            gateway_available=gateway_available,
            thinking_available=services.thinking_available,
        )
        cleanup.push_async_callback(asyncio.to_thread, runtime.close_workers)
        cleanup.push_async_callback(runtime.aclose)
        if config.memory_mcp is not None:
            from .memory_mcp import serve_memory_mcp

            port = config.memory_mcp.port
            try:
                write_note = None
                if config.memory_mcp.write_enabled:
                    try:
                        write_note = await runtime.opencode_note_writer()
                    except MemoryError as error:
                        print(f"OpenCode memory writes unavailable: {error.info.code}")
                await cleanup.enter_async_context(
                    serve_memory_mcp(
                        runtime.memory,
                        config.runtime_root,
                        port,
                        deep=runtime.deep,
                        timezone=config.recall_timezone,
                        write_note=write_note,
                    )
                )
            except (ConfigurationError, TimeoutError) as error:
                detail = (
                    str(error) if isinstance(error, ConfigurationError) else "Startup timed out."
                )
                print(f"Memory MCP unavailable: {detail}")
            else:
                print(f"Memory MCP ready on http://127.0.0.1:{port}/mcp")
                if write_note is not None:
                    print("OpenCode memory writes enabled for the OpenCode library.")
        binding = RoomBinding(
            instance_id=services.instance,
            character_id=runtime.memory.character_id,
            room="ember-" + services.instance.hex,
            endpoint=config.services.livekit.endpoint,
        )
        if value is not None:
            try:
                await _run_room(
                    config,
                    runtime,
                    services,
                    value,
                    binding,
                    reservation,
                    stop=stop,
                    open_browser=open_browser,
                )
                return
            except (ConfigurationError, OSError, api.TwirpError, TimeoutError):
                if not config.browser.enabled:
                    raise
        await _run_text_browser(
            config,
            runtime,
            services,
            binding,
            reservation,
            gateway_available=gateway_available,
            stop=stop,
            open_browser=open_browser,
        )


async def _run_room(
    config: DeploymentConfig,
    runtime: LocalRuntime,
    services: RuntimeServices,
    value: LiveKitCredentials,
    binding: RoomBinding,
    reservation: HttpGatewayClient,
    *,
    stop: asyncio.Event | None,
    open_browser: bool,
) -> None:
    async with AsyncExitStack() as cleanup:
        service = api.LiveKitAPI(
            binding.endpoint.replace("ws://", "http://").replace("wss://", "https://"),
            value.api_key,
            value.api_secret,
            failover=False,
        )
        cleanup.push_async_callback(service.aclose)

        async def remember(request: RememberInput) -> str:
            return await runtime.remember(request.key, request.text)

        voice = None
        voice_status = "disabled"
        if config.audio.enabled:
            try:
                from ..audio.components import VoiceComponents

                if await services.start_phonon():
                    voice, voice_status = await VoiceComponents.create(
                        config, start_speech=services.start_speech
                    )
                else:
                    voice_status = "stt_artifacts_unavailable"
            except ImportError:
                voice_status = "audio_dependencies_not_installed"
            if voice is not None:
                cleanup.push_async_callback(voice.aclose)
                voice_status = voice.output_status
        # Load speech before creating a room with an empty-room timeout.
        async with asyncio.timeout(5):
            await service.room.create_room(
                api.CreateRoomRequest(name=binding.room, max_participants=2, empty_timeout=30)
            )

        async def delete_room() -> None:
            async with asyncio.timeout(5):
                await service.room.delete_room(api.DeleteRoomRequest(room=binding.room))

        cleanup.push_async_callback(delete_room)

        host = HostedRoom(
            runtime.core, binding, remember, voice=voice, investigate=runtime.investigate
        )
        cleanup.push_async_callback(host.aclose)

        async def reserve(identity: UUID, connected: bool) -> None:
            await reservation.session(identity, connected=connected)

        lifecycle = CaptureLifecycle(services.instance, reserve, host.quiesce, runtime.consolidate)
        host.before_turn = lifecycle.before_turn
        host.connection_changed = lifecycle.connection_changed
        cleanup.push_async_callback(lifecycle.aclose)
        # Explicitly hosted Ember owns this room for the runtime lifetime. LiveKit
        # dependent agent participants do not keep a room alive between human visits.
        # A standard room-scoped host token retains identical media/data limits.
        async with asyncio.timeout(config.services.livekit.startup_timeout_seconds):
            await host.start(
                value.token(binding.room, AGENT_IDENTITY, audio=bool(voice and voice.tts))
            )
        lifecycle.start()
        discovery = config.runtime_root / "livekit-room.json"
        await asyncio.to_thread(publish_room, discovery, binding)
        cleanup.push_async_callback(asyncio.to_thread, discovery.unlink, missing_ok=True)
        if config.browser.enabled:
            if await asyncio.to_thread(port_open, config.browser.endpoint):
                raise ConfigurationError("Browser control port is already occupied.")
            await _start_public_search(runtime)
            task_router = await _start_tasks(runtime, binding, cleanup)
            if task_router is not None:
                host.start_task_announcements(task_router)
                cleanup.push_async_callback(host.stop_task_announcements)
            control = BrowserControl(
                hermes_chat=await _start_hermes_client(runtime),
                dashboard=runtime.dashboard,
                memory_graph=runtime.memory_graph,
                task_router=task_router,
                origin=config.browser.endpoint,
                lan_origin=config.browser.public_origin
                or f"http://{config.browser.lan_address}:{urlsplit(config.browser.endpoint).port}"
                if config.browser.lan_address
                else None,
                trusted_proxy=config.browser.trusted_proxy,
                assets=config.browser.assets,
                binding=binding,
                core=runtime.core,
                deep=runtime.deep,
                controls=runtime,
                prompt_controls=runtime,
                room_token=lambda: value.token(
                    binding.room, USER_IDENTITY, audio=voice is not None
                ),
                answer=host.answer_investigation,
                voice_ready=voice is not None,
                voice_status=voice_status,
                speech_options=lambda: voice.speech_options if voice is not None else None,
                configure_speech=host.configure_speech,
                prompt_idle=host.prompt_idle,
                capture_changed=lifecycle.policy_changed,
            )
            browser = BrowserServer(control, config.runtime_root)
            cleanup.push_async_callback(browser.aclose)
            await browser.start()
            print("Browser controls ready. Open the private browser-activation.json URL.")
            if control.lan_origin:
                print(
                    "LAN browser: "
                    + control.lan_origin
                    + (
                        ". Open this address directly."
                        if config.browser.trusted_proxy
                        else ". Use Copy LAN link in the local browser."
                    )
                )
            if open_browser and not await browser.open_browser():
                print("Browser launch unavailable; open the private activation file URL manually.")
        print("Gateway and LiveKit room ready. Semantic retrieval: " + runtime.semantic_status)
        print("Voice: " + voice_status)
        print("Run ember livekit-chat with the same --config to use terminal chat.")
        await (stop or asyncio.Event()).wait()


async def _run_text_browser(
    config: DeploymentConfig,
    runtime: LocalRuntime,
    services: RuntimeServices,
    binding: RoomBinding,
    reservation: HttpGatewayClient,
    *,
    gateway_available: bool,
    stop: asyncio.Event | None,
    open_browser: bool,
) -> None:
    async with AsyncExitStack() as cleanup:
        text = DirectTextSession(
            runtime.core, runtime.deep, runtime.investigate, model_available=gateway_available
        )

        async def reserve(identity: UUID, connected: bool) -> None:
            if not gateway_available:
                raise GatewayError("gateway_unavailable")
            await reservation.session(identity, connected=connected)

        lifecycle = CaptureLifecycle(services.instance, reserve, text.quiesce, runtime.consolidate)
        text.before_turn = lifecycle.before_turn
        text.connection_changed = lifecycle.connection_changed
        if gateway_available:
            cleanup.push_async_callback(lifecycle.aclose)
            lifecycle.start()

        def room_token() -> str:
            raise GatewayError("livekit_unavailable")

        async def room_answer(_: DeepRecallResult) -> str:
            raise GatewayError("use_browser_text_transport")

        await _start_public_search(runtime)
        task_router = await _start_tasks(runtime, binding, cleanup)
        control = BrowserControl(
            hermes_chat=await _start_hermes_client(runtime),
            dashboard=runtime.dashboard,
            memory_graph=runtime.memory_graph,
            task_router=task_router,
            origin=config.browser.endpoint,
            lan_origin=config.browser.public_origin
            or f"http://{config.browser.lan_address}:{urlsplit(config.browser.endpoint).port}"
            if config.browser.lan_address
            else None,
            trusted_proxy=config.browser.trusted_proxy,
            assets=config.browser.assets,
            binding=binding,
            core=runtime.core,
            deep=runtime.deep,
            controls=runtime,
            prompt_controls=runtime,
            room_token=room_token,
            answer=room_answer,
            text_session=text,
            voice_status="livekit_unavailable",
            capture_changed=lifecycle.policy_changed,
        )
        browser = BrowserServer(control, config.runtime_root)
        cleanup.push_async_callback(browser.aclose)
        await browser.start()
        print(
            "Browser controls ready. Voice transport unavailable. Text: "
            + ("ready" if gateway_available else "unavailable; gateway not ready")
        )
        if open_browser and not await browser.open_browser():
            print("Browser launch unavailable; open the private activation file URL manually.")
        await (stop or asyncio.Event()).wait()


async def run_participant(config: DeploymentConfig, *, prompt: str | None = None) -> None:
    """Join the current runtime without constructing or opening canonical memory."""
    path = config.runtime_root / "livekit-room.json"
    try:
        payload = await asyncio.to_thread(path.read_bytes)
    except FileNotFoundError as error:
        raise ConfigurationError(
            "No Ember room is available. Run ember start with the same --config in another "
            "terminal and wait for the room-ready message, then retry livekit-chat."
        ) from error
    except OSError as error:
        raise ConfigurationError(
            "Cannot read Ember room discovery; check runtime access."
        ) from error
    try:
        binding = RoomBinding.model_validate_json(payload)
    except ValueError as error:
        raise ConfigurationError(
            "Ember room discovery is invalid. Restart ember start with the same --config."
        ) from error
    if binding.endpoint != config.services.livekit.endpoint:
        raise ConfigurationError("LiveKit discovery endpoint does not match configuration.")
    assert config.services.livekit.credentials_file is not None
    value = await asyncio.to_thread(
        credentials, config.services.livekit.credentials_file, create=False
    )
    client = RoomClient(binding)
    try:
        await client.connect(value.token(binding.room, USER_IDENTITY))
        if prompt is not None:
            await client.ask(prompt, on_text=lambda chunk: print(chunk, end="", flush=True))
            print()
            return
        print("Connected to Ember. /remember KEY TEXT saves memory; /quit disconnects.")
        while True:
            try:
                text = await read_line("You: ")
            except EOFError:
                break
            if text.strip() == "/quit":
                break
            if text.startswith("/remember "):
                parts = text.split(" ", 2)
                if len(parts) != 3:
                    print("Usage: /remember KEY TEXT")
                    continue
                try:
                    print("Memory " + await client.remember(parts[1], parts[2]) + ".")
                except (rtc.RpcError, ValueError):
                    print("Memory operation failed; check the key and 1600-character limit.")
                continue
            try:
                print("Ember: ", end="", flush=True)
                _, result = await client.ask(
                    text, on_text=lambda chunk: print(chunk, end="", flush=True)
                )
                print(f"\nRecalled memories: {result.retained_memories}")
            except GatewayError as error:
                print(str(error))
            except ValueError:
                print("Room input must contain at most 1600 characters.")
    finally:
        await client.aclose()
