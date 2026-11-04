"""Paired chat uses one character turn and one gateway call, with bounded provenance."""

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from ember_agent.character.core import CharacterCore
from ember_agent.character.prompts import compile_prompts
from ember_agent.inference.contracts import (
    GatewayError,
    GatewayEvent,
    GatewayRequest,
    GenerationUsage,
)
from ember_agent.memory.backends.dialogue import ArchiveDialogueSink
from ember_agent.runtime.hermes_chat import HermesChats, explicit_public_query
from ember_memory_abi.interfaces import ForgetRequest
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_importers.normalization.extraction import explicit_primary_gpu

from .test_character import FakeModel

TOKEN = "synthetic-paired-client-credential" * 2
TOOL = {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}


def headers(alias: str = "chat1", turn: str = "turn1", request: str = "request1") -> dict[str, str]:
    return {
        "Authorization": "Bearer " + TOKEN,
        "X-Ember-Session": alias,
        "X-Ember-Turn": turn,
        "X-Ember-Request": request,
    }


def body(
    text: str = "Hello",
    *,
    prior: tuple[dict[str, object], ...] = (),
    tools: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    return {
        "model": "ember",
        "messages": [
            {"role": "system", "content": "UNTRUSTED_CLIENT_PERSONA"},
            *prior,
            {"role": "user", "content": text},
        ],
        "tools": list(tools),
        "max_tokens": 512,
    }


@asynccontextmanager
async def application(
    root: Path,
    *,
    model: FakeModel | None = None,
    timeout: float = 180,
    state: bool = True,
    private: bool = False,
    reserve: Callable[[UUID, bool], Awaitable[None]] | None = None,
) -> AsyncGenerator[SimpleNamespace, None]:
    with CanonicalEngine(root / "memory") as engine:
        character = engine.create_character().store_id
        memory = InProcessAsyncMemoryClient(
            engine.character(character, authority=MutationAuthority(mode="confirmed_user"))
        )
        sink = ArchiveDialogueSink(engine.archive(character))
        model = model or FakeModel()
        cores = []

        def factory() -> CharacterCore:
            core = CharacterCore(
                memory, model, model_tag="pinned-model", sink=sink, recall_enabled=private
            )
            core.load_prompts = AsyncMock(
                return_value=compile_prompts("SHARED_RULES_SENTINEL", "PERSONALITY_SENTINEL", 7000)
            )
            cores.append(core)
            return core

        manager = HermesChats(
            factory,
            model,
            client_token=TOKEN,
            model_context_tokens=131072,
            state_root=root / "metadata" if state else None,
            idle_seconds=timeout,
            reserve=reserve,
            private_recall=private,
        )
        app = FastAPI()
        app.include_router(manager.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
            base_url="http://127.0.0.1",
        ) as http:
            try:
                yield SimpleNamespace(
                    manager=manager,
                    model=model,
                    cores=cores,
                    http=http,
                    app=app,
                    memory=memory,
                    factory=factory,
                    engine=engine,
                    character=character,
                )
            finally:
                await manager.aclose()
                await sink.aclose()
                await memory.aclose()


def seed_gpu(env: SimpleNamespace) -> UUID:
    subject = ensure_subject(env.engine, env.character)
    receipt = MemoryAuthoringService(
        env.engine, subject, authority=MutationAuthority(mode="confirmed_user")
    ).create(
        NewMemoryInput(
            idempotency_key="gpu",
            text="My primary GPU is RTX 3090.",
            payload=explicit_primary_gpu("My primary GPU is RTX 3090.", subject),
        )
    )
    return receipt.affected[0].memory_id


async def forget_gpu(env: SimpleNamespace, identity: UUID) -> None:
    await env.memory.forget(
        ForgetRequest(
            memory_id=identity,
            expected_revision=1,
            idempotency_key="forget-gpu",
            reason="Synthetic user correction",
        )
    )


class MemoryModel(FakeModel):
    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent]:
        private = any("RTX 3090" in message.content for message in request.messages)
        async for event in super().stream(request):
            yield (
                event.model_copy(
                    update={"text": "PRIVATE_GPU_REPLY_RTX3090" if private else "NO_PRIVATE_RECALL"}
                )
                if event.kind == "text"
                else event
            )


