"""Exercise public Research via pinned OpenCode and scripted P2/retrieval backends."""

import asyncio
import json
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime
from hashlib import file_digest
from pathlib import Path
from time import time
from uuid import UUID, uuid4

import httpx
import pytest
from anyio import CancelScope

from ember_agent.inference.contracts import GatewayError, GatewayRequest, ToolCall
from ember_agent.inference.upstream import NativeFrame, NativeFunction, NativeMessage, NativeTool
from ember_agent.runtime.config import HarnessSettings
from ember_agent.runtime.research import PAGE, SEARCH, ResearchBroker, open_research
from ember_agent.runtime.task_router import (
    TaskResult,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolContext,
    ToolRouter,
)
from ember_agent.runtime.task_store import TaskConflict, TaskState, TaskSubmission
from ember_agent.web_evidence import WebEvidence, WebSource

from .test_openai_bridge import _bridge, _call, _config, _Upstream
from .test_opencode_harness import _terminal


def test_broker_requires_auth_session_one_use_permit_and_revoke() -> None:
    calls: list[str] = []
    started = asyncio.Event()
    settled = asyncio.Event()

    class Retrieval:
        async def search(self, query: str) -> WebEvidence:
            calls.append(query)
            if query == "cancel me":
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    settled.set()
            return WebEvidence(
                provider="exa", operation="search", retrieved_at=datetime.now(UTC), coverage="empty"
            )

        async def read(self, url: str) -> WebEvidence:
            pytest.fail(f"Unexpected page call: {url}")

    async def run() -> None:
        broker = ResearchBroker(Retrieval(), "exa")
        broker.begin(
            "active",
            time() + 15,
            "Inline https://example.org/unlisted\nhttps://example.org/allowed\n"
            "Search: public fixture\nSearch: cancel me\nSearch: expire me",
        )

        def tool(name: str, **arguments: str) -> ToolCall:
            return ToolCall(call_id=uuid4(), name=name, arguments=arguments)

        entered = tool(SEARCH, query="public fixture")
        for bad in (
            tool(SEARCH, query="rewritten public fixture"),
            tool(SEARCH, query="https://example.org"),
            tool(SEARCH, query="public fixture", ember_permit="forged"),
            tool(PAGE, url="https://example.org/unlisted"),
        ):
            with pytest.raises(GatewayError, match="research_input_denied"):
                broker.grant((entered, bad))
            assert not broker.pending and not broker.used
        with pytest.raises(GatewayError, match="research_tool_budget"):
            broker.grant((entered, entered))
        assert not broker.pending and not broker.used
        (approved,) = broker.grant((entered,))
        nonce = approved.arguments["ember_permit"]
        assert isinstance(nonce, str) and len(nonce) == 32
        key = next(iter(broker.environment().values()))
        async with (
            broker.app.router.lifespan_context(broker.app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=broker.authenticate),
                base_url="http://127.0.0.1:1234",
            ) as http,
        ):

            async def call(
                query: str,
                session: str,
                permit: str,
                *,
                authorized: bool = True,
                extra: bool = False,
            ) -> httpx.Response:
                headers = {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-11-25",
                }
                if authorized:
                    headers["Authorization"] = f"Bearer {key}"
                return await http.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": uuid4().hex,
                        "method": "tools/call",
                        "params": {
                            "name": "search",
                            "arguments": {
                                "query": query,
                                "ember_permit": permit,
                                **({"extra": True} if extra else {}),
                            },
                            "_meta": {"ai.opencode/sessionID": session},
                        },
                    },
                )

            assert (
                await call("public fixture", "active", nonce, authorized=False)
            ).status_code == 401
            for query, session, permit, extra in (
                ("public fixture", "stale", nonce, False),
                ("wrong query", "active", nonce, False),
                ("public fixture", "active", "0" * 32, False),
                ("public fixture", "active", nonce, True),
            ):
                assert (await call(query, session, permit, extra=extra)).json()["result"]["isError"]
            assert calls == []
            assert not (await call("public fixture", "active", nonce)).json()["result"]["isError"]
            assert (await call("public fixture", "active", nonce)).json()["result"]["isError"]
            (slow, expires) = broker.grant(
                (tool(SEARCH, query="cancel me"), tool(SEARCH, query="expire me"))
            )
            slow_nonce = str(slow.arguments["ember_permit"])
            expires_nonce = str(expires.arguments["ember_permit"])
            deadline = broker.deadline
            broker.deadline = time() - 1
            assert (await call("expire me", "active", expires_nonce)).json()["result"]["isError"]
            broker.deadline = deadline
            work = asyncio.create_task(call("cancel me", "active", slow_nonce))
            async with asyncio.timeout(5):
                await started.wait()
                broker.fence()
                assert (await call("expire me", "active", expires_nonce)).json()["result"][
                    "isError"
                ]
                await broker.drain()
                await work
            assert settled.is_set()
            assert calls == ["public fixture", "cancel me"]

    asyncio.run(run())


