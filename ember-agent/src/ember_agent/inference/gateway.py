"""P0 admission and owned generation; caller cancellation does not release the runner slot."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import cast
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from pydantic import JsonValue, TypeAdapter

from ..runtime.config import DeploymentConfig
from .contracts import (
    GatewayError,
    GatewayEvent,
    GatewayHealth,
    GatewayRequest,
    GenerationUsage,
    ToolCall,
)
from .ollama import NativeFrame, NativeTool, Upstream
from .visible import VisibleText


@dataclass
class Generation:
    """Owned job with a bounded subscriber queue and explicit detach/finish state."""

    request: GatewayRequest
    queue: asyncio.Queue[GatewayEvent] = field(default_factory=lambda: asyncio.Queue(maxsize=32))
    detached: bool = False
    task: asyncio.Task[None] | None = None
    sequence: int = 0

    def emit(self, kind: str, **fields: object) -> None:
        if self.detached:
            return
        event = GatewayEvent.model_validate(
            {
                "request_id": self.request.request_id,
                "context_id": self.request.context_id,
                "sequence": self.sequence,
                "kind": kind,
                **fields,
            }
        )
        self.sequence += 1
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(
                GatewayEvent(
                    request_id=self.request.request_id,
                    context_id=self.request.context_id,
                    sequence=self.sequence,
                    kind="error",
                    code="slow_consumer",
                )
            )
            self.detached = True

    async def events(self) -> AsyncIterator[GatewayEvent]:
        """Detaching drains native generation instead of cancelling a possibly running model."""
        try:
            while True:
                event = await self.queue.get()
                yield event
                if event.kind in {"end", "error"}:
                    break
        finally:
            self.detached = True


class P0Gateway:
    """One active upstream; future workload classes extend this owner rather than replacing it."""

    def __init__(
        self,
        config: DeploymentConfig,
        upstream: Upstream,
        *,
        record: Callable[[str, GatewayRequest], Awaitable[None]] | None = None,
        uncertain: bool = False,
    ) -> None:
        self.config = config
        self.upstream = upstream
        self.record = record
        self.active: Generation | None = None
        self.uncertain = uncertain
        self.closed = False
        self.journal_failed = False
        self.used_ids: set[UUID] = set()

    def input_cost(self, request: GatewayRequest) -> int:
        """Conservative UTF-8 serialization plus role/template allowance until tokenizer pinning."""
        serialized = json.dumps(
            {
                "messages": [item.model_dump() for item in request.messages],
                "tools": [item.model_dump() for item in request.tools],
            },
            ensure_ascii=False,
        )
        return len(serialized.encode("utf-8")) + 512 + 64 * len(request.messages)

    async def admit(self, request: GatewayRequest) -> Generation:
        """Reject auth-class/model/budget/deadline/capacity failures before upstream generation."""
        request = GatewayRequest.model_validate_json(request.model_dump_json())
        if self.closed:
            raise GatewayError("draining")
        if request.workload != "P0":
            raise GatewayError("unsupported_workload")
        if request.model != self.config.model_tag:
            raise GatewayError("model_not_allowlisted")
        if self.uncertain:
            raise GatewayError("quiescence_required")
        if self.active is not None:
            raise GatewayError("busy")
        deadline = datetime.fromisoformat(request.deadline_at.replace("Z", "+00:00"))
        if deadline <= datetime.now(UTC):
            raise GatewayError("deadline_exceeded")
        reserve = max(1192, self.config.output_reserve_tokens)
        ceiling = min(self.config.max_input_tokens, self.config.context_tokens - reserve)
        if self.input_cost(request) > ceiling or request.max_output_tokens > reserve:
            raise GatewayError("prompt_overflow")
        if request.request_id in self.used_ids:
            raise GatewayError("duplicate_request")
        if len(self.used_ids) >= 10000:
            raise GatewayError("request_registry_full")
        if len({item.function.name for item in request.tools}) != len(request.tools):
            raise GatewayError("duplicate_tool_definition")
        for definition in request.tools:
            schema = definition.function.parameters
            if schema.get("type") != "object" or any(
                key in json.dumps(schema) for key in ('"$ref"', '"$dynamicRef"', '"$recursiveRef"')
            ):
                raise GatewayError("unsupported_tool_schema")
            try:
                Draft202012Validator.check_schema(schema)
            except Exception as error:
                raise GatewayError("invalid_tool_schema") from error
        job = Generation(request)
        self.active = job
        try:
            await self.upstream.verify()
            if self.record:
                await self.record("running", request)
        except BaseException:
            self.active = None
            raise
        self.used_ids.add(request.request_id)
        job.task = asyncio.create_task(self._pump(job), name="ember-p0-" + str(request.request_id))
        return job

    async def _pump(self, job: Generation) -> None:
        request = job.request
        started = monotonic()
        first: float | None = None
        complete = False
        frame = NativeFrame()
        filtered = VisibleText()
        json_text: list[str] = []
        json_size = 0
        tools: dict[int, NativeTool] = {}
        job.emit("start")
        try:
            remaining = max(
                0,
                (
                    datetime.fromisoformat(request.deadline_at.replace("Z", "+00:00"))
                    - datetime.now(UTC)
                ).total_seconds(),
            )
            async with asyncio.timeout(min(remaining, 180)):
                async for frame in self.upstream.generate(request):
                    text = filtered.feed(frame.message.content)
                    if text:
                        if first is None:
                            first = monotonic() - started
                        if request.output == "json":
                            json_text.append(text)
                            json_size += len(text.encode("utf-8"))
                            if json_size > 1024 * 1024:
                                raise GatewayError("structured_output_too_large")
                        else:
                            job.emit("text", text=text)
                    for tool in frame.message.tool_calls:
                        old = tools.get(tool.index)
                        if old is None:
                            tools[tool.index] = tool
                        elif old.function.name != tool.function.name:
                            raise GatewayError("tool_stream_conflict")
                        elif isinstance(old.function.arguments, str) and isinstance(
                            tool.function.arguments, str
                        ):
                            tools[tool.index] = tool.model_copy(
                                update={
                                    "function": tool.function.model_copy(
                                        update={
                                            "arguments": old.function.arguments
                                            + tool.function.arguments
                                        }
                                    )
                                }
                            )
                        elif isinstance(old.function.arguments, dict) and isinstance(
                            tool.function.arguments, dict
                        ):
                            tools[tool.index] = tool.model_copy(
                                update={
                                    "function": tool.function.model_copy(
                                        update={
                                            "arguments": {
                                                **old.function.arguments,
                                                **tool.function.arguments,
                                            }
                                        }
                                    )
                                }
                            )
                        else:
                            raise GatewayError("tool_stream_conflict")
                    if frame.done:
                        complete = True
                        break
                if not complete:
                    raise GatewayError("upstream_incomplete")
                filtered.finish()
                try:
                    await self.upstream.verify(loaded=True)
                except GatewayError:
                    self.uncertain = True
                    raise
                if frame.prompt_eval_count > min(
                    self.config.max_input_tokens, self.config.context_tokens - 1192
                ):
                    self.uncertain = True
                    raise GatewayError("upstream_budget_mismatch")
                if frame.eval_count > request.max_output_tokens:
                    self.uncertain = True
                    raise GatewayError("upstream_output_budget_mismatch")
                if request.output == "json":
                    raw = "".join(json_text)
                    adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
                    try:
                        parsed = adapter.validate_json(raw)
                        json.dumps(parsed, allow_nan=False)
                    except ValueError as error:
                        raise GatewayError("invalid_structured_output") from error
                    job.emit("text", text=raw)
                calls = self._tools(request, tools)
                if calls:
                    job.emit("tools", calls=calls)
                usage = GenerationUsage(
                    prompt_tokens=frame.prompt_eval_count,
                    output_tokens=frame.eval_count,
                    total_seconds=monotonic() - started,
                    first_visible_seconds=first,
                    tokens_per_second=frame.eval_count / (frame.eval_duration / 1e9)
                    if frame.eval_duration > 0
                    else None,
                    load_seconds=frame.load_duration / 1e9,
                )
                if self.record:
                    await self.record("complete", request)
                job.emit("end", usage=usage)
        except asyncio.CancelledError:
            self.uncertain = True
            if self.record:
                try:
                    await self.record("uncertain", request)
                except Exception:
                    # Expose journal failure; the durable running marker still blocks restart.
                    self.journal_failed = True
            job.emit("error", code="shutdown_outcome_unknown")
            raise
        except Exception as error:
            if not complete:
                self.uncertain = True
            if self.record:
                try:
                    await self.record("uncertain" if self.uncertain else "complete", request)
                except Exception:
                    self.uncertain = True
                    self.journal_failed = True
            job.emit(
                "error", code=error.code if isinstance(error, GatewayError) else "generation_failed"
            )
        finally:
            self.active = None

    def _tools(self, request: GatewayRequest, tools: dict[int, NativeTool]) -> tuple[ToolCall, ...]:
        definitions = {item.function.name: item.function for item in request.tools}
        result: list[ToolCall] = []
        adapter: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
        for tool in tools.values():
            definition = definitions.get(tool.function.name)
            if definition is None:
                raise GatewayError("unrequested_tool")
            try:
                arguments = (
                    adapter.validate_json(tool.function.arguments)
                    if isinstance(tool.function.arguments, str)
                    else adapter.validate_python(tool.function.arguments)
                )
                json.dumps(arguments, allow_nan=False)
            except ValueError as error:
                raise GatewayError("invalid_tool_arguments") from error
            validator = Draft202012Validator(definition.parameters)
            # Third-party overload exposes an Unknown deprecated second argument.
            valid = cast(Callable[[object], bool], validator.is_valid)  # pyright: ignore[reportUnknownMemberType]
            if not valid(arguments):
                raise GatewayError("invalid_tool_arguments")
            result.append(ToolCall(call_id=uuid4(), name=tool.function.name, arguments=arguments))
        return tuple(result)

    async def health(self) -> GatewayHealth:
        """Probe metadata without loading weights or generating an auxiliary request."""
        status = (
            "draining"
            if self.closed
            else "quiescence_required"
            if self.uncertain
            else "busy"
            if self.active
            else "ready"
        )
        if status == "ready":
            try:
                await self.upstream.verify()
            except GatewayError:
                status = "upstream_unavailable"
        return GatewayHealth(
            journal_status="failed" if self.journal_failed else "available",
            status=status,
            model=self.config.model_tag,
            digest=self.config.model_digest,
            context_tokens=self.config.context_tokens,
            active_request_id=self.active.request.request_id if self.active else None,
        )

    async def aclose(self) -> None:
        """Stop admission, drain owned work, then close transport without stopping Ollama."""
        self.closed = True
        if self.active is not None and self.active.task is not None:
            await asyncio.shield(self.active.task)
        await self.upstream.aclose()