def test_private_chats_share_authorized_memory_with_separate_bounded_persona_history(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        async with application(tmp_path, private=True, model=MemoryModel()) as env:
            seed_gpu(env)
            assert env.manager.private_recall is True
            first = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(),
                json=body("What is my primary GPU?"),
            )
            answer = first.json()["choices"][0]["message"]["content"]
            assert answer == "PRIVATE_GPU_REPLY_RTX3090"
            other = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(alias="chat2"),
                json=body("Tell me my primary GPU again, SECOND_CHAT"),
            )
            assert other.json()["choices"][0]["message"]["content"] == answer
            assert len(env.model.requests) == 2
            assert env.cores[0].context_id != env.cores[1].context_id
            assert env.cores[0].memory is env.cores[1].memory
            assert env.cores[0].history[0].assistant == ""
            continued = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(turn="next", request="next"),
                json=body(
                    "Tell me more about that GPU",
                    prior=({"role": "assistant", "content": answer},),
                ),
            )
            assert continued.status_code == 200, continued.text
            assert env.cores[0].history[0].assistant == answer
            assert env.cores[0].history[0].complete
            assert env.cores[0].history[0].dependencies
            assert env.cores[1].history[0].assistant == ""
            for request in env.model.requests:
                content = "\n".join(message.content for message in request.messages)
                assert "SHARED_RULES_SENTINEL" in content and "PERSONALITY_SENTINEL" in content
                assert "RTX 3090" in content and "UNTRUSTED_CLIENT_PERSONA" not in content
                assert request.workload == "P0" and not request.tools
                assert len(content.encode()) < 24576
            assert "SECOND_CHAT" not in str(env.model.requests[-1].messages)

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
def test_suppressed_shared_memory_blocks_exact_cached_buffered_and_sse_retries(
    tmp_path: Path, stream: bool
) -> None:
    async def run() -> None:
        async with application(tmp_path, private=True, model=MemoryModel()) as env:
            identity = seed_gpu(env)
            payload = {**body("What is my primary GPU?"), "stream": stream}
            first = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=payload
            )
            assert first.status_code == 200 and "PRIVATE_GPU_REPLY_RTX3090" in first.text
            await forget_gpu(env, identity)
            repeated = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=payload
            )
            assert repeated.status_code == 400
            assert repeated.json()["error"]["code"] == "context_invalidated"
            assert "PRIVATE_GPU_REPLY_RTX3090" not in repeated.text
            assert len(env.model.requests) == 1
            assert env.cores[0].pending_delivery is None
            assert not env.cores[0].history[0].assistant
            fresh = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(turn="next", request="next"),
                json=body(
                    "What is my primary GPU now?",
                    prior=({"role": "assistant", "content": "PRIVATE_GPU_REPLY_RTX3090"},),
                ),
            )
            assert fresh.status_code == 200, fresh.text
            assert "PRIVATE_GPU_REPLY_RTX3090" not in fresh.text
            assert "RTX 3090" not in str(env.model.requests[-1].messages)

    asyncio.run(run())


def test_private_memory_is_rechecked_before_backend_delivery_acknowledgement(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        async with application(tmp_path, private=True, model=MemoryModel()) as env:
            identity = seed_gpu(env)
            first = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(),
                json=body("What is my primary GPU?"),
            )
            answer = first.json()["choices"][0]["message"]["content"]
            await forget_gpu(env, identity)
            next_body = body(
                "Tell me more",
                prior=({"role": "assistant", "content": answer},),
            )
            denied = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(turn="next", request="next"),
                json=next_body,
            )
            assert denied.status_code == 400 and "context_invalidated" in denied.text
            assert env.cores[0].pending_delivery is None
            assert not env.cores[0].history[0].assistant and len(env.model.requests) == 1
            retried = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(turn="next", request="next"),
                json=next_body,
            )
            assert retried.status_code == 200, retried.text
            assert "RTX 3090" not in str(env.model.requests[-1].messages)

    asyncio.run(run())