def test_research_receipt_identifies_each_retrieval_at_full_call_budget() -> None:
    path = "/" + "a/" * 243
    urls = tuple(f"https://example.org{path}{suffix}" for suffix in ("xyzabc", "uvwxyz", "abcdef"))
    # Three searches can return the same three URLs, and three reads can revisit them.
    queries = ("first", "second", "third")
    search_dates = tuple(datetime(2026, 1, day, tzinfo=UTC) for day in (1, 2, 3))
    read_date = datetime(2026, 2, 1, tzinfo=UTC)

    class Retrieval:
        async def search(self, query: str) -> WebEvidence:
            return WebEvidence(
                provider="exa",
                operation="search",
                retrieved_at=search_dates[queries.index(query)],
                coverage="partial",
                sources=tuple(
                    WebSource(url=url, title="📖" * 160, passages=(f"Excerpt from {query}",))
                    for url in urls
                ),
            )

        async def read(self, url: str) -> WebEvidence:
            return WebEvidence(
                provider="exa",
                operation="read",
                retrieved_at=read_date,
                coverage="partial",
                sources=(
                    WebSource(url=url, title="Page", passages=("Clipped page " + "x" * 1200,)),
                ),
            )

    async def run() -> None:
        assert all(len(url) == 512 for url in urls)
        broker = ResearchBroker(Retrieval(), "exa")
        broker.begin("receipt", time() + 15, "\n".join(f"Search: {query}" for query in queries))
        key = next(iter(broker.environment().values()))
        async with (
            broker.app.router.lifespan_context(broker.app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=broker.authenticate),
                base_url="http://127.0.0.1:1234",
            ) as http,
        ):

            async def call(name: str, **arguments: str) -> list[dict[str, object]]:
                (approved,) = broker.grant(
                    (ToolCall(call_id=uuid4(), name=name, arguments=arguments),)
                )
                response = await http.post(
                    "/mcp",
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Authorization": f"Bearer {key}",
                        "MCP-Protocol-Version": "2025-11-25",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": uuid4().hex,
                        "method": "tools/call",
                        "params": {
                            "name": name.removeprefix("emberweb_"),
                            "arguments": approved.arguments,
                            "_meta": {"ai.opencode/sessionID": "receipt"},
                        },
                    },
                )
                result = response.json()["result"]
                assert not result["isError"]
                return json.loads(result["content"][0]["text"])["sources"]

            shown = [row for query in queries for row in await call(SEARCH, query=query)]
            for url in urls:
                shown.extend(await call(PAGE, url=url))

        expected_ids = [f"S{number}" for number in range(1, 13)]
        assert [row["id"] for row in shown] == expected_ids
        assert [row["excerpt"] for row in shown[:9]] == [
            f"Excerpt from {query}" for query in queries for _ in urls
        ]
        assert all(str(row["excerpt"]).startswith("Clipped page ") for row in shown[9:])

        visible, receipt = broker.result("Searches [S1] and [S4]; page [S10].")
        assert "Sources cited: [S1], [S4], [S10]." in visible
        expected_dates = [date for date in (*search_dates, read_date) for _ in urls]
        assert receipt == [
            {
                "id": row["id"],
                "url": row["url"],
                "title": row["title"],
                "provider": "exa",
                "operation": "search" if index < 9 else "read",
                "retrieved_at": expected_dates[index].isoformat(),
                "truncated": index >= 9,
            }
            for index, row in enumerate(shown)
        ]
        assert [row["url"] for row in shown] == list(urls) * 4
        with pytest.raises(TaskConflict, match="research_invalid_citations"):
            broker.result("Unknown [S13].")
        broker.fence()
        await broker.drain()

    asyncio.run(run())


