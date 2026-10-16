"""LAN access preserves local model boundaries and authenticated browser/room scope."""

import asyncio
import json
import socket
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from ember_agent.character.core import CharacterCore
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.runtime.browser_control import BrowserControl
from ember_agent.runtime.browser_server import BrowserServer
from ember_agent.runtime.config import ConfigurationError, load_config
from ember_agent.runtime.controls import CaptureControl, ProcessingStatus, RuntimeControls
from ember_agent.runtime.livekit_config import LiveKitCredentials, server_yaml
from ember_agent.runtime.network import network_info
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_character import FakeModel

EXAMPLE = Path(__file__).parents[1] / "config/deployment.example.toml"


def controls() -> Mock:
    """Keep LAN tests independent of another test module's collection order."""
    value = Mock(spec=RuntimeControls)
    value.recall_settings = AsyncMock(return_value=RecallSelection())
    value.capture_settings = AsyncMock(return_value=CaptureControl(enabled=True, revision=0))
    value.libraries = AsyncMock(return_value=())
    value.processing_status = AsyncMock(
        return_value=ProcessingStatus(counts={}, failure_codes=(), index_status="missing")
    )
    return value


@pytest.mark.parametrize("address", ["10.254.254.11", "10.20.30.40", "172.31.1.2"])
def test_explicit_lan_configuration_keeps_internal_model_endpoints_local(
    tmp_path: Path, address: str
) -> None:
    path = tmp_path / "deployment.toml"
    text = (
        EXAMPLE.read_text(encoding="utf-8")
        .replace(
            'endpoint = "ws://127.0.0.1:7880"\nmode = "attach"',
            'endpoint = "ws://127.0.0.1:7880"\nmode = "managed"',
        )
        .replace("[browser]\nenabled = false", "[browser]\nenabled = true")
        .replace('lan_address = ""', f'lan_address = "{address}"')
    )
    path.write_text(text, encoding="utf-8")
    config = load_config(path)
    assert config.browser.lan_address == address
    assert config.services.gateway.endpoint == "http://127.0.0.1:11435"
    assert config.services.ollama.endpoint == "http://127.0.0.1:11434"
    assert not config.data_root.exists()


@pytest.mark.parametrize(
    "address",
    ["0.0.0.0", "8.8.8.8", "127.0.0.1", "169.254.1.2", "::1", "example.com", "192.168.1.999"],
)
def test_lan_configuration_refuses_public_wildcard_and_unassigned_address_forms(
    tmp_path: Path, address: str
) -> None:
    path = tmp_path / "deployment.toml"
    path.write_text(
        EXAMPLE.read_text(encoding="utf-8")
        .replace(
            'endpoint = "ws://127.0.0.1:7880"\nmode = "attach"',
            'endpoint = "ws://127.0.0.1:7880"\nmode = "managed"',
        )
        .replace("[browser]\nenabled = false", "[browser]\nenabled = true")
        .replace('lan_address = ""', f'lan_address = "{address}"'),
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="LAN|IPv4"):
        load_config(path)


def test_firewall_descriptor_contains_only_browser_and_livekit_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "deployment.toml"
    path.write_text(
        EXAMPLE.read_text(encoding="utf-8")
        .replace(
            'endpoint = "ws://127.0.0.1:7880"\nmode = "attach"',
            'endpoint = "ws://127.0.0.1:7880"\nmode = "managed"',
        )
        .replace("[browser]\nenabled = false", "[browser]\nenabled = true")
        .replace('lan_address = ""', 'lan_address = "10.254.254.11"'),
        encoding="utf-8",
    )
    monkeypatch.setattr("ember_agent.runtime.network.shutil.which", lambda _: "livekit-server.exe")
    result = network_info(load_config(path))
    assert result.enabled
    assert {(rule.protocol, rule.port) for rule in result.rules} == {
        ("TCP", 8787),
        ("TCP", 7880),
        ("TCP", 7881),
        ("UDP", 7882),
    }
    assert "activation" not in result.model_dump_json() and "token" not in result.model_dump_json()
    value = LiveKitCredentials(api_key="synthetic_key", api_secret="s" * 40)
    assert "bind_addresses: [0.0.0.0]" in server_yaml(
        value, port=7880, tcp_port=7881, udp_port=7882, lan=True
    )
    assert "bind_addresses: [127.0.0.1]" in server_yaml(
        value, port=7880, tcp_port=7881, udp_port=7882
    )