def test_private_revocation_drains_waiting_model_and_p0_reservation(tmp_path: Path) -> None:
    async def run() -> None:
        calls = []

        async def reserve(context: UUID, connected: bool) -> None:
            calls.append(connected)

        model = MemoryModel()
        model.release.clear()
        async with application(tmp_path, private=True, model=model, reserve=reserve) as env:
            identity = seed_gpu(env)
            pending = asyncio.create_task(
                env.http.post(
                    "/hermes/v1/chat/completions",
                    headers=headers(),
                    json=body("What is my primary GPU?"),
                )
            )
            async with asyncio.timeout(3):
                while not model.requests:
                    await asyncio.sleep(0.005)
            await forget_gpu(env, identity)
            denied = await asyncio.wait_for(pending, 3)
            assert denied.status_code == 400 and "context_invalidated" in denied.text
            assert "PRIVATE_GPU_REPLY_RTX3090" not in denied.text
            assert calls == [True, False] and len(model.requests) == 1
            assert not env.cores[0].busy and env.cores[0].pending_delivery is None
            assert not env.cores[0].history[0].assistant

    asyncio.run(run())


@pytest.mark.parametrize("before_first", [False, True])
def test_private_sse_checks_late_subscriber_and_each_publication(
    tmp_path: Path, before_first: bool
) -> None:
    class GatedFinish(MemoryModel):
        def __init__(self) -> None:
            super().__init__()
            self.finish = asyncio.Event()

        async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent]:
            async for event in super().stream(request):
                if event.kind == "end":
                    await self.finish.wait()
                yield event

    async def run() -> None:
        model = MemoryModel() if before_first else GatedFinish()
        async with application(tmp_path, private=True, model=model) as env:
            identity = seed_gpu(env)
            payload = {
                **body("What is my primary GPU?"),
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            frames = []
            sent = False
            suppressed = False

            async def receive() -> dict[str, object]:
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": json.dumps(payload).encode()}
                await asyncio.Event().wait()
                return {"type": "http.disconnect"}

            async def send(value: dict[str, object]) -> None:
                nonlocal suppressed
                frames.append(value)
                if before_first and value["type"] == "http.response.start":
                    async with asyncio.timeout(3):
                        while env.cores[0].pending_delivery is None:
                            await asyncio.sleep(0.005)
                    await forget_gpu(env, identity)
                    suppressed = True
                elif not before_first and b"PRIVATE_GPU_REPLY_RTX3090" in value.get("body", b""):
                    await forget_gpu(env, identity)
                    suppressed = True
                    assert isinstance(model, GatedFinish)
                    model.finish.set()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/hermes/v1/chat/completions",
                "raw_path": b"/hermes/v1/chat/completions",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (key.lower().encode(), value.encode()) for key, value in headers().items()
                ],
                "client": ("127.0.0.1", 1),
                "server": ("127.0.0.1", 80),
            }
            await asyncio.wait_for(env.app(scope, receive, send), 3)
            output = b"".join(frame.get("body", b"") for frame in frames)
            assert suppressed and b"context_invalidated" in output
            assert output.count(b"PRIVATE_GPU_REPLY_RTX3090") == (0 if before_first else 1)
            assert b'"finish_reason": "stop"' not in output
            assert b'"completion_tokens"' not in output and output.endswith(b"data: [DONE]\n\n")
            assert len(model.requests) == 1 and env.cores[0].pending_delivery is None
            assert not env.cores[0].history[0].assistant

    asyncio.run(run())


@pytest.mark.parametrize("private", [False, True])
def test_factory_recall_policy_must_match_selected_mode(tmp_path: Path, private: bool) -> None:
    async def run() -> None:
        async with application(tmp_path, private=private) as env:
            env.manager.private_recall = not private
            denied = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=body()
            )
            assert denied.status_code == 400
            assert denied.json()["error"]["code"] == "hermes_chat_factory_policy_mismatch"
            assert not env.model.requests and not env.cores[0].history

    asyncio.run(run())


