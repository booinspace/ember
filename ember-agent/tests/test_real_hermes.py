"""Opt-in native Windows delegation through actual Hermes, SearXNG and gateway admission."""

import asyncio
import hashlib
import json
import os
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from pydantic import JsonValue

from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import (
    GatewayError,
    GatewayEvent,
    GatewayRequest,
    GenerationUsage,
    PromptMessage,
)
from ember_agent.runtime.config import load_config
from ember_agent.runtime.hermes_tasks import HermesTask
from ember_agent.runtime.searx_tools import SearXNGTools
from ember_agent.runtime.task_router import (
    TaskResult,
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolContext,
    ToolReceipt,
    ToolRouter,
)
from ember_agent.runtime.task_store import TERMINAL_STATES, TaskConflict, TaskState, TaskSubmission
from ember_memory_engine import CanonicalEngine


class ObservedModels:
    """Observe the real authenticated gateway without substituting model behavior."""

    def __init__(self, client: HttpGatewayClient) -> None:
        self.client = client
        self.requests: list[GatewayRequest] = []
        self.usage: list[GenerationUsage] = []

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent]:
        self.requests.append(request)
        async for event in self.client.stream(request):
            assert event.kind != "thinking"
            if event.usage is not None:
                self.usage.append(event.usage)
            yield event


class ObservedSearch:
    """Observe actual broker receipts while retaining the real search transport."""

    def __init__(self, provider: SearXNGTools) -> None:
        self.provider = provider
        self.receipts: list[ToolReceipt] = []

    @property
    def capabilities(self) -> frozenset[str]:
        return self.provider.capabilities

    async def invoke(
        self, name: str, arguments: dict[str, JsonValue], context: ToolContext
    ) -> ToolReceipt:
        receipt = await self.provider.invoke(name, arguments, context)
        self.receipts.append(receipt)
        return receipt


class ObservedHermes(HermesTask):
    """Retain only a safe failure code when native task storage hides provider exceptions."""

    failure: str | None = None

    async def execute(
        self,
        request: TaskSubmission,
        context: ToolContext,
        progress: Callable[[TaskState], Awaitable[None]],
    ) -> TaskResult:
        try:
            return await super().execute(request, context, progress)
        except Exception as error:
            code = error.code if isinstance(error, GatewayError) else str(error)
            self.failure = (
                code
                if isinstance(error, (GatewayError, TaskConflict))
                and re.fullmatch(r"[a-z_]{1,80}", code)
                else type(error).__name__
            )
            raise


