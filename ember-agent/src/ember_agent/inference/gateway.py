"""P0/P3/P4 admission; caller cancellation does not release the owned runner slot."""

import asyncio
import json
from collections import deque
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
from .upstream import NativeFrame, NativeTool, Upstream
from .visible import VisibleText


@dataclass
class Generation:
    """Owned job with a bounded subscriber queue and explicit detach/finish state."""

    request: GatewayRequest
    queue: asyncio.Queue[GatewayEvent] = field(default_factory=lambda: asyncio.Queue(maxsize=32))
    admitted_at: float = field(default_factory=monotonic)
    detached: bool = False
    task: asyncio.Task[None] | None = None
    sequence: int = 0
    on_detach: Callable[[], None] | None = None
    expiry: asyncio.TimerHandle | None = None
    heartbeat: asyncio.TimerHandle | None = None

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
            if self.on_detach is not None:
                self.on_detach()


class P0Gateway:
    """One active upstream; future workload classes extend this owner rather than replacing it."""

    def __init__(
        self,
        config: DeploymentConfig,
        upstream: Upstream,
        *,
        record: Callable[[str, GatewayRequest], Awaitable[None]] | None = None,
        uncertain: bool = False,
        sessions: tuple[UUID, ...] = (),
        record_sessions: Callable[[tuple[UUID, ...]], Awaitable[None]] | None = None,
        background_queue_limit: int = 8,
    ) -> None:
        self.config = config
        self.upstream = upstream
        self.record = record
        self.active: Generation | None = None
        self.uncertain = uncertain
        self.closed = False
        self.journal_failed = False
        self.used_ids: set[UUID] = set()
        self.completed_ids: deque[UUID] = deque()
        self.sessions = set(sessions)
        self._session_guard = asyncio.Lock()
        self.record_sessions = record_sessions
        self.background_queue_limit = background_queue_limit
        self.pending: dict[str, deque[Generation]] = {
            name: deque() for name in ("P1", "P2", "P3", "P4", "P5")
        }
        self.dispatcher: asyncio.Task[None] | None = None
        self._schedule = ("P2", "P2", "P2", "P2", "P3", "P4", "P5")
        self._schedule_cursor = 0
        self._foreground_waiters = 0

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
        if request.model != self.config.model_tag:
            raise GatewayError("model_not_allowlisted")
        if self.journal_failed:
            raise GatewayError("journal_unavailable")
        if self.uncertain:
            raise GatewayError("quiescence_required")
        if request.workload == "P1" and request.context_id not in self.sessions:
            raise GatewayError("live_reservation_required")
        if request.workload == "P5" and not self.config.inference.coding_api:
            raise GatewayError("workload_not_authorized")
        if request.workload == "P0" and self.active is not None:
            active = self.active
            if active.request.workload != "P5" or self._foreground_waiters:
                raise GatewayError("busy")
            self._foreground_waiters += 1
            try:
                # Admission itself may still be checking the pin or writing its journal.
                if active.task is None and self.dispatcher is not None:
                    await asyncio.shield(self.dispatcher)
                if active.task is not None:
                    await asyncio.shield(active.task)
                return await self.admit(request)
            finally:
                self._foreground_waiters -= 1
                self._wake_background()
        if (
            request.workload != "P0"
            and len(self.pending[request.workload]) >= self.background_queue_limit
        ):
            raise GatewayError("background_queue_full")
        if request.workload in {"P3", "P4"} and (request.output != "json" or request.tools):
            raise GatewayError("background_requires_structured_output")
        deadline = datetime.fromisoformat(request.deadline_at.replace("Z", "+00:00"))
        if deadline <= datetime.now(UTC):
            raise GatewayError("deadline_exceeded")
        reserve = max(1192, self.config.output_reserve_tokens)
        if request.max_output_tokens > reserve:
            raise GatewayError("output_budget_exceeded")
        ceiling = min(self.config.max_input_tokens, self.config.context_tokens - reserve)
        if self.input_cost(request) > ceiling or request.max_output_tokens > reserve:
            raise GatewayError("prompt_overflow")
        if request.request_id in self.used_ids:
            raise GatewayError("duplicate_request")
        if len(self.used_ids) >= 10000:
            if not self.completed_ids:
                raise GatewayError("request_registry_full")
            self.used_ids.discard(self.completed_ids.popleft())
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
        if request.output_schema is not None:
            if request.output != "json" or len(json.dumps(request.output_schema)) > 16000:
                raise GatewayError("invalid_output_schema")
            try:
                Draft202012Validator.check_schema(request.output_schema)
            except Exception as error:
                raise GatewayError("invalid_output_schema") from error
            references = json.dumps(request.output_schema)
            if any(item in references for item in ("http:", "https:", "file:")):
                raise GatewayError("external_schema_reference")
        job = Generation(request)
        if request.workload != "P0":
            self.used_ids.add(request.request_id)
            self.pending[request.workload].append(job)
            job.emit("start")
            loop = asyncio.get_running_loop()
            job.expiry = loop.call_later(
                max(0.0, (deadline - datetime.now(UTC)).total_seconds()),
                self._remove_pending,
                job,
                "deadline_exceeded",
            )
            job.on_detach = lambda: self._remove_pending(job, "subscriber_detached")
            self._heartbeat(job)
            self._wake_background()
            return job
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

    def _remove_pending(self, job: Generation, code: str) -> None:
        queue = self.pending.get(job.request.workload)
        if queue is None or job not in queue:
            return
        queue.remove(job)
        if job.expiry is not None:
            job.expiry.cancel()
        if job.heartbeat is not None:
            job.heartbeat.cancel()
        job.emit("error", code=code)
        self.completed_ids.append(job.request.request_id)

    def _heartbeat(self, job: Generation) -> None:
        if job not in self.pending[job.request.workload]:
            return
        if job.detached:
            self._remove_pending(job, "subscriber_detached")
            return
        job.emit("progress")
        job.heartbeat = asyncio.get_running_loop().call_later(5, self._heartbeat, job)

    def _wake_background(self) -> None:
        if self.closed or self.uncertain or self.journal_failed or self.active is not None:
            return
        if self.dispatcher is not None and not self.dispatcher.done():
            return
        if self._foreground_waiters:
            return
        if self._live_job() is None and not self.pending["P5"] and (
            self.sessions or not any(self.pending[name] for name in ("P2", "P3", "P4"))
        ):
            return
        self.dispatcher = asyncio.create_task(self._dispatch(), name="ember-background-dispatch")

        def completed(task: asyncio.Task[None]) -> None:
            if not task.cancelled() and task.exception() is not None:
                self.uncertain = True
                for queue in self.pending.values():
                    for job in tuple(queue):
                        self._remove_pending(job, "scheduler_failed")
            elif not self.closed:
                self._wake_background()

        self.dispatcher.add_done_callback(completed)

    def _park(self, job: Generation) -> None:
        # Metadata checks have not generated tokens. Return the step to its FIFO
        # boundary when realtime ownership arrives during admission.
        self.active = None
        if job.detached:
            self.completed_ids.append(job.request.request_id)
            return
        self.pending[job.request.workload].appendleft(job)
        deadline = datetime.fromisoformat(job.request.deadline_at.replace("Z", "+00:00"))
        job.expiry = asyncio.get_running_loop().call_later(
            max(0.0, (deadline - datetime.now(UTC)).total_seconds()),
            self._remove_pending,
            job,
            "deadline_exceeded",
        )
        self._heartbeat(job)

    def _eligible(self, job: Generation) -> bool:
        if job.request.workload == "P1":
            return job.request.context_id in self.sessions
        if job.request.workload == "P5":
            return not self._foreground_waiters and self._live_job() is None
        return not self.sessions

    def _live_job(self) -> Generation | None:
        return next(
            (job for job in self.pending["P1"] if job.request.context_id in self.sessions),
            None,
        )

    async def _dispatch(self) -> None:
        if self.active is not None or self.closed or self.uncertain or self.journal_failed:
            return
        job = self._live_job()
        if job is not None:
            self.pending["P1"].remove(job)
        elif self._foreground_waiters:
            return
        elif self.sessions:
            if not self.pending["P5"]:
                return
            job = self.pending["P5"].popleft()
        else:
            # Include coding in the existing round-robin when background is eligible.
            for offset in range(len(self._schedule)):
                index = (self._schedule_cursor + offset) % len(self._schedule)
                queue = self.pending[self._schedule[index]]
                if queue:
                    job = queue.popleft()
                    self._schedule_cursor = (index + 1) % len(self._schedule)
                    break
            else:
                return
        if job.expiry is not None:
            job.expiry.cancel()
        if job.heartbeat is not None:
            job.heartbeat.cancel()
        self.active = job
        try:
            deadline = datetime.fromisoformat(job.request.deadline_at.replace("Z", "+00:00"))
            if deadline <= datetime.now(UTC):
                raise GatewayError("deadline_exceeded")
            await self.upstream.verify()
            if not self._eligible(job) or job.detached:
                self._park(job)
                return
            if self.record:
                await self.record("running", job.request)
            if not self._eligible(job) or job.detached:
                if self.record:
                    await self.record("complete", job.request)
                self._park(job)
                return
        except Exception as error:
            self.active = None
            self.completed_ids.append(job.request.request_id)
            job.emit(
                "error", code=error.code if isinstance(error, GatewayError) else "admission_failed"
            )
            return
        job.task = asyncio.create_task(
            self._pump(job), name="ember-background-" + str(job.request.request_id)
        )

    async def session(self, identity: UUID, *, connected: bool) -> None:
        """Reserve before waiting for background quiescence; idle connected sessions block it.

        Journal failure leaves background blocked. No native cancellation or release
        proof is inferred from an HTTP disconnect or apparent idle gap.
        """
        if connected:
            if self.closed:
                raise GatewayError("draining")
            if identity not in self.sessions and len(self.sessions) >= 32:
                raise GatewayError("session_limit")
            self.sessions.add(identity)
        async with self._session_guard:
            proposed = self.sessions if connected else self.sessions - {identity}
            try:
                if self.record_sessions is not None:
                    await self.record_sessions(tuple(sorted(proposed, key=str)))
            except Exception as error:
                self.sessions.add(identity)
                self.journal_failed = True
                raise GatewayError("session_journal_failed") from error
            if not connected:
                # Keep admission blocked until durable disconnection succeeds.
                self.sessions.discard(identity)
                for job in tuple(self.pending["P1"]):
                    if job.request.context_id == identity:
                        self._remove_pending(job, "live_reservation_released")

        if connected:
            # A dispatcher can be between journal admission and starting its pump.
            if self.dispatcher is not None and not self.dispatcher.done():
                await asyncio.shield(self.dispatcher)
            active = self.active
            if (
                active is not None
                and active.request.workload in {"P2", "P3", "P4"}
                and active.task is not None
            ):
                await asyncio.shield(active.task)
            if self.uncertain:
                raise GatewayError("quiescence_required")
        else:
            self._wake_background()

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
        if job.sequence == 0:
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
                    if request.thinking and frame.message.thinking:
                        job.emit("thinking", text=frame.message.thinking)
                    text = filtered.feed(frame.message.content)
                    if text:
                        if first is None:
                            first = monotonic() - started
                        if request.output == "json" or request.workload == "P2":
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
                if frame.done_reason == "length" and (
                    request.output == "json"
                    or (request.workload != "P5" and (request.tools or request.workload != "P0"))
                ):
                    raise GatewayError("upstream_output_truncated")
                if request.output == "json":
                    raw = "".join(json_text)
                    adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
                    try:
                        parsed = adapter.validate_json(raw)
                        json.dumps(parsed, allow_nan=False)
                    except ValueError as error:
                        raise GatewayError("invalid_structured_output") from error
                    if request.output_schema is not None:
                        validator = Draft202012Validator(request.output_schema)
                        validate = cast(Callable[[object], bool], validator.is_valid)  # pyright: ignore[reportUnknownMemberType]
                        if not validate(parsed):
                            raise GatewayError("structured_schema_mismatch")
                truncated = request.workload == "P5" and frame.done_reason == "length"
                calls = () if truncated else self._tools(request, tools)
                usage = GenerationUsage(
                    prompt_tokens=frame.prompt_eval_count,
                    output_tokens=frame.eval_count,
                    total_seconds=monotonic() - started,
                    first_visible_seconds=first,
                    tokens_per_second=frame.decode_tokens_per_second
                    if frame.decode_tokens_per_second is not None
                    else frame.eval_count / (frame.eval_duration / 1e9)
                    if frame.eval_duration > 0
                    else None,
                    load_seconds=frame.load_duration / 1e9,
                    queue_seconds=started - job.admitted_at
                    if request.workload in {"P1", "P2"}
                    else None,
                    prefill_seconds=frame.prompt_eval_duration / 1e9
                    if request.version in {"0.3.0", "0.4.0"}
                    and frame.prompt_eval_duration is not None
                    else None,
                )
                if self.record:
                    await self.record("complete", request)
                if request.output == "json" or request.workload == "P2":
                    job.emit("text", text="".join(json_text))
                if calls:
                    job.emit("tools", calls=calls)
                job.emit("end", usage=usage, truncated=truncated)
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
            self.completed_ids.append(job.request.request_id)
            self._wake_background()

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
            supported_workloads=("P0", "P1", "P2", "P3", "P4"),
            queued_p1=len(self.pending["P1"]),
            queued_p2=len(self.pending["P2"]),
            queued_p3=len(self.pending["P3"]),
            queued_p4=len(self.pending["P4"]),
            realtime_sessions=tuple(sorted(self.sessions, key=str)),
            features=("live_thinking_v1",),
        )

    async def aclose(self) -> None:
        """Drain owned work and close transport without stopping the external runner."""
        self.closed = True
        for queue in self.pending.values():
            for job in tuple(queue):
                self._remove_pending(job, "draining")
        if self.dispatcher is not None:
            await asyncio.shield(self.dispatcher)
        if self.active is not None and self.active.task is not None:
            await asyncio.shield(self.active.task)
        await self.upstream.aclose()