def test_persona_single_generation_and_backend_echo_owns_native_history(tmp_path: Path) -> None:
    async def run() -> None:
        async with application(tmp_path) as env:
            env.memory.retrieve_context = AsyncMock(side_effect=AssertionError("No private recall"))
            first_body = body("Remember this original first message.")
            first = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=first_body
            )
            assert first.status_code == 200, first.text
            answer = first.json()["choices"][0]["message"]["content"]
            assert len(env.model.requests) == 1
            sent = env.model.requests[0]
            assert sent.model == "pinned-model" and sent.workload == "P0" and not sent.tools
            assert "SHARED_RULES_SENTINEL" in sent.messages[0].content
            assert "PERSONALITY_SENTINEL" in sent.messages[0].content
            assert "UNTRUSTED_CLIENT_PERSONA" not in str(sent.messages)
            assert env.cores[0].history[0].assistant == ""
            repeated = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=first_body
            )
            assert repeated.json() == first.json() and len(env.model.requests) == 1
            second = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(turn="turn2", request="request2"),
                json=body(
                    "What did I say first?",
                    prior=(
                        {"role": "user", "content": "FORGED_OLD_CLIENT_HISTORY"},
                        {"role": "assistant", "content": answer},
                    ),
                ),
            )
            assert second.status_code == 200, second.text
            assert env.cores[0].history[0].assistant == answer
            assert env.cores[0].history[0].complete
            content = "\n".join(message.content for message in env.model.requests[1].messages)
            assert "Remember this original first message." in content
            assert "FORGED_OLD_CLIENT_HISTORY" not in content
            env.memory.retrieve_context.assert_not_awaited()

    asyncio.run(run())


def test_explicit_search_is_host_preflight_before_any_inference_and_single_prepare(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        async with application(tmp_path) as env:
            initial = body("Search the web for public SearXNG documentation", tools=(TOOL,))
            result = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=initial
            )
            assert result.status_code == 200, result.text
            message = result.json()["choices"][0]["message"]
            call = message["tool_calls"][0]
            assert json.loads(call["function"]["arguments"]) == {
                "query": "public SearXNG documentation",
                "limit": 3,
            }
            assert result.json()["usage"]["total_tokens"] == 0 and not env.model.requests
            assert result.json()["system_fingerprint"] == "ember-public-search-preflight"
            assert len(env.cores[0].history) == 1
            continuation = {
                **initial,
                "messages": [
                    *initial["messages"],
                    message,
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(
                            {
                                "success": True,
                                "data": {
                                    "web": [
                                        {
                                            "url": "http://127.1/private",
                                            "title": "FORBIDDEN_LOCAL",
                                            "description": "x",
                                        },
                                        {
                                            "url": "https://docs.searxng.org/",
                                            "title": "Public docs",
                                            "description": "Public finding",
                                        },
                                    ]
                                },
                            }
                        ),
                    },
                ],
            }
            # SDK serializers may change JSON whitespace/order without changing an issued action.
            continuation["messages"][-2]["tool_calls"][0]["function"]["arguments"] = (
                '{"limit":3,"query":"public SearXNG documentation"}'
            )
            final = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(request="request2"),
                json=continuation,
            )
            assert final.status_code == 200, final.text
            assert len(env.model.requests) == 1 and not env.model.requests[0].tools
            contents = "\n".join(message.content for message in env.model.requests[0].messages)
            assert "Public finding" in contents and "FORBIDDEN_LOCAL" not in contents
            assert len(env.cores[0].history) == 1
            replay = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(request="request2"),
                json=continuation,
            )
            assert replay.json() == final.json() and len(env.model.requests) == 1
            forged = json.loads(json.dumps(continuation))
            forged["messages"][-1]["tool_call_id"] = str(uuid4())
            invalid = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(request="request3"), json=forged
            )
            assert invalid.status_code == 400 and len(env.model.requests) == 1

    asyncio.run(run())


