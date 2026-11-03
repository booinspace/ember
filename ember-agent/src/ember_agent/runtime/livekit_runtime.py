"""Phase 3 composition: managed services and one explicitly dispatched text room."""

import asyncio
import os
import shutil
import socket
import sqlite3
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from livekit import api, rtc
from pydantic import ValidationError

from ..inference.client import HttpGatewayClient
from ..inference.contracts import GatewayError
from ..memory.deep_contracts import DeepRecallResult
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
from .livekit_config import LiveKitCredentials, credentials, server_yaml
from .processes import OwnedProcess, RuntimeLease
from .speech_health import check_voice
from .task_router import (
    RecallTask,
    SummaryTask,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolRouter,
)
from .text_session import DirectTextSession


async def _start_tasks(
    runtime: LocalRuntime, binding: RoomBinding, cleanup: AsyncExitStack
) -> TaskRouter | None:
    token_path = runtime.config.runtime_root / "gateway-agent.token"
    try:
        token = (await asyncio.to_thread(token_path.read_text, encoding="utf-8")).strip()
    except OSError:
        token = ""
    summary = None
    if 32 <= len(token) <= 128:
        client = HttpGatewayClient(runtime.config.services.gateway.endpoint, token)
        cleanup.push_async_callback(client.aclose)
        summary = SummaryTask(client, runtime.config.model_tag)
    router = TaskRouter(
        runtime.config.runtime_root,
        binding.character_id,
        binding.instance_id,
        TextInspectionTask(ToolRouter(TextInspectionProvider(), binding.character_id)),
        summary=summary,
        recall=RecallTask(
            runtime.deep, runtime.core.reference_clock, runtime.core.calendar.timezone
        ),
    )
    try:
        await router.start()
    except (OSError, sqlite3.Error):
        print("Tasks unavailable: operational storage could not start.")
        return None
    runtime.core.submit_task = router.submit_turn
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

    async def start_gateway(self) -> None:
        """Verify/start the sole gateway independently of voice transport availability."""
        settings = self.config.services.livekit
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
                    [
                        sys.executable,
                        "-m",
                        "ember_agent",
                        "gateway",
                        "--config",
                        str(self.config_path),
                    ],
                    root=self.config.runtime_root,
                    component="gateway",
                    instance=self.instance,
                )
            )
            self.gateway_owned = True
        try:
            async with asyncio.timeout(settings.startup_timeout_seconds):
                while True:
                    for child in self.children:
                        if child.component == "gateway":
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
        """Start/attach installed Kokoro independently; provider failures leave text available."""
        settings = self.config.services.kokoro
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
                    component="kokoro",
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

    async def aclose(self) -> None:
        """Request graceful gateway drain, then stop only the exact handles created here."""
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
            if not config.browser.enabled:
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
            task_router = await _start_tasks(runtime, binding, cleanup)
            control = BrowserControl(
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
                room_token=lambda: value.token(
                    binding.room, USER_IDENTITY, audio=voice is not None
                ),
                answer=host.answer_investigation,
                voice_ready=voice is not None,
                voice_status=voice_status,
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

        task_router = await _start_tasks(runtime, binding, cleanup)
        control = BrowserControl(
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
