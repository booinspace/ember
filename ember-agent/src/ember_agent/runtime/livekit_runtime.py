"""Phase 3 composition: managed services and one explicitly dispatched text room."""

import asyncio
import os
import shutil
import socket
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from livekit import api, rtc

from ..inference.client import HttpGatewayClient
from ..inference.contracts import GatewayError
from ..realtime.client import RoomClient
from ..realtime.contracts import (
    AGENT_IDENTITY,
    USER_IDENTITY,
    RememberInput,
    RoomBinding,
)
from ..realtime.session import HostedRoom
from .composition import LocalRuntime
from .config import ConfigurationError, DeploymentConfig
from .console import read_line
from .livekit_config import LiveKitCredentials, credentials, server_yaml
from .processes import OwnedProcess, RuntimeLease


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

        gateway = self.config.services.gateway
        if gateway.mode == "disabled":
            raise ConfigurationError(
                "The gateway is disabled; model-backed room chat is unavailable."
            )
        if not await asyncio.to_thread(port_open, gateway.endpoint):
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
        return self.credentials

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
                await asyncio.to_thread(
                    self.children[-1].wait,
                    self.config.services.livekit.shutdown_timeout_seconds,
                )
            except (OSError, httpx.HTTPError):
                # Owned-process cleanup below remains mandatory; journal uncertainty is preserved.
                print("Gateway shutdown request failed; stopping only the owned child.")
        for child in reversed(self.children):
            await asyncio.to_thread(child.stop)
        self.children.clear()


async def run_runtime(
    config: DeploymentConfig, config_path: Path, *, stop: asyncio.Event | None = None
) -> None:
    """Host one text room until Ctrl+C; unwind room, memory and services in reverse order."""
    async with AsyncExitStack() as cleanup:
        lease = await asyncio.to_thread(RuntimeLease, config.runtime_root)
        cleanup.push_async_callback(asyncio.to_thread, lease.close)
        services = RuntimeServices(config, config_path)
        cleanup.push_async_callback(services.aclose)
        value = await services.start()
        foreground_token = await asyncio.to_thread(
            read_token, config.runtime_root / "gateway.token"
        )
        reservation = HttpGatewayClient(config.services.gateway.endpoint, foreground_token)
        cleanup.push_async_callback(reservation.aclose)
        cleanup.push_async_callback(reservation.session, services.instance, connected=False)
        async with asyncio.timeout(config.services.livekit.startup_timeout_seconds):
            await reservation.session(services.instance, connected=True)
        runtime = await asyncio.to_thread(LocalRuntime, config)
        cleanup.push_async_callback(asyncio.to_thread, runtime.close_workers)
        cleanup.push_async_callback(runtime.aclose)
        binding = RoomBinding(
            instance_id=services.instance,
            character_id=runtime.memory.character_id,
            room="ember-" + services.instance.hex,
            endpoint=config.services.livekit.endpoint,
        )
        service = api.LiveKitAPI(
            binding.endpoint.replace("ws://", "http://").replace("wss://", "https://"),
            value.api_key,
            value.api_secret,
            failover=False,
        )
        cleanup.push_async_callback(service.aclose)
        async with asyncio.timeout(5):
            await service.room.create_room(
                api.CreateRoomRequest(name=binding.room, max_participants=2, empty_timeout=30)
            )

        async def delete_room() -> None:
            async with asyncio.timeout(5):
                await service.room.delete_room(api.DeleteRoomRequest(room=binding.room))

        cleanup.push_async_callback(delete_room)

        async def remember(request: RememberInput) -> str:
            return await runtime.remember(request.key, request.text)

        host = HostedRoom(runtime.core, binding, remember)
        cleanup.push_async_callback(host.aclose)
        async with asyncio.timeout(config.services.livekit.startup_timeout_seconds):
            await host.start(value.token(binding.room, AGENT_IDENTITY, agent=True))
        discovery = config.runtime_root / "livekit-room.json"
        await asyncio.to_thread(publish_room, discovery, binding)
        cleanup.push_async_callback(asyncio.to_thread, discovery.unlink, missing_ok=True)
        print("Memory ready; retrieval degraded. Gateway ready. LiveKit text room ready.")
        print("Run ember livekit-chat with the same --config. Voice/browser are not implemented.")
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