def test_separate_contexts_tombstones_and_authority_failures(tmp_path: Path) -> None:
    async def run() -> None:
        async with application(tmp_path) as env:
            assert (await env.http.get("/hermes/v1/models")).status_code == 401
            assert (
                await env.http.post(
                    "/hermes/v1/chat/completions",
                    headers={"Authorization": "Bearer " + TOKEN},
                    json=body(),
                )
            ).status_code == 400
            for index in range(4):
                response = await env.http.post(
                    "/hermes/v1/chat/completions",
                    headers=headers(alias=f"chat{index}"),
                    json=body(f"Question {index}"),
                )
                assert response.status_code == 200
            assert len({core.context_id for core in env.cores}) == 4
            limited = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(alias="fifth"), json=body()
            )
            assert limited.status_code == 400 and len(env.cores) == 4
            deleted = await env.http.delete(
                "/hermes/v1/sessions/chat0", headers=headers(alias="chat0")
            )
            assert deleted.json() == {"deleted": True}
            dead = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(alias="chat0"), json=body()
            )
            assert dead.status_code == 410
            replacement = HermesChats(
                env.factory,
                env.model,
                client_token=TOKEN,
                model_context_tokens=131072,
                state_root=tmp_path / "metadata",
            )
            app = FastAPI()
            app.include_router(replacement.router)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1)),
                base_url="http://127.0.0.1",
            ) as http:
                response = await http.post(
                    "/hermes/v1/chat/completions", headers=headers(alias="chat1"), json=body()
                )
                assert response.status_code == 410
            await replacement.aclose()
            assert "Question" not in (tmp_path / "metadata/chat-identities.json").read_text()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["model", "extra", "tool", "schema", "temperature", "max_tokens", "reasoning_effort"]
)
def test_malformed_metadata_is_rejected_before_observation(tmp_path: Path, fault: str) -> None:
    async def run() -> None:
        async with application(tmp_path) as env:
            value = body()
            if fault == "model":
                value["model"] = "other"
            elif fault == "extra":
                value["workload"] = "P0"
            elif fault == "tool":
                value["tools"] = [
                    {
                        "type": "function",
                        "function": {"name": "shell", "parameters": {"type": "object"}},
                    }
                ]
            elif fault == "schema":
                value["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "parameters": {"type": "object", "pattern": "^(a+)+$"},
                        },
                    }
                ]
            elif fault == "temperature":
                value["temperature"] = 0.9
            elif fault == "reasoning_effort":
                value["reasoning_effort"] = "medium"
            else:
                value["max_tokens"] = 1192
            response = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=value
            )
            assert response.status_code == 400 and not env.cores and not env.model.requests

    asyncio.run(run())


def test_native_no_reasoning_profile_is_supported_without_enabling_thinking(tmp_path: Path) -> None:
    async def run() -> None:
        async with application(tmp_path) as env:
            response = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(),
                json={**body(), "reasoning_effort": "none"},
            )
            assert response.status_code == 200
            assert len(env.model.requests) == 1 and not env.model.requests[0].thinking

    asyncio.run(run())


def test_cancellation_drains_reservation_and_never_commits_unreceived_output(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        reservations = []

        async def reserve(context: UUID, connected: bool) -> None:
            reservations.append((context, connected))

        model = FakeModel()
        model.release.clear()
        async with application(tmp_path, model=model, reserve=reserve) as env:
            request = asyncio.create_task(
                env.http.post("/hermes/v1/chat/completions", headers=headers(), json=body())
            )
            async with asyncio.timeout(3):
                while not model.requests:
                    await asyncio.sleep(0.01)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert reservations == [
                (env.cores[0].context_id, True),
                (env.cores[0].context_id, False),
            ]
            assert env.cores[0].pending_delivery is None
            assert env.cores[0].history[0].assistant == ""

    asyncio.run(run())


def test_incremental_sse_precedes_model_completion_and_replays_known_reply(tmp_path: Path) -> None:
    class Incremental(FakeModel):
        def __init__(self) -> None:
            super().__init__()
            self.end = asyncio.Event()

        async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent]:
            self.requests.append(request)
            yield GatewayEvent(
                request_id=request.request_id,
                context_id=request.context_id,
                sequence=0,
                kind="start",
            )
            yield GatewayEvent(
                request_id=request.request_id,
                context_id=request.context_id,
                sequence=1,
                kind="text",
                text="EARLY_DELTA",
            )
            await self.end.wait()
            yield GatewayEvent(
                request_id=request.request_id,
                context_id=request.context_id,
                sequence=2,
                kind="end",
                usage=GenerationUsage(
                    prompt_tokens=1,
                    output_tokens=1,
                    total_seconds=0.1,
                    first_visible_seconds=0.01,
                    tokens_per_second=10,
                ),
            )

    async def run() -> None:
        model = Incremental()
        async with application(tmp_path, model=model) as env:
            payload = {**body(), "stream": True, "stream_options": {"include_usage": True}}
            received = asyncio.Event()
            frames = []

            async def receive() -> dict[str, object]:
                await asyncio.Event().wait()
                return {"type": "http.disconnect"}

            sent = False

            async def input_event() -> dict[str, object]:
                nonlocal sent
                if not sent:
                    sent = True
                    return {
                        "type": "http.request",
                        "body": json.dumps(payload).encode(),
                        "more_body": False,
                    }
                return await receive()

            async def send(value: dict[str, object]) -> None:
                frames.append(value)
                if b"EARLY_DELTA" in value.get("body", b""):
                    received.set()

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/hermes/v1/chat/completions",
                "raw_path": b"/hermes/v1/chat/completions",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (key.lower().encode(), value.encode()) for key, value in headers().items()
                ],
                "client": ("127.0.0.1", 1),
                "server": ("127.0.0.1", 80),
            }
            task = asyncio.create_task(env.app(scope, input_event, send))
            await asyncio.wait_for(received.wait(), 3)
            assert not task.done() and len(model.requests) == 1
            model.end.set()
            await task
            output = b"".join(frame.get("body", b"") for frame in frames)
            assert b'"finish_reason": "stop"' in output and b'"completion_tokens": 1' in output
            repeated = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=payload
            )
            assert repeated.content == output and len(model.requests) == 1

    asyncio.run(run())