def test_lan_browser_requires_activation_and_same_origin_and_advertises_remote_livekit(
    tmp_path: Path,
) -> None:
    with CanonicalEngine(tmp_path / "canonical") as engine:
        identity = engine.create_character().store_id
        memory = InProcessAsyncMemoryClient(engine.character(identity))
        deep = DeepRecallService(identity, lambda _: memory, validation=memory)
        origin = "http://127.0.0.1:8787"
        lan = "http://10.254.254.11:8787"
        owner = BrowserControl(
            origin=origin,
            lan_origin=lan,
            assets=tmp_path,
            binding=RoomBinding(
                instance_id=uuid4(),
                character_id=identity,
                room="ember-" + uuid4().hex,
                endpoint="ws://127.0.0.1:7880",
            ),
            core=CharacterCore(memory, FakeModel(), model_tag="synthetic"),
            deep=deep,
            controls=controls(),
            room_token=lambda: "synthetic_room",
            answer=AsyncMock(return_value="receipt"),
        )

        async def exercise() -> None:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=owner.app, client=("192.168.10.20", 3000)),
                base_url=lan,
            ) as client:
                assert (await client.get("/api/state")).status_code == 401
                assert (
                    await client.get("/api/state", headers={"host": "attacker.example"})
                ).status_code == 403
                assert (
                    await client.post(
                        "/api/bootstrap",
                        json={"token": owner.activation},
                        headers={"origin": origin},
                    )
                ).status_code == 403
                response = await client.post(
                    "/api/bootstrap", json={"token": owner.activation}, headers={"origin": lan}
                )
                assert response.status_code == 200
                state = (await client.get("/api/state")).json()
                assert state["binding"]["endpoint"] == "ws://10.254.254.11:7880"
                assert state["lan_available"]
                token = await client.post("/api/room-token", json={}, headers={"origin": lan})
                assert token.json()["endpoint"] == "ws://10.254.254.11:7880"
                assert (
                    await client.post("/api/recall", json={}, headers={"origin": origin})
                ).status_code == 403
                link = await client.post("/api/lan-link", json={}, headers={"origin": lan})
                assert link.json()["url"] == lan + "/#activate=" + owner.activation
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=owner.app, client=("8.8.8.8", 3000)), base_url=lan
            ) as client:
                assert (await client.get("/api/state")).status_code == 403
            await owner.aclose()
            await deep.aclose()
            await memory.aclose()

        asyncio.run(exercise())


def test_lan_browser_binds_wildcard_and_retains_private_activation_file(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "canonical") as engine:
        identity = engine.create_character().store_id
        memory = InProcessAsyncMemoryClient(engine.character(identity))
        deep = DeepRecallService(identity, lambda _: memory, validation=memory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        lan = f"http://10.254.254.11:{port}"
        owner = BrowserControl(
            origin=origin,
            lan_origin=lan,
            assets=tmp_path,
            binding=RoomBinding(
                instance_id=uuid4(),
                character_id=identity,
                room="ember-" + uuid4().hex,
                endpoint="ws://127.0.0.1:7880",
            ),
            core=CharacterCore(memory, FakeModel(), model_tag="synthetic"),
            deep=deep,
            controls=controls(),
            room_token=lambda: "synthetic_room",
            answer=AsyncMock(return_value="receipt"),
        )
        server = BrowserServer(owner, tmp_path)

        async def exercise() -> None:
            try:
                await server.start()
                assert server.socket is not None and server.socket.getsockname()[0] == "0.0.0.0"
                activation = json.loads(server.activation_file.read_text(encoding="utf-8"))
                assert activation["url"].startswith(origin)
                assert activation["lan_url"].startswith(lan)
                async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
                    assert (
                        await client.get("/api/state", headers={"host": f"10.254.254.11:{port}"})
                    ).status_code == 401
            finally:
                await server.aclose()
                await deep.aclose()
                await memory.aclose()
            assert not server.activation_file.exists()

        asyncio.run(exercise())
