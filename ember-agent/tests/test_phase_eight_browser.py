"""Operator boundaries prevent cross-origin control and private validation-value disclosure."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ember_agent.character.core import CharacterCore
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.runtime.browser_control import BrowserControl
from ember_agent.runtime.controls import CaptureControl, GraphInput, LibraryChoice, ProcessingStatus
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

        async def graph(_: GraphInput) -> str:
            return '{"nodes":[],"links":[],"next_cursor":null,"truncated":false}'

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
            memory_graph=graph,
        )
        with TestClient(owner.app, base_url=ORIGIN, headers={"origin": ORIGIN}) as client:
            assert client.get("/api/state").status_code == 401
            assert client.post("/api/memory-graph", json={}).status_code == 401
            assert client.get("/api/skills").status_code == 401
            assert client.post("/api/skills/refresh", json={}).status_code == 401
            assert client.get("/").status_code == 503
            assert client.post("/api/bootstrap", json={"token": "x" * 48}).status_code == 401
            response = client.post("/api/bootstrap", json={"token": owner.activation})
            assert response.status_code == 200
            assert "httponly" in response.headers["set-cookie"].lower()
            assert "samesite=strict" in response.headers["set-cookie"].lower()
            assert client.get("/api/state").json()["binding"]["character_id"] == str(identity)
            result = client.post("/api/memory-graph", json={})
            assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
            assert result.json()["nodes"] == []
            assert client.post("/api/memory-graph", json={"cursor": "invalid"}).status_code == 400
            assert client.get("/memory-graph").status_code == 503

            catalog = client.get("/api/skills")
            assert catalog.status_code == 200
            assert catalog.headers["cache-control"] == "no-store"
            assert catalog.json()["unavailable"] == "skills_provider_unconfigured"
            assert client.get("/api/skills/document?identity=shared/brief").status_code == 503
            assert client.post("/api/skills/refresh", json={}).status_code == 503
            assert (
                client.post(
                    "/api/skills/refresh", json={}, headers={"origin": "http://unrelated.example"}
                ).status_code
                == 403
            )
            # Portable bundles have their own bounded envelope; unrelated controls stay small.
            bundle = {"name": "brief", "files": {"SKILL.md": "a" * 40000}}
            assert client.post("/api/skills/import", json=bundle).status_code == 503
            bundle["files"]["SKILL.md"] = "a" * 262144
            assert client.post("/api/skills/import", json=bundle).status_code == 413
            assert (
                client.post("/api/room-token", json={}).json()["token"]
                == "synthetic_room_capability"
            )
            assert (
                client.post("/api/capture", json={"enabled": False, "revision": 0}).status_code
                == 200
            )
            state = client.get("/api/state").json()
            assert "hermes_chat_available" not in state and "hermes_private_recall" not in state
            assert client.get("/hermes/v1/models").status_code == 404
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


def test_owned_browser_socket_startup_shutdown_and_port_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket
    from unittest.mock import AsyncMock, Mock

    import httpx

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
            with pytest.raises(ConfigurationError, match="not ready"):
                await server.open_browser()
            opened = Mock(return_value=True)
            monkeypatch.setattr("ember_agent.runtime.browser_server.webbrowser.open", opened)
            await server.start()
            assert server.activation_file.is_file()
            assert await server.open_browser()
            opened.assert_called_once_with(origin + "/#activate=" + owner.activation, new=2)
            opened.return_value = False
            assert not await server.open_browser()
            opened.side_effect = OSError("synthetic desktop failure")
            assert not await server.open_browser()
            assert server.task is not None and server.server.started
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


@pytest.mark.parametrize("preview", [False, True])
def test_browser_deep_archive_relevance_is_opt_in(tmp_path: Path, preview: bool) -> None:
    from unittest.mock import patch

    import httpx

    from ember_agent.runtime.composition import LocalRuntime
    from ember_agent.runtime.config import load_config
    from ember_memory_abi.interfaces import ArchiveSearchResponse, RetrievalCoverage

    example = Path(__file__).parents[1] / "config/deployment.example.toml"
    path = tmp_path / "deployment.toml"
    text = example.read_text(encoding="utf-8")
    if preview:
        text = text.replace("# deep_archive_relevance", "deep_archive_relevance")
    path.write_text(text, encoding="utf-8")
    config = load_config(path).model_copy(
        update={
            "data_root": tmp_path / "data",
            "runtime_root": tmp_path / "runtime",
            "system_prompt_file": tmp_path / "prompts/system.md",
            "embedding_directory": None,
        }
    )
    runtime = LocalRuntime(config, gateway_available=False)

    async def answer(_: object) -> str:
        return "unused"

    async def exercise() -> None:
        owner = BrowserControl(
            origin=ORIGIN,
            assets=tmp_path / "unbuilt",
            binding=RoomBinding(
                instance_id=uuid4(),
                character_id=runtime.memory.character_id,
                room="ember-" + uuid4().hex,
                endpoint="ws://127.0.0.1:7880",
            ),
            core=runtime.core,
            deep=runtime.deep,
            controls=Controls(),
            room_token=lambda: "synthetic",
            answer=answer,
        )
        response = ArchiveSearchResponse(
            results=(), coverage=RetrievalCoverage(status="complete", index_status="available")
        )
        try:
            with patch.object(
                InProcessAsyncMemoryClient, "search_archive", return_value=response
            ) as search:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=owner.app),
                    base_url=ORIGIN,
                    headers={"origin": ORIGIN},
                ) as browser:
                    assert (
                        await browser.post("/api/bootstrap", json={"token": owner.activation})
                    ).is_success
                    await runtime.core.prepare("Synthetic router topic")
                    source = await runtime.core.prepare("What was the original source?")
                    assert source.omissions == ("source_followup_unavailable",)
                    search.assert_not_called()
                    request_id = uuid4()
                    started = await browser.post(
                        "/api/deep",
                        json={"request_id": str(request_id), "text": "Synthetic router topic"},
                    )
                    assert started.is_success
                    await asyncio.wait_for(owner._tasks[request_id], timeout=5)
                    assert (await browser.get(f"/api/deep/{request_id}")).json()["result"]
                search.assert_awaited_once()
                query = search.await_args.args[0]
                assert query.mode == "hybrid" and query.roles == ("user",)
                assert query.options.deadline_at is not None
                assert query.options.extensions == (
                    {"ember.archive_relevance": "corroborated_v1"} if preview else {}
                )
        finally:
            await owner.aclose()
            await runtime.aclose()

    try:
        asyncio.run(exercise())
    finally:
        runtime.close_workers()


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


def test_authenticated_operator_task_round_trip(tmp_path: Path) -> None:
    import httpx

    from ember_agent.runtime.task_router import (
        RecallTask,
        TaskRouter,
        TextInspectionProvider,
        TextInspectionTask,
        ToolRouter,
    )
    from ember_memory_engine.deep_profile import DeepSources
    from ember_memory_engine.read_budget import ReadBudget

    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "canonical") as engine:
            identity = engine.create_character().store_id
            memory = InProcessAsyncMemoryClient(engine.character(identity))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")

            def deep_client(deadline: float) -> InProcessAsyncMemoryClient:
                budget = ReadBudget(deadline=deadline)
                return InProcessAsyncMemoryClient(
                    engine.character(identity),
                    deep_sources=DeepSources(engine, budget),
                    read_budget=budget,
                )

            deep = DeepRecallService(identity, deep_client, validation=memory)
            session = uuid4()
            router = TaskRouter(
                tmp_path / "runtime",
                identity,
                session,
                TextInspectionTask(ToolRouter(TextInspectionProvider(), identity)),
                recall=RecallTask(deep, core.reference_clock, core.calendar.timezone),
            )

            async def answer(_: object) -> str:
                return "receipt"

            owner = BrowserControl(
                origin=ORIGIN,
                assets=tmp_path / "unbuilt",
                binding=RoomBinding(
                    instance_id=session,
                    character_id=identity,
                    room="ember-" + uuid4().hex,
                    endpoint="ws://127.0.0.1:7880",
                ),
                core=core,
                deep=deep,
                controls=Controls(),
                room_token=lambda: "test",
                answer=answer,
                task_router=router,
            )
            await router.start()
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=owner.app),
                    base_url=ORIGIN,
                    headers={"origin": ORIGIN},
                ) as client:
                    payload = {"idempotency_key": "same", "text": "two words"}
                    assert (await client.post("/api/tasks", json=payload)).status_code == 401
                    assert (
                        await client.post("/api/bootstrap", json={"token": owner.activation})
                    ).status_code == 200
                    response = await client.post("/api/tasks", json=payload)
                    assert response.status_code == 202
                    task = response.json()["task_id"]
                    retry = await client.post("/api/tasks", json=payload)
                    assert retry.json()["task_id"] == task
                    async with asyncio.timeout(3):
                        while True:
                            status = (await client.get("/api/tasks/" + task)).json()
                            if status["state"] == "succeeded":
                                break
                            await asyncio.sleep(0.01)
                    assert status["result"] == "2 words, 9 characters."
                    artifact_route = "/api/tasks/" + task + "/artifact"
                    assert (await client.get(artifact_route)).status_code == 409
                    artifact = await client.post(artifact_route, json={})
                    assert artifact.status_code == 200
                    assert artifact.json()["size_bytes"] == len(status["result"].encode())
                    assert (await client.get(artifact_route)).text == status["result"]
                    artifact_path = tmp_path / "runtime" / "artifacts" / (task + ".txt")
                    artifact_path.write_text("changed", encoding="utf-8")
                    assert (await client.get(artifact_route)).status_code == 409

                    core.task_notice = router.turn_notice
                    notice = await core.prepare("Any task updates?")
                    assert notice.local_reply == "You have 1 task result(s) in Tasks."
                    events = [event async for event in core.stream(notice)]
                    assert [event.kind for event in events] == ["start", "text", "end"]
                    assert (await client.get("/api/task-notifications")).json()["items"]

                    delivery = (await client.get("/api/task-notifications")).json()
                    assert [item["task_id"] for item in delivery["items"]] == [task]
                    for _ in range(2):
                        receipt = await client.post("/api/tasks/" + task + "/acknowledge", json={})
                        assert receipt.status_code == 200
                    assert (await client.get("/api/task-notifications")).json()["items"] == []

                    recall = await client.post(
                        "/api/tasks",
                        json={
                            "idempotency_key": "recall",
                            "text": "synthetic",
                            "operation": "recall",
                        },
                    )
                    assert recall.status_code == 202
                    recall_id = recall.json()["task_id"]
                    async with asyncio.timeout(3):
                        while True:
                            status = (await client.get("/api/tasks/" + recall_id)).json()
                            if status["state"] in {"succeeded", "failed"}:
                                break
                            await asyncio.sleep(0.01)
                    assert status["state"] == "succeeded"
                    response = await client.get("/api/tasks/" + recall_id + "/recall")
                    assert response.json()["result"]["context_id"] == recall_id
                    ordinary_view = await client.get("/api/deep/" + recall_id)
                    assert ordinary_view.json()["result"] is None

                    review = await client.post(
                        "/api/tasks",
                        json={
                            "idempotency_key": "review",
                            "text": "A synthetic draft",
                            "operation": "review",
                        },
                    )
                    assert review.status_code == 202
                    review_id = review.json()["task_id"]
                    async with asyncio.timeout(3):
                        while True:
                            confirmations = (await client.get("/api/task-confirmations")).json()
                            if confirmations:
                                break
                            await asyncio.sleep(0.01)
                    confirmation = confirmations[0]
                    assert confirmation["arguments"] == {"draft": "A synthetic draft"}
                    wrong = await client.post(
                        "/api/task-confirmations/" + confirmation["confirmation_id"],
                        json={"action_sha256": "0" * 64, "approve": True},
                    )
                    assert wrong.status_code == 409
                    approved = await client.post(
                        "/api/task-confirmations/" + confirmation["confirmation_id"],
                        json={"action_sha256": confirmation["action_sha256"], "approve": True},
                    )
                    assert approved.json()["state"] == "approved"
                    async with asyncio.timeout(3):
                        while True:
                            review_status = (await client.get("/api/tasks/" + review_id)).json()
                            if review_status["state"] in {"succeeded", "failed"}:
                                break
                            await asyncio.sleep(0.01)
                    assert review_status["state"] == "succeeded"
                    assert review_status["result"] == "A synthetic draft"
                    published = await client.get("/api/tasks/" + review_id + "/artifact")
                    assert published.status_code == 200
                    assert published.text == "A synthetic draft"

                    core.submit_task = router.submit_turn
                    turn = await core.prepare("Count the words in a synthetic example")
                    assert turn.local_reply and "queued" in turn.local_reply
                    assert turn.retained_memories == 0
                    events = [event async for event in core.stream(turn)]
                    assert [event.kind for event in events] == ["start", "text", "end"]

            finally:
                await owner.aclose()
                await router.aclose()
                await deep.aclose()
                await memory.aclose()

    asyncio.run(exercise())