def _files(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                result[str(path.relative_to(root))] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return result


@pytest.mark.integration
@pytest.mark.end_to_end
def test_real_native_hermes_broker_outbox_and_followup_p0(tmp_path: Path) -> None:
    setting = os.getenv("EMBER_TEST_HERMES_CONFIG")
    if not setting:
        pytest.skip("Set EMBER_TEST_HERMES_CONFIG with an idle isolated gateway and native worker.")
    assert os.name == "nt", "The production qualification must execute natively on Windows."
    config = load_config(Path(setting), require_model_pin=True)
    assert config.hermes is not None and not config.hermes.private_recall
    assert Path(config.hermes.command[0]).name.lower() not in {"wsl", "wsl.exe"}
    agent_token = (config.runtime_root / "gateway-agent.token").read_text().strip()
    foreground_token = (config.runtime_root / "gateway.token").read_text().strip()
    canonical = tmp_path / "synthetic-canonical"
    with CanonicalEngine(canonical) as engine:
        character = engine.create_character().store_id
    before = _files(canonical)
    # Never open, migrate or mutate the operator's configured canonical root.
    config = config.model_copy(update={"data_root": canonical})
    session, turn = uuid4(), uuid4()

    async def run() -> dict[str, object]:
        agent = HttpGatewayClient(config.services.gateway.endpoint, agent_token)
        foreground = HttpGatewayClient(config.services.gateway.endpoint, foreground_token)
        observed = ObservedModels(agent)
        search = SearXNGTools(config.hermes.search_endpoint)
        observed_search = ObservedSearch(search)
        delegate = ObservedHermes(config, observed, ToolRouter(observed_search, character))
        router = TaskRouter(
            tmp_path / "operational",
            character,
            session,
            TextInspectionTask(ToolRouter(TextInspectionProvider(), character)),
            delegate=delegate,
        )
        await router.start()
        try:
            health = await agent.health()
            assert health.status == "ready" and health.journal_status == "available"
            assert not health.realtime_sessions and "P2" in health.supported_workloads
            assert health.model == config.model_tag and health.digest == config.model_digest
            assert health.context_tokens == config.context_tokens and health.context_tokens >= 64000
            started = monotonic()
            handle = await router.submit(
                "real-hermes-" + str(uuid4()),
                "Find official SearXNG search API docs and summarize GET/POST formats "
                "citing source.",
                turn_id=turn,
                operation="delegate",
            )
            async with asyncio.timeout(190):
                while (
                    snapshot := await router.status(handle.task_id)
                ) is None or snapshot.state not in TERMINAL_STATES:
                    await asyncio.sleep(0.05)
            elapsed = monotonic() - started
            assert snapshot.state == "succeeded", (
                "Native Hermes delegation did not succeed: "
                f"{delegate.failure}; model calls={len(observed.requests)}, "
                f"search receipts={len(observed_search.receipts)}"
            )
            assert snapshot.receipt is not None and snapshot.result is not None
            result = TaskResult.model_validate_json(snapshot.receipt)
            receipt = result.receipt
            assert receipt.outcome == "success" and receipt.tool == "hermes"
            assert (
                receipt.context.character_id == character and receipt.context.session_id == session
            )
            assert receipt.context.turn_id == turn and receipt.context.task_id == handle.task_id
            assert receipt.data["purged"] is True
            assert receipt.data["profile_sha256"] == config.hermes.profile_sha256
            assert receipt.data["revision"] == config.hermes.revision
            assert not result.citations and result.dependencies is None
            assert observed_search.receipts and len(observed_search.receipts) <= 3
            assert all(
                item.context == receipt.context
                and item.tool == "searx_search"
                and item.outcome == "success"
                for item in observed_search.receipts
            )
            citations = receipt.data["citations"]
            assert isinstance(citations, list) and citations
            assert any(
                isinstance(item, dict)
                and isinstance(url := item.get("url"), str)
                and urlsplit(url).hostname == "docs.searxng.org"
                and "search_api" in urlsplit(url).path
                for item in citations
            )
            assert "GET" in result.summary.upper() and "POST" in result.summary.upper()
            tool_receipts = receipt.data["tool_receipts"]
            assert isinstance(tool_receipts, list) and len(tool_receipts) == len(
                observed_search.receipts
            )
            assert observed.requests and len(observed.requests) <= 12
            assert all(
                request.workload == "P2"
                and request.context_id == handle.task_id
                and not request.thinking
                for request in observed.requests
            )
            assert len({request.request_id for request in observed.requests}) == len(
                observed.requests
            )
            assert len(observed.usage) == len(observed.requests)
            notifications = await router.notifications(None)
            assert (
                len(notifications.items) == 1 and notifications.items[0].task_id == handle.task_id
            )
            assert notifications.items[0].state == "succeeded"
            assert "1 task result" in await router.turn_notice()
            await router.acknowledge(handle.task_id)
            assert not (await router.notifications(None)).items
            assert (await foreground.health()).status == "ready"
            followup = GatewayRequest(
                request_id=uuid4(),
                context_id=uuid4(),
                model=config.model_tag,
                messages=(PromptMessage(role="user", content="Reply with exactly: gateway ready"),),
                max_output_tokens=32,
                deadline_at=(datetime.now(UTC) + timedelta(seconds=60))
                .isoformat()
                .replace("+00:00", "Z"),
            )
            followup_started = monotonic()
            events = [event async for event in foreground.stream(followup)]
            visible = "".join(event.text for event in events)
            assert "gateway ready" in visible.lower() and "searxng" not in visible.lower()
            assert events[-1].kind == "end" and events[-1].usage is not None
            assert followup.context_id != handle.task_id and followup.workload == "P0"
            assert _files(canonical) == before
            return {
                "profile_sha256": config.hermes.profile_sha256,
                "revision": config.hermes.revision,
                "model": config.model_tag,
                "model_digest": config.model_digest,
                "context_tokens": config.context_tokens,
                "task_id": str(handle.task_id),
                "p2_request_ids": [str(request.request_id) for request in observed.requests],
                "p2_context_ids": [str(request.context_id) for request in observed.requests],
                "p2_model_calls": len(observed.requests),
                "search_calls": len(observed_search.receipts),
                "citation_count": len(citations),
                "official_search_api_cited": True,
                "structured_finish_verified": True,
                "purged": True,
                "outbox_delivered_and_acknowledged": True,
                "canonical_files_unchanged": True,
                "task_seconds": elapsed,
                "p2_usage": [usage.model_dump(mode="json") for usage in observed.usage],
                "followup_p0_seconds": monotonic() - followup_started,
                "followup_p0_context_id": str(followup.context_id),
                "followup_p0_usage": events[-1].usage.model_dump(mode="json"),
            }
        finally:
            await router.aclose()
            await search.aclose()
            await agent.aclose()
            await foreground.aclose()

    report = asyncio.run(run())
    destination = os.getenv("EMBER_TEST_HERMES_REPORT")
    if destination:
        path = Path(destination).resolve()
        assert ".local" in {part.lower() for part in path.parts}, (
            "Use ignored .local report storage."
        )
        root = Path(__file__).resolve().parents[2]
        sources = [
            *sorted((root / "scripts/hermes").glob("hermes_*.py")),
            root / "ember-agent/src/ember_agent/inference/openai_bridge.py",
            root / "ember-agent/src/ember_agent/runtime/hermes_tasks.py",
            root / "ember-agent/src/ember_agent/runtime/worker_process.py",
        ]
        report["source_sha256"] = {
            str(source.relative_to(root)).replace("\\", "/"): hashlib.sha256(
                source.read_bytes()
            ).hexdigest()
            for source in sources
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
