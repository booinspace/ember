"""One isolated Hermes workflow through actor-bound tools and the existing P2 gateway."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import time

from pydantic import Field, JsonValue, TypeAdapter

from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.related import ContextDependencies

from ..inference.contracts import ModelClient, ToolDefinition, ToolFunction
from ..inference.openai_bridge import OpenAIModelBridge
from .config import DeploymentConfig, RuntimeModel
from .task_router import TaskResult, ToolContext, ToolReceipt, ToolRouter
from .task_store import TaskConflict, TaskState, TaskSubmission
from .worker_process import WIRE_BYTES, owned_stdio, settled

JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
TOOLS = ("searx_search", "ember_recall", "ember_finish")
SEARCH_CONTEXT_BYTES = 2048


def copy_search_results(results: list[JsonValue], call: int) -> list[JsonValue]:
    """Copy whole broker references under a task-context budget without changing their receipt."""
    selected: list[JsonValue] = []
    for row in results:
        if not isinstance(row, dict):
            raise TaskConflict("hermes_invalid_reference")
        identity = row.get("id")
        if not isinstance(identity, str):
            raise TaskConflict("hermes_invalid_reference")
        candidate: dict[str, JsonValue] = {**row, "id": "q" + str(call) + "-" + identity}
        body = json.dumps(
            {"results": [*selected, candidate]}, ensure_ascii=False, allow_nan=False
        ).encode()
        if len(body) <= SEARCH_CONTEXT_BYTES:
            selected.append(candidate)
    return selected


class HermesReport(RuntimeModel):
    """Only broker-observed references can support an external worker's final report."""

    summary: str = Field(
        min_length=1,
        max_length=2000,
        repr=False,
        description=(
            "Concise findings grounded in broker results. Put source IDs in citations, not URLs."
        ),
    )
    citations: tuple[str, ...] = Field(
        max_length=3,
        description=(
            "Unique broker-issued citation IDs exactly as listed; never URLs or invented IDs."
        ),
    )


def available_model_tools(
    advertised: tuple[ToolDefinition, ...],
    *,
    search_remaining: int,
    recall_available: bool,
    evidence_available: bool,
    citation_ids: tuple[str, ...],
) -> tuple[ToolDefinition, ...]:
    """Advertise current host authority; worker schemas never grant an operation or citation."""
    selected: list[ToolDefinition] = []
    for tool in advertised:
        name = tool.function.name
        parameters: dict[str, JsonValue]
        if name == "ember_finish" and evidence_available:
            parameters = JSON_OBJECT.validate_json(json.dumps(HermesReport.model_json_schema()))
            properties = parameters["properties"]
            assert isinstance(properties, dict)
            citations = properties["citations"]
            assert isinstance(citations, dict)
            if citation_ids:
                citations["minItems"] = 1
                citations["items"] = {"type": "string", "enum": list(citation_ids)}
            else:
                citations["maxItems"] = 0
            description = (
                "Finish now with concise supported findings and the listed citation IDs. "
                "Report gaps honestly. Citation IDs are not URLs."
            )
        elif name == "searx_search" and search_remaining > 0:
            parameters = {
                "type": "object",
                "additionalProperties": False,
                "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 400}},
                "required": ["query"],
            }
            description = (
                f"Search public sources; {search_remaining} search call(s) remain. "
                "Prefer the first useful official source, then call ember_finish."
            )
        elif name == "ember_recall" and recall_available:
            parameters = {
                "type": "object",
                "additionalProperties": False,
                "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 400}},
                "required": ["query"],
            }
            description = (
                "Read authorized private context once; public search is unavailable after it."
            )
        else:
            continue
        selected.append(
            ToolDefinition(
                function=ToolFunction(name=name, description=description, parameters=parameters)
            )
        )
    return tuple(selected)


@dataclass(frozen=True)
class PersonalContext:
    """Volatile authorized selections, bounded independently of the runner's context capacity."""

    text: str
    dependencies: ContextDependencies
    sources: tuple[SourceRef, ...] = ()