def test_research_consent_survives_restart_without_switching_providers(tmp_path: Path) -> None:
    async def run() -> None:
        identity, session = uuid4(), uuid4()
        inspect = TextInspectionTask(ToolRouter(TextInspectionProvider(), identity))
        router = TaskRouter(tmp_path, identity, session, inspect)
        await router.store.initialize()
        request = TaskSubmission(
            character_id=identity,
            session_id=session,
            turn_id=uuid4(),
            idempotency_key="public-fixture",
            goal="Search: public fixture",
            operation="research",
            research_provider="exa",
        )
        queued = await router.store.submit(request)
        assert await router.store.claim(identity, realtime_reserved=True) is None
        assert (await router.store.active(identity))[0].state == "waiting_for_realtime"
        with pytest.raises(TaskConflict, match="idempotency_key_reused"):
            await router.store.submit(request.model_copy(update={"research_provider": "parallel"}))
        legacy = await router.store.submit(
            request.model_copy(update={"idempotency_key": "legacy", "research_provider": None})
        )

        class UnexpectedProvider:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                pytest.fail("Unapproved provider must not start")

        restarted = TaskRouter(tmp_path, identity, session, inspect)
        restarted.providers["research"] = UnexpectedProvider()
        restarted.research_provider = "parallel"
        await restarted.start()
        try:
            for task in (queued, legacy):
                terminal = await _terminal(restarted, task.task_id)
                assert terminal.state == "failed"
                assert terminal.result == "research_provider_consent_required"
            for provider in (None, "exa"):
                with pytest.raises(TaskConflict, match="research_provider_consent_required"):
                    await restarted.submit(
                        "fresh", request.goal, operation="research", research_provider=provider
                    )
        finally:
            await restarted.aclose()

    asyncio.run(run())


def test_research_cancel_fences_issued_permit_before_db_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        identity = uuid4()
        router = TaskRouter(
            tmp_path,
            identity,
            uuid4(),
            TextInspectionTask(ToolRouter(TextInspectionProvider(), identity)),
        )

        class NoRetrieval:
            async def search(self, query: str) -> WebEvidence:
                pytest.fail("Cancelled permit must not reach retrieval")

            async def read(self, url: str) -> WebEvidence:
                pytest.fail("Unexpected page request")

        broker = ResearchBroker(NoRetrieval(), "exa")
        query = "public fixture"
        permit = ""

        class Provider:
            async def execute(
                self,
                request: TaskSubmission,
                context: ToolContext,
                progress: Callable[[TaskState], Awaitable[None]],
            ) -> TaskResult:
                nonlocal permit
                assert context.deadline_at is not None
                broker.begin("fixture", context.deadline_at, request.goal)
                (call,) = broker.grant(
                    (ToolCall(call_id=uuid4(), name=SEARCH, arguments={"query": query}),)
                )
                permit = str(call.arguments["ember_permit"])
                entered.set()
                # Pause before worker cleanup so the broker has to check owner cancellation.
                try:
                    await asyncio.Future()
                finally:
                    await release.wait()
                    broker.fence()
                pytest.fail("Research should have been cancelled")

        store_cancel = router.store.cancel

        async def blocked_cancel(task_id: UUID, owner: UUID) -> None:
            cancelling.set()
            await release.wait()
            await store_cancel(task_id, owner)

        monkeypatch.setattr(router.store, "cancel", blocked_cancel)
        router.providers["research"] = Provider()
        router.research_provider = "exa"
        await router.start()
        cancel: asyncio.Task[None] | None = None
        try:
            async with (
                asyncio.timeout(5),
                broker.app.router.lifespan_context(broker.app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=broker.authenticate),
                    base_url="http://127.0.0.1:1234",
                ) as http,
            ):
                task = await router.submit(
                    "race", f"Search: {query}", operation="research", research_provider="exa"
                )
                await entered.wait()
                assert permit in broker.pending
                cancel = asyncio.create_task(router.cancel(task.task_id))
                await cancelling.wait()
                assert not cancel.done() and permit in broker.pending
                key = next(iter(broker.environment().values()))
                response = await http.post(
                    "/mcp",
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Authorization": f"Bearer {key}",
                        "MCP-Protocol-Version": "2025-11-25",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": "cancel-race",
                        "method": "tools/call",
                        "params": {
                            "name": "search",
                            "arguments": {"query": query, "ember_permit": permit},
                            "_meta": {"ai.opencode/sessionID": "fixture"},
                        },
                    },
                )
                assert response.json()["result"]["isError"]
                assert permit in broker.pending
                release.set()
                await cancel
                assert (await _terminal(router, task.task_id)).state == "cancelled"
        finally:
            release.set()
            if cancel is not None:
                await asyncio.gather(cancel, return_exceptions=True)
            await router.aclose()

    asyncio.run(run())


