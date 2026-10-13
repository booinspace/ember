"""Operator boundaries prevent cross-origin control and private validation-value disclosure."""

import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from ember_agent.character.core import CharacterCore
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.runtime.browser_control import BrowserControl
from ember_agent.runtime.controls import CaptureControl, LibraryChoice, ProcessingStatus
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient

from .test_character import FakeModel

ORIGIN = "http://127.0.0.1:8787"


class Controls:
    def __init__(self) -> None:
        self.capture = CaptureControl(enabled=True, revision=0)
        self.recall = RecallSelection()

    async def processing_status(self) -> ProcessingStatus:
        return ProcessingStatus(counts={}, failure_codes=(), index_status="missing")

    async def libraries(self) -> tuple[LibraryChoice, ...]:
        return ()

    async def capture_settings(self) -> CaptureControl:
        return self.capture

    async def configure_capture(self, value: CaptureControl) -> CaptureControl:
        self.capture = value
        return value

    async def recall_settings(self) -> RecallSelection:
        return self.recall

    async def configure_recall(self, value: RecallSelection) -> RecallSelection:
        self.recall = value
        return value


def test_operator_authentication_origin_and_safe_validation(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "canonical") as engine:
        identity = engine.create_character().store_id
        memory = InProcessAsyncMemoryClient(engine.character(identity))
        core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
        deep = DeepRecallService(identity, lambda _: memory, validation=memory)

        async def answer(_: object) -> str:
            return "synthetic_receipt"

        owner = BrowserControl(
            origin=ORIGIN,
            assets=tmp_path / "unbuilt",
            binding=RoomBinding(
                instance_id=uuid4(),
                character_id=identity,
                room="ember-" + uuid4().hex,
                endpoint="ws://127.0.0.1:7880",
            ),
            core=core,
            deep=deep,
            controls=Controls(),
            room_token=lambda: "synthetic_room_capability",
            answer=answer,
        )
        with TestClient(owner.app, base_url=ORIGIN, headers={"origin": ORIGIN}) as client:
            assert client.get("/api/state").status_code == 401
            assert client.get("/").status_code == 503
            assert client.post("/api/bootstrap", json={"token": "x" * 48}).status_code == 401
            response = client.post("/api/bootstrap", json={"token": owner.activation})
            assert response.status_code == 200
            assert "httponly" in response.headers["set-cookie"].lower()
            assert "samesite=strict" in response.headers["set-cookie"].lower()
            assert client.get("/api/state").json()["binding"]["character_id"] == str(identity)
            assert (
                client.post("/api/room-token", json={}).json()["token"]
                == "synthetic_room_capability"
            )
            assert (
                client.post("/api/capture", json={"enabled": False, "revision": 0}).status_code
                == 200
            )
            state = client.get("/api/state").json()
            assert not state["capture"]["enabled"] and state["recall"]["local_enabled"]
            assert (
                client.post(
                    "/api/capture",
                    json={"enabled": True, "revision": 0},
                    headers={"origin": "http://unrelated.example"},
                ).status_code
                == 403
            )
            private = "SYNTHETIC_PRIVATE_INVALID_VALUE"
            invalid = client.post("/api/capture", json={"enabled": private, "revision": 0})
            assert invalid.status_code == 400 and private not in invalid.text
            assert (
                client.get("/api/state", headers={"host": "unrelated.example"}).status_code == 403
            )
            assert client.post("/api/room-token", content=b"x" * 32769).status_code == 413
        asyncio.run(owner.aclose())
        asyncio.run(deep.aclose())
        asyncio.run(memory.aclose())


def test_owned_browser_socket_startup_shutdown_and_port_conflict(tmp_path: Path) -> None:
    import socket
    from unittest.mock import AsyncMock

    import httpx
    import pytest

    from ember_agent.runtime.browser_server import BrowserServer
    from ember_agent.runtime.config import ConfigurationError

    with CanonicalEngine(tmp_path / "canonical") as engine:
        identity = engine.create_character().store_id
        memory = InProcessAsyncMemoryClient(engine.character(identity))
        core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
        deep = DeepRecallService(identity, lambda _: memory, validation=memory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = "http://127.0.0.1:" + str(port)
        owner = BrowserControl(
            origin=origin,
            assets=tmp_path / "unbuilt",
            binding=RoomBinding(
                instance_id=uuid4(),
                character_id=identity,
                room="ember-" + uuid4().hex,
                endpoint="ws://127.0.0.1:7880",
            ),
            core=core,
            deep=deep,
            controls=Controls(),
            room_token=lambda: "synthetic_room",
            answer=AsyncMock(return_value="receipt"),
        )
        server = BrowserServer(owner, tmp_path)

        async def exercise() -> None:
            await server.start()
            assert server.activation_file.is_file()
            async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
                assert (await client.get("/api/state")).status_code == 401
                response = await client.post(
                    "/api/bootstrap", json={"token": owner.activation}, headers={"origin": origin}
                )
                assert response.status_code == 200
                assert (await client.get("/api/state")).status_code == 200
            await server.aclose()
            assert server.task is None and server.socket is None
            assert not server.activation_file.exists()

            # A conflict raises a domain diagnostic, never uvicorn SystemExit.
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", port))
                occupied.listen()
                conflicting = BrowserServer(owner, tmp_path)
                with pytest.raises(ConfigurationError, match="port is unavailable"):
                    await conflicting.start()
                assert conflicting.task is None and not conflicting.activation_file.exists()
            await deep.aclose()
            await memory.aclose()

        asyncio.run(exercise())


def test_status_history_remains_bounded_while_older_work_is_active(tmp_path: Path) -> None:
    from ember_agent.memory.deep_contracts import DeepRecallRequest, DeepRecallResult

    with CanonicalEngine(tmp_path / "canonical") as engine:
        character = engine.create_character().store_id
        memory = InProcessAsyncMemoryClient(engine.character(character))
        core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
        deep = DeepRecallService(character, lambda _: memory, validation=memory)
        held = uuid4()
        release = asyncio.Event()

        async def investigate(request: DeepRecallRequest) -> DeepRecallResult:
            if request.request_id == held:
                await release.wait()
            return DeepRecallResult(
                request_id=request.request_id,
                character_id=character,
                context_id=core.context_id,
                outcome="deferred",
            )

        deep.investigate = investigate

        async def answer(_: object) -> str:
            return "unused"

        owner = BrowserControl(
            origin=ORIGIN,
            assets=tmp_path / "unbuilt",
            binding=RoomBinding(
                instance_id=uuid4(),
                character_id=character,
                room="ember-" + uuid4().hex,
                endpoint="ws://127.0.0.1:7880",
            ),
            core=core,
            deep=deep,
            controls=Controls(),
            room_token=lambda: "synthetic",
            answer=answer,
        )
        with TestClient(owner.app, base_url=ORIGIN, headers={"origin": ORIGIN}) as client:
            client.post("/api/bootstrap", json={"token": owner.activation})
            assert (
                client.post(
                    "/api/deep",
                    json={
                        "request_id": str(held),
                        "text": "held",
                    },
                ).status_code
                == 200
            )
            for _ in range(25):
                identity = uuid4()
                assert (
                    client.post(
                        "/api/deep",
                        json={
                            "request_id": str(identity),
                            "text": "immediate",
                        },
                    ).status_code
                    == 200
                )
                client.get("/api/deep/" + str(identity))
            assert held in owner._tasks
            assert len(owner._statuses) <= 16
            assert client.portal is not None
            client.portal.call(owner.aclose)
        asyncio.run(deep.aclose())
        asyncio.run(memory.aclose())