def test_nonexplicit_search_is_never_inferred_from_memory_question() -> None:
    assert explicit_public_query("What do you remember about my search history?") is None
    assert explicit_public_query("please look up public documentation") == "public documentation"


@pytest.mark.parametrize("failure", ["acquire", "release"])
def test_reservation_failure_is_observed_without_hanging_or_committing_reply(
    tmp_path: Path, failure: str
) -> None:
    async def run() -> None:
        calls = []

        async def reserve(context: UUID, connected: bool) -> None:
            calls.append(connected)
            if connected == (failure == "acquire"):
                raise GatewayError("synthetic_reservation_failure")

        async with application(tmp_path, reserve=reserve) as env:
            response = await asyncio.wait_for(
                env.http.post("/hermes/v1/chat/completions", headers=headers(), json=body()), 3
            )
            assert response.status_code == 400
            assert calls == [True, False]
            assert env.cores[0].pending_delivery is None
            assert not env.cores[0].history[0].assistant
            assert len(env.model.requests) == (failure == "release")

    asyncio.run(run())


def test_duplicate_inflight_request_attaches_once_and_missing_receipt_cannot_advance(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        model = FakeModel()
        model.release.clear()
        async with application(tmp_path, model=model) as env:

            async def ask() -> httpx.Response:
                return await env.http.post(
                    "/hermes/v1/chat/completions", headers=headers(), json=body()
                )

            first, second = asyncio.create_task(ask()), asyncio.create_task(ask())
            async with asyncio.timeout(3):
                while not model.requests:
                    await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)
            assert len(model.requests) == 1
            model.release.set()
            left, right = await asyncio.gather(first, second)
            assert left.json() == right.json()
            failed = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(turn="next", request="next"),
                json=body("Next question"),
            )
            assert failed.status_code == 400 and len(model.requests) == 1
            assert env.cores[0].pending_delivery is not None
            assert not env.cores[0].history[0].assistant

    asyncio.run(run())


def test_expired_search_cannot_silently_restart_or_generate(tmp_path: Path) -> None:
    async def run() -> None:
        async with application(tmp_path, timeout=0.02) as env:
            response = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(),
                json=body("Search public documents", tools=(TOOL,)),
            )
            assert response.status_code == 200 and not env.model.requests
            await asyncio.sleep(0.05)
            expired = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=body()
            )
            assert expired.status_code == 410
            assert "new Hermes chat" in expired.json()["error"]["message"]
            assert not env.model.requests

    asyncio.run(run())