@pytest.mark.integration
def test_pinned_research_loop(tmp_path: Path) -> None:
    binary = os.environ.get("EMBER_TEST_OPENCODE")
    if not binary:
        pytest.skip("EMBER_TEST_OPENCODE must point to installed OpenCode 2.3.2")
    executable = Path(binary).resolve(strict=True)
    with executable.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()

    source = WebSource(
        url="https://example.org/reference", title="Fixture", passages=("pinned evidence",)
    )

    started = asyncio.Event()
    stopping = asyncio.Event()
    release = asyncio.Event()
    stopped = asyncio.Event()
    good_goal = "Find public fixture evidence\nSearch: public fixture"
    cancel_goal = "Cancel public fixture\nSearch: slow fixture"
    rewritten_goal = "Try rewriting a search query\nSearch: public fixture"
    mixed_goal = "Try a mixed batch\nSearch: public fixture"
    invented_goal = "Test source citation checking\nSearch: public fixture"
    retrieval_calls: list[tuple[str, str]] = []

    class Retrieval:
        async def search(self, query: str) -> WebEvidence:
            retrieval_calls.append(("search", query))
            if query == "slow fixture":
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopping.set()
                    with CancelScope(shield=True):
                        await release.wait()
                    stopped.set()
            assert query == "public fixture"
            return WebEvidence(
                provider="exa",
                operation="search",
                retrieved_at=datetime.now(UTC),
                coverage="partial",
                sources=(
                    source.model_copy(update={"passages": ("pinned evidence " + '"' * 800,)}),
                ),
            )

        async def read(self, url: str) -> WebEvidence:
            retrieval_calls.append(("read", url))
            assert url == source.url
            return WebEvidence(
                provider="exa",
                operation="read",
                retrieved_at=datetime.now(UTC),
                coverage="partial",
                sources=(source,),
            )

    class Upstream(_Upstream):
        async def generate(self, request: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
            self.requests.append(request)
            user = [item.content for item in request.messages if item.role == "user"]
            assert request.workload == "P2"
            assert {tool.function.name for tool in request.tools} == {SEARCH, PAGE}
            assert all(
                "ember_permit" not in tool.function.parameters.get("properties", {})
                for tool in request.tools
            )
            assert all(
                "ember_permit" not in call.arguments
                for item in request.messages
                for call in item.tool_calls
            )
            if user == [cancel_goal]:
                yield _call(SEARCH, {"query": "slow fixture"})
                yield NativeFrame(done=True, done_reason="tool_calls")
                return
            if user == ["Try an unlisted page"]:
                yield _call(PAGE, {"url": source.url})
                yield NativeFrame(done=True, done_reason="tool_calls")
                return
            if user == ["Try a built-in web fetch"]:
                yield _call("webfetch", {"url": source.url})
                yield NativeFrame(done=True, done_reason="tool_calls")
                return
            if user == ["Answer without evidence"]:
                yield NativeFrame(message=NativeMessage(content="Invented finding."))
                yield NativeFrame(done=True, done_reason="stop")
                return
            if user == [rewritten_goal]:
                yield _call(SEARCH, {"query": "rewritten public fixture"})
                yield NativeFrame(done=True, done_reason="tool_calls")
                return
            if user == [mixed_goal]:
                yield _call(SEARCH, {"query": "public fixture"})
                yield NativeFrame(
                    message=NativeMessage(
                        tool_calls=[
                            NativeTool(
                                index=1,
                                function=NativeFunction(name=PAGE, arguments={"url": source.url}),
                            )
                        ]
                    )
                )
                yield NativeFrame(done=True, done_reason="tool_calls")
                return
            assert user in ([good_goal], [invented_goal])
            results = [item.content for item in request.messages if item.role == "tool"]
            if not results:
                yield _call(SEARCH, {"query": "public fixture"})
            elif len(results) == 1:
                assert '"id":"S1"' in results[0] and "pinned evidence" in results[0]
                yield _call(PAGE, {"url": source.url})
            else:
                assert all("pinned evidence" in item for item in results)
                assert '"id":"S2"' in results[1]
                citation = "[S99]" if user == [invented_goal] else "[S1] [S2]"
                yield NativeFrame(message=NativeMessage(content=f"Public finding {citation}."))
            yield NativeFrame(
                done=True,
                done_reason="tool_calls" if len(results) < 2 else "stop",
                prompt_eval_count=300,
                eval_count=10,
            )

    async def run() -> None:
        config = _config(tmp_path).model_copy(
            update={
                "context_tokens": 32768,
                "max_input_tokens": 16384,
                "runtime_root": tmp_path / "runtime",
            }
        )
        config.runtime_root.mkdir(parents=True)
        settings = HarnessSettings(
            provider="opencode",
            command=(str(executable),),
            revision="2.3.2",
            profile_sha256=digest,
            workspace=tmp_path / "never-opened",
            research=True,
        )
        upstream = Upstream()
        _, gateway, model = _bridge(config, upstream)
        identity = uuid4()
        try:
            async with open_research(config, settings, model, Retrieval(), "exa") as worker:
                router = TaskRouter(
                    config.runtime_root,
                    identity,
                    uuid4(),
                    TextInspectionTask(ToolRouter(TextInspectionProvider(), identity)),
                )
                router.providers["research"] = worker
                router.research_provider = "exa"
                router.research_health = lambda: worker.available
                await router.start()
                try:
                    cancelled = await router.submit(
                        "cancel", cancel_goal, operation="research", research_provider="exa"
                    )
                    try:
                        async with asyncio.timeout(8):
                            await started.wait()
                            await router.cancel(cancelled.task_id)
                            await stopping.wait()
                            assert worker.ingress.bridge is None
                            assert worker.research is not None and worker.research.session is None
                            assert not stopped.is_set()
                    finally:
                        release.set()
                    assert (await _terminal(router, cancelled.task_id)).state == "cancelled"
                    assert stopped.is_set()
                    assert worker.available and worker.ingress.bridge is None
                    for goal, reason in (
                        ("Try an unlisted page", "research_input_denied"),
                        ("Try a built-in web fetch", "unrequested_tool"),
                        ("Answer without evidence", "research_no_evidence"),
                        (rewritten_goal, "research_input_denied"),
                        (mixed_goal, "research_input_denied"),
                        (invented_goal, "research_invalid_citations"),
                    ):
                        retrieval_calls.clear()
                        task = await router.submit(
                            goal, goal, operation="research", research_provider="exa"
                        )
                        failed = await _terminal(router, task.task_id)
                        assert failed.state == "failed"
                        assert failed.result is not None and reason in failed.result
                        if goal != invented_goal:
                            assert not retrieval_calls
                        assert worker.available
                    for key in ("first", "second"):
                        task = await router.submit(
                            key, good_goal, operation="research", research_provider="exa"
                        )
                        result = await _terminal(router, task.task_id)
                        assert result.state == "succeeded", result.result
                        assert result.result and "Coverage: excerpts only." in result.result
                        assert "Some excerpts were shortened or omitted." in result.result
                        assert "Sources cited: [S1], [S2]." in result.result
                        receipt = (await router.receipt(task.task_id)).receipt
                        assert receipt is not None and receipt.tool == "research"
                        sources = receipt.data["sources"]
                        assert isinstance(sources, list) and len(sources) == 2
                        for row, identity, operation, truncated in zip(
                            sources, ("S1", "S2"), ("search", "read"), (True, False), strict=True
                        ):
                            assert isinstance(row, dict)
                            assert (row["id"], row["url"], row["provider"], row["operation"]) == (
                                identity,
                                source.url,
                                "exa",
                                operation,
                            )
                            assert row["truncated"] is truncated
                        assert "pinned evidence" not in receipt.model_dump_json()
                        assert [request.context_id for request in upstream.requests[-3:]] == [
                            task.task_id
                        ] * 3
                finally:
                    await router.aclose()
        finally:
            await model.aclose()
            await gateway.aclose()

    asyncio.run(run())
