"""P0 production admission/transport/privacy with controlled native upstreams."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import (
    GatewayError,
    GatewayRequest,
    PromptMessage,
    ToolDefinition,
    ToolFunction,
)
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.ollama import (
    NativeFrame,
    NativeFunction,
    NativeMessage,
    NativeTool,
    OllamaUpstream,
)
from ember_agent.inference.service import ServiceOwner, create_app
from ember_agent.inference.visible import VisibleText
from ember_agent.runtime.config import DeploymentConfig, load_config

EXAMPLE = Path(__file__).parents[1] / "config/deployment.example.toml"


def configuration(tmp_path: Path) -> DeploymentConfig:
    path = tmp_path / "deployment.toml"
    path.write_text(
        EXAMPLE.read_text(encoding="utf-8")
        .replace('tag = ""', 'tag = "synthetic:27b"')
        .replace('expected_digest = ""', 'expected_digest = "' + "a" * 64 + '"'),
        encoding="utf-8",
    )
    return load_config(path, require_model_pin=True)


def request(config: DeploymentConfig, **updates: object) -> GatewayRequest:
    original = GatewayRequest(
        request_id=uuid4(),
        context_id=uuid4(),
        model=config.model_tag,
        messages=(PromptMessage(role="user", content="Synthetic hello"),),
        deadline_at=(datetime.now(UTC) + timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
    )
    return GatewayRequest.model_validate_json(original.model_copy(update=updates).model_dump_json())


class FakeUpstream:
    def __init__(self, frames: tuple[NativeFrame, ...] | None = None) -> None:
        self.frames = frames or (
            NativeFrame(message=NativeMessage(content="visible")),
            NativeFrame(done=True, prompt_eval_count=10, eval_count=2),
        )
        self.release = asyncio.Event()
        self.release.set()
        self.started = asyncio.Event()
        self.count = 0
        self.fail = False
        self.closed = False

    async def verify(self, *, loaded: bool = False) -> None:
        del loaded
        if self.fail:
            raise GatewayError("model_pin_mismatch")

    async def generate(self, value: GatewayRequest) -> AsyncIterator[NativeFrame]:
        del value
        self.count += 1
        self.started.set()
        await self.release.wait()
        for frame in self.frames:
            yield frame
            await asyncio.sleep(0)

    async def aclose(self) -> None:
        self.closed = True


def test_stream_strips_split_reasoning_and_has_terminal_usage(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream(
            tuple(
                NativeFrame(message=NativeMessage(content=text))
                for text in ("<th", "ink>SECRET", "</thi", "nk>Hello", " world")
            )
            + (NativeFrame(done=True),)
        )
        gateway = P0Gateway(config, upstream)
        events = [event async for event in (await gateway.admit(request(config))).events()]
        assert "".join(event.text for event in events) == "Hello world"
        assert [event.kind for event in events] == ["start", "text", "text", "end"]
        assert events[-1].usage is not None and gateway.active is None
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("workload", ["P1", "P2"])
def test_agent_workload_requires_scoped_token(tmp_path: Path, workload: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream()
        gateway = P0Gateway(config, upstream)
        app = create_app(gateway, "foreground", background_token="bulk", agent_token="agent")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            identity = uuid4()
            if workload == "P1":
                await gateway.session(identity, connected=True)
            payload = request(config, workload=workload, context_id=identity).model_dump_json()
            for token in ("foreground", "bulk"):
                response = await client.post(
                    "/v1/generate", content=payload, headers={"Authorization": f"Bearer {token}"}
                )
                assert response.status_code == 403
            response = await client.post(
                "/v1/generate", content=payload, headers={"Authorization": "Bearer agent"}
            )
            assert response.status_code == 200
            assert json.loads(response.text.splitlines()[-1])["kind"] == "end"
            assert upstream.count == 1
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("fault", ["model", "budget", "deadline", "pin", "uncertain"])
def test_bad_admission_never_generates(tmp_path: Path, fault: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream()
        gateway = P0Gateway(config, upstream, uncertain=fault == "uncertain")
        updates: dict[str, object] = {}
        if fault == "model":
            updates["model"] = "other"
        elif fault == "budget":
            updates["messages"] = (PromptMessage(role="user", content="x" * 7000),)
        elif fault == "deadline":
            updates["deadline_at"] = "2000-01-01T00:00:00.000000Z"
        elif fault == "pin":
            upstream.fail = True
        with pytest.raises(GatewayError):
            await gateway.admit(request(config, **updates))
        assert upstream.count == 0
        await gateway.aclose()

    asyncio.run(run())


def test_subscriber_detach_keeps_runner_owned(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream()
        upstream.release.clear()
        gateway = P0Gateway(config, upstream)
        value = request(config)
        job = await gateway.admit(value)
        iterator = job.events()
        assert (await anext(iterator)).kind == "start"
        await iterator.aclose()
        with pytest.raises(GatewayError, match="busy"):
            await gateway.admit(request(config))
        assert gateway.active is job and not job.task.done() if job.task else False
        upstream.release.set()
        assert job.task is not None
        await job.task
        assert gateway.active is None and not gateway.uncertain
        with pytest.raises(GatewayError, match="duplicate_request"):
            await gateway.admit(value)
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["eof", "timeout", "cancel"])
def test_unknown_runner_completion_fails_closed(tmp_path: Path, mode: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream((NativeFrame(message=NativeMessage(content="partial")),))
        gateway = P0Gateway(config, upstream)
        value = request(config)
        if mode == "timeout":
            upstream.release.clear()
            value = request(
                config,
                deadline_at=(datetime.now(UTC) + timedelta(milliseconds=30))
                .isoformat()
                .replace("+00:00", "Z"),
            )
        if mode == "cancel":
            upstream.release.clear()
        job = await gateway.admit(value)
        assert job.task is not None
        if mode == "cancel":
            await upstream.started.wait()
            job.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job.task
        events = [event async for event in job.events()]
        assert events[-1].kind == "error" and gateway.uncertain
        with pytest.raises(GatewayError, match="quiescence_required"):
            await gateway.admit(request(config))
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("text", ["{bad", '{"ready":true}'])
def test_json_is_published_only_after_validation(tmp_path: Path, text: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream(
            (
                NativeFrame(message=NativeMessage(content=text[:2])),
                NativeFrame(message=NativeMessage(content=text[2:])),
                NativeFrame(done=True),
            )
        )
        gateway = P0Gateway(config, upstream)
        events = [
            event async for event in (await gateway.admit(request(config, output="json"))).events()
        ]
        if text == "{bad":
            assert events[-1].code == "invalid_structured_output"
            assert not any(event.kind == "text" for event in events)
        else:
            assert [event.text for event in events if event.kind == "text"] == [text]
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("argument", ['{"ready":true}', '{"ready":"wrong"}', '{"ready":'])
def test_complete_tool_schema_validation(tmp_path: Path, argument: str) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream(
            (
                NativeFrame(
                    message=NativeMessage(
                        tool_calls=[
                            NativeTool(function=NativeFunction(name="probe", arguments=argument))
                        ]
                    )
                ),
                NativeFrame(done=True),
            )
        )
        definition = ToolDefinition(
            function=ToolFunction(
                name="probe",
                parameters={
                    "type": "object",
                    "properties": {"ready": {"type": "boolean"}},
                    "required": ["ready"],
                },
            )
        )
        gateway = P0Gateway(config, upstream)
        events = [
            event
            async for event in (await gateway.admit(request(config, tools=(definition,)))).events()
        ]
        assert any(event.kind == "tools" for event in events) == (argument == '{"ready":true}')
        assert events[-1].kind == ("end" if argument == '{"ready":true}' else "error")
        await gateway.aclose()

    asyncio.run(run())


def test_authenticated_http_uses_same_gateway_and_safe_errors(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        gateway = P0Gateway(config, FakeUpstream())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(gateway, "token")), base_url="http://local"
        ) as http:
            assert (await http.get("/health")).status_code == 401
            bad = await http.post(
                "/v1/generate",
                headers={"Authorization": "Bearer token"},
                content='{"PRIVATE_SENTINEL":"SECRET"}',
            )
            assert bad.status_code == 422 and "SECRET" not in bad.text
            client = HttpGatewayClient("http://local", "token", client=http)
            assert (await client.health()).status == "ready"
            events = [event async for event in client.stream(request(config))]
            assert events[-1].kind == "end"
        await gateway.aclose()

    asyncio.run(run())


def test_gateway_restart_requires_quiescence_and_holds_os_lock(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    owner = ServiceOwner(tmp_path / "runtime")
    with pytest.raises(GatewayError, match="gateway_already_owned"):
        ServiceOwner(tmp_path / "runtime")
    asyncio.run(owner.record("running", request(config)))
    owner.close()
    reopened = ServiceOwner(tmp_path / "runtime")
    assert reopened.uncertain
    reopened.close()
    acknowledged = ServiceOwner(tmp_path / "runtime", acknowledge_quiescence=True)
    assert not acknowledged.uncertain
    acknowledged.close()


def test_native_adapter_pins_options_and_discards_thinking(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        bodies: list[dict[str, object]] = []

        def handler(value: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(value.content))
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "model": config.model_tag,
                        "message": {"content": "visible", "thinking": "PRIVATE_REASONING"},
                        "done": True,
                    }
                )
                + "\n",
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://local"
        ) as http:
            upstream = OllamaUpstream(config, client=http)
            frames = [frame async for frame in upstream.generate(request(config))]
            assert "PRIVATE_REASONING" not in frames[0].model_dump_json()
            assert bodies[0]["think"] is False
            assert bodies[0]["options"] == {"num_ctx": 8192, "num_predict": 512, "temperature": 0.2}

    asyncio.run(run())


@pytest.mark.parametrize(
    "chunks",
    [
        ("<analysis>", "hidden", "</analysis>visible"),
        ("before<think>hidden</think>after",),
        ("2 < 3 is true",),
    ],
)
def test_visible_filter(chunks: tuple[str, ...]) -> None:
    filtered = VisibleText()
    output = "".join(filtered.feed(chunk) for chunk in chunks)
    filtered.finish()
    assert "hidden" not in output


@pytest.mark.parametrize("version", ("0.1.0", "0.2.0", "0.3.0"))
def test_prefill_counter_is_opt_in_and_legacy_completion_json_is_unchanged(
    tmp_path: Path,
    version: str,
) -> None:
    config = configuration(tmp_path)
    upstream = FakeUpstream(
        (
            NativeFrame(message=NativeMessage(content="Visible")),
            NativeFrame(
                done=True, prompt_eval_count=12, eval_count=2, prompt_eval_duration=250000000
            ),
        )
    )
    gateway = P0Gateway(config, upstream)

    async def run() -> None:
        generation = await gateway.admit(request(config, version=version))
        events = [event async for event in generation.events()]
        usage = events[-1].usage
        assert usage is not None
        assert usage.prefill_seconds == (0.25 if version == "0.3.0" else None)
        assert ("prefill_seconds" in usage.model_dump()) == (version == "0.3.0")
        await gateway.aclose()

    asyncio.run(run())


def test_live_p1_runs_while_p2_waits_for_reservation(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream()
        gateway = P0Gateway(config, upstream)
        identity = uuid4()
        await gateway.session(identity, connected=True)
        try:
            with pytest.raises(GatewayError, match="live_reservation_required"):
                await gateway.admit(request(config, workload="P1"))
            background = await gateway.admit(request(config, workload="P2"))
            live = await gateway.admit(request(config, workload="P1", context_id=identity))
            async with asyncio.timeout(3):
                assert [event.kind async for event in live.events()][-1] == "end"
                assert upstream.count == 1
                assert (await gateway.health()).queued_p2 == 1
                await gateway.session(identity, connected=False)
                assert [event.kind async for event in background.events()][-1] == "end"
                assert upstream.count == 2
        finally:
            await gateway.aclose()

    asyncio.run(run())


def test_incomplete_p2_never_publishes_partial_text(tmp_path: Path) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream((NativeFrame(message=NativeMessage(content="partial")),))
        gateway = P0Gateway(config, upstream)
        try:
            job = await gateway.admit(request(config, workload="P2"))
            async with asyncio.timeout(3):
                events = [event async for event in job.events()]
            assert [event.kind for event in events if event.kind != "progress"] == [
                "start",
                "error",
            ]
        finally:
            await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("structured", [False, True])
def test_length_terminated_json_is_not_a_success_even_when_syntax_is_complete(
    tmp_path: Path, structured: bool
) -> None:
    async def run() -> None:
        config = configuration(tmp_path)
        upstream = FakeUpstream(
            (
                NativeFrame(message=NativeMessage(content='{"ready":true}')),
                NativeFrame(done=True, done_reason="length", prompt_eval_count=10, eval_count=2),
            )
        )
        gateway = P0Gateway(config, upstream)
        value = request(config, output="json" if structured else "text")
        events = [event async for event in (await gateway.admit(value)).events()]
        if structured:
            assert events[-1].kind == "error" and events[-1].code == "upstream_output_truncated"
            assert not any(event.kind in {"text", "tools", "end"} for event in events)
        else:
            assert events[-1].kind == "end"
        await gateway.aclose()

    asyncio.run(run())