def test_stream_failure_has_no_false_finish_and_no_assistant_capture(tmp_path: Path) -> None:
    class Failed(FakeModel):
        async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent]:
            self.requests.append(request)
            yield GatewayEvent(
                request_id=request.request_id,
                context_id=request.context_id,
                sequence=0,
                kind="start",
            )
            yield GatewayEvent(
                request_id=request.request_id,
                context_id=request.context_id,
                sequence=1,
                kind="error",
                code="synthetic_failure",
            )

    async def run() -> None:
        async with application(tmp_path, model=Failed()) as env:
            response = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json={**body(), "stream": True}
            )
            assert "synthetic_failure" in response.text
            assert '"finish_reason": "stop"' not in response.text
            assert not env.cores[0].history[0].assistant

    asyncio.run(run())


def test_failed_metadata_delete_still_drains_the_owned_model(tmp_path: Path) -> None:
    async def run() -> None:
        model = FakeModel()
        model.release.clear()
        async with application(tmp_path, model=model) as env:
            request = asyncio.create_task(
                env.http.post("/hermes/v1/chat/completions", headers=headers(), json=body())
            )
            async with asyncio.timeout(3):
                while not model.requests:
                    await asyncio.sleep(0.005)

            def failed_state(identities: tuple[str, ...]) -> None:
                raise OSError("synthetic unavailable storage")

            env.manager._persist = failed_state
            deleted = await env.http.delete("/hermes/v1/sessions/chat1", headers=headers())
            assert deleted.status_code == 400
            assert deleted.json()["error"]["code"] == "hermes_chat_state_unavailable"
            response = await asyncio.wait_for(request, 3)
            assert response.status_code == 400
            assert not env.cores[0].busy and env.cores[0].pending_delivery is None
            assert not env.cores[0].history[0].assistant

    asyncio.run(run())


def test_cancelled_turn_can_continue_without_losing_its_original_input(tmp_path: Path) -> None:
    async def run() -> None:
        model = FakeModel()
        model.release.clear()
        async with application(tmp_path, model=model) as env:
            request = asyncio.create_task(
                env.http.post(
                    "/hermes/v1/chat/completions", headers=headers(), json=body("Original input")
                )
            )
            async with asyncio.timeout(3):
                while not model.requests:
                    await asyncio.sleep(0.005)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            model.release.set()
            next_turn = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(turn="next", request="next"),
                json=body("Next input"),
            )
            assert next_turn.status_code == 200, next_turn.text
            assert [turn.user for turn in env.cores[0].history] == ["Original input", "Next input"]
            assert not env.cores[0].history[0].assistant

    asyncio.run(run())


def test_stale_response_cancellation_cannot_interrupt_the_next_turn(tmp_path: Path) -> None:
    async def run() -> None:
        model = FakeModel()
        async with application(tmp_path, model=model) as env:
            first = await env.http.post(
                "/hermes/v1/chat/completions", headers=headers(), json=body()
            )
            answer = first.json()["choices"][0]["message"]["content"]
            chat = next(iter(env.manager.chats.values()))
            old = chat.turn
            model.release.clear()
            newer = asyncio.create_task(
                env.http.post(
                    "/hermes/v1/chat/completions",
                    headers=headers(turn="next", request="next"),
                    json=body(
                        "Next input",
                        prior=(
                            {"role": "user", "content": "Hello"},
                            {"role": "assistant", "content": answer},
                        ),
                    ),
                )
            )
            async with asyncio.timeout(3):
                while len(model.requests) < 2:
                    await asyncio.sleep(0.005)
            await env.manager._cancel(chat, target=old)
            assert not newer.done() and not chat.turn.cancelled
            model.release.set()
            assert (await newer).status_code == 200

    asyncio.run(run())


def test_new_user_can_abandon_a_public_preflight_without_starting_its_model(tmp_path: Path) -> None:
    async def run() -> None:
        async with application(tmp_path) as env:
            first = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(),
                json=body("Search public documents", tools=(TOOL,)),
            )
            assert first.status_code == 200 and not env.model.requests
            next_turn = await env.http.post(
                "/hermes/v1/chat/completions",
                headers=headers(turn="next", request="next"),
                json=body("Different question"),
            )
            assert next_turn.status_code == 200 and len(env.model.requests) == 1
            assert [turn.user for turn in env.cores[0].history] == [
                "Search public documents",
                "Different question",
            ]

    asyncio.run(run())