class HermesTask:
    """Advertise only one-shot read-only delegation; safe checkpoints are native task receipts."""

    capabilities = {
        "protocol": 1,
        "async_execution": True,
        "events": True,
        "cancel": True,
        "purge": True,
        "user_input": False,
        "updates": False,
        "resume_execution": False,
        "tool_effects": ["read"],
    }

    def __init__(
        self,
        config: DeploymentConfig,
        client: ModelClient,
        tools: ToolRouter,
        *,
        recall: Callable[[str, ToolContext], Awaitable[PersonalContext | None]] | None = None,
        validate: Callable[[ContextDependencies], Awaitable[bool]] | None = None,
    ) -> None:
        if config.hermes is None:
            raise TaskConflict("hermes_not_configured")
        self.config, self.settings = config, config.hermes
        self.client, self.tools = client, tools
        self.recall, self.validate = recall, validate

    async def execute(
        self,
        request: TaskSubmission,
        context: ToolContext,
        progress: Callable[[TaskState], Awaitable[None]],
    ) -> TaskResult:
        """Retain process and context ownership until a verified purge and terminal receipt."""
        if (
            request.character_id != context.character_id
            or request.session_id != context.session_id
            or request.turn_id != context.turn_id
            or context.character_id != self.tools.character_id
        ):
            raise TaskConflict("hermes_scope_denied")
        deadline = min(context.deadline_at or time() + 180, time() + 180)
        bridge = OpenAIModelBridge(
            self.client,
            self.config,
            context_id=context.task_id,
            deadline_at=timestamp_from_datetime(datetime.fromtimestamp(deadline, UTC)),
        )
        personal: PersonalContext | None = None
        issued: dict[str, dict[str, JsonValue]] = {}
        receipts: list[JsonValue] = []
        accepted: HermesReport | None = None
        recall_attempted = False
        search_calls = 0
        tool_calls = 0
        previous_id = 0

        def model_tools(advertised: tuple[ToolDefinition, ...]) -> tuple[ToolDefinition, ...]:
            return available_model_tools(
                advertised,
                search_remaining=0 if personal is not None else 3 - search_calls,
                recall_available=self.settings.private_recall
                and self.recall is not None
                and not recall_attempted,
                evidence_available=bool(receipts) or personal is not None,
                citation_ids=tuple(issued),
            )

        async def check_context() -> None:
            if personal is not None and (
                self.validate is None or not await self.validate(personal.dependencies)
            ):
                raise TaskConflict("hermes_context_revoked")

        async def watch_context() -> None:
            while True:
                await asyncio.sleep(0.25)
                await check_context()

        async with owned_stdio(self.settings.command) as process:
            output, input_stream = process.stdout, process.stdin
            assert output is not None and input_stream is not None

            async def receive() -> dict[str, JsonValue]:
                try:
                    data = await output.readline()
                    if not data or len(data) > WIRE_BYTES:
                        raise TaskConflict("hermes_incomplete_protocol")
                    frame = JSON_OBJECT.validate_json(data)
                    if frame.get("protocol") != 1 or type(frame.get("protocol")) is not int:
                        raise TaskConflict("hermes_protocol_mismatch")
                    return frame
                except (ValueError, RecursionError) as error:
                    raise TaskConflict("hermes_invalid_frame") from error

            async def send(frame: dict[str, JsonValue]) -> None:
                encoded = json.dumps(frame, ensure_ascii=False, allow_nan=False).encode()
                if len(encoded) > WIRE_BYTES:
                    raise TaskConflict("hermes_wire_limit")
                input_stream.write(encoded + b"\n")
                await input_stream.drain()

            async def run() -> TaskResult:
                nonlocal personal, accepted, recall_attempted, search_calls, tool_calls, previous_id
                hello = await receive()
                if hello != {
                    "type": "hello",
                    "protocol": 1,
                    "revision": self.settings.revision,
                    "profile_sha256": self.settings.profile_sha256,
                    "tools": list(TOOLS),
                }:
                    raise TaskConflict("hermes_profile_mismatch")
                await send(
                    {
                        "type": "start",
                        "protocol": 1,
                        "goal": request.goal,
                        "context": [],
                        "allowed_tools": [
                            name
                            for name in TOOLS
                            if name != "ember_recall" or self.settings.private_recall
                        ],
                        "model": self.config.model_tag,
                        "context_tokens": self.config.context_tokens,
                        "max_iterations": 12,
                        "max_output_tokens": min(1192, self.config.output_reserve_tokens),
                        "timeout_seconds": max(1, int(deadline - time())),
                    }
                )
                while True:
                    await check_context()
                    frame = await receive()
                    kind = frame.get("type")
                    if kind == "result":
                        if set(frame) != {"protocol", "type", "result"} or accepted is None:
                            raise TaskConflict("hermes_unverified_result")
                        report = HermesReport.model_validate_json(json.dumps(frame["result"]))
                        if report != accepted:
                            raise TaskConflict("hermes_result_changed")
                        stopped = await receive()
                        if (
                            stopped
                            != {
                                "type": "stopped",
                                "protocol": 1,
                                "purged": True,
                                "returncode": 0,
                            }
                            or stopped.get("purged") is not True
                            or type(stopped.get("returncode")) is not int
                        ):
                            raise TaskConflict("hermes_purge_unverified")
                        if await output.read(1) or await process.wait() != 0:
                            raise TaskConflict("hermes_incomplete_shutdown")
                        await check_context()
                        references: list[JsonValue] = [issued[key] for key in report.citations]
                        links = [issued[key] for key in report.citations if "url" in issued[key]]
                        return TaskResult(
                            summary=report.summary
                            + (
                                "\n\nSources:\n"
                                + "\n".join(
                                    str(ref["title"]) + ": " + str(ref["url"]) for ref in links
                                )
                                if links
                                else ""
                            ),
                            receipt=ToolReceipt(
                                context=context,
                                tool="hermes",
                                data={
                                    "revision": self.settings.revision,
                                    "profile_sha256": self.settings.profile_sha256,
                                    "citations": references,
                                    "tool_receipts": receipts,
                                    "purged": True,
                                },
                            ),
                            dependencies=personal.dependencies if personal else None,
                            citations=personal.sources if personal else (),
                        )
                    identity = frame.get("id")
                    if type(identity) is not int or identity != previous_id + 1 or accepted:
                        raise TaskConflict("hermes_protocol_order")
                    previous_id = identity
                    body: JsonValue
                    if kind == "model":
                        if set(frame) != {"protocol", "type", "id", "method", "path", "body"}:
                            raise TaskConflict("hermes_invalid_model_frame")
                        await progress("waiting_for_inference")
                        if frame["method"] == "GET" and frame["path"] == "/v1/models":
                            body = bridge.models()
                        elif (
                            frame["method"] == "POST"
                            and frame["path"] == "/v1/chat/completions"
                            and isinstance(frame["body"], dict)
                        ):
                            body = await bridge.complete(frame["body"], tool_policy=model_tools)
                        else:
                            raise TaskConflict("hermes_model_route_denied")
                    elif kind == "tool":
                        if set(frame) != {"protocol", "type", "id", "name", "arguments"}:
                            raise TaskConflict("hermes_invalid_tool_frame")
                        name, arguments = frame["name"], frame["arguments"]
                        if not isinstance(arguments, dict) or name not in TOOLS:
                            raise TaskConflict("hermes_tool_denied")
                        tool_calls += 1
                        if tool_calls > 30:
                            raise TaskConflict("hermes_tool_budget")
                        await progress("waiting_for_tool")
                        if name == "searx_search":
                            search_calls += 1
                            if search_calls > 3 or personal is not None:
                                raise TaskConflict("hermes_search_scope_denied")
                            receipt = await self.tools.invoke(
                                name, arguments, context, allowed=frozenset({"searx_search"})
                            )
                            if receipt.outcome != "success":
                                raise TaskConflict("hermes_tool_failed")
                            results = receipt.data.get("results")
                            if not isinstance(results, list):
                                raise TaskConflict("hermes_invalid_tool_result")
                            selected = copy_search_results(results, search_calls)
                            for row in selected:
                                assert isinstance(row, dict) and isinstance(row["id"], str)
                                issued[row["id"]] = row
                            body = {"results": selected}
                            receipts.append(
                                {
                                    "tool": receipt.tool,
                                    "outcome": receipt.outcome,
                                    "call": tool_calls,
                                }
                            )
                        elif name == "ember_recall":
                            if (
                                not self.settings.private_recall
                                or self.recall is None
                                or recall_attempted
                                or set(arguments) != {"query"}
                                or not isinstance(arguments["query"], str)
                                or not 1 <= len(arguments["query"]) <= 512
                            ):
                                raise TaskConflict("hermes_recall_scope_denied")
                            recall_attempted = True
                            personal = await self.recall(arguments["query"], context)
                            if personal is not None:
                                if len(personal.text.encode()) > 1024:
                                    raise TaskConflict("hermes_context_budget")
                                await check_context()
                                issued["memory"] = {"id": "memory", "title": "Ember scoped recall"}
                            body = {
                                "context": personal.text
                                if personal
                                else "No eligible memory found.",
                                "citation_ids": ["memory"] if personal else [],
                            }
                        else:
                            report = HermesReport.model_validate_json(json.dumps(arguments))
                            urls = set(re.findall(r"https?://[^\s<>\]\)]+", report.summary))
                            cited_urls = {
                                str(issued[key]["url"])
                                for key in report.citations
                                if key in issued and "url" in issued[key]
                            }
                            if len(set(report.citations)) != len(report.citations):
                                raise TaskConflict("hermes_unverified_citations_duplicate")
                            if any(key not in issued for key in report.citations):
                                raise TaskConflict("hermes_unverified_citations_unknown_id")
                            if not receipts and personal is None:
                                raise TaskConflict("hermes_unverified_citations_no_evidence")
                            if issued and not report.citations:
                                raise TaskConflict("hermes_unverified_citations_missing")
                            if not urls <= cited_urls:
                                raise TaskConflict("hermes_unverified_citations_url_mismatch")
                            accepted = report
                            body = {"accepted": True}
                    else:
                        raise TaskConflict("hermes_unexpected_frame")
                    await check_context()
                    await send(
                        {"type": "reply", "protocol": 1, "id": identity, "ok": True, "body": body}
                    )

            async with asyncio.timeout(max(0.0, deadline - time())):
                execution = asyncio.create_task(run())
                watcher = asyncio.create_task(watch_context())
                try:
                    done, _ = await asyncio.wait(
                        (execution, watcher), return_when=asyncio.FIRST_COMPLETED
                    )
                    if watcher in done:
                        await watcher
                        raise TaskConflict("hermes_context_watch_failed")
                    return await execution
                finally:
                    execution.cancel()
                    watcher.cancel()

                    async def drain() -> None:
                        await asyncio.gather(execution, watcher, return_exceptions=True)

                    await settled(asyncio.create_task(drain()))
