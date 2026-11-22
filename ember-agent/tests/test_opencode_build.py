"""Run OpenCode Build in a pinned local image, substituting only model generation."""

import asyncio
import os
import shlex
import shutil
from collections.abc import AsyncGenerator
from io import BytesIO
from pathlib import Path
from textwrap import dedent
from uuid import uuid4
from zipfile import ZipFile

import httpx
import pytest
from pydantic import JsonValue

from ember_agent.inference.contracts import GatewayRequest
from ember_agent.inference.upstream import NativeFrame, NativeMessage
from ember_agent.observability import ActivityLog
from ember_agent.runtime.config import BuildSettings, HarnessSettings
from ember_agent.runtime.opencode_harness import open_build
from ember_agent.runtime.task_router import (
    TaskRouter,
    TextInspectionProvider,
    TextInspectionTask,
    ToolRouter,
)

from .test_openai_bridge import (  # pyright: ignore[reportPrivateUsage]
    _bridge,
    _call,
    _config,
    _Upstream,
)

_GOAL = "Build and check a synthetic page. Serve it only if Ember grants a port."
_SUMMARY = "Built and checked the synthetic page."
_PAGE = b"<h1>Synthetic Build fixture 42</h1>\n"
_SOURCE = (
    "from pathlib import Path\n"
    'Path("index.html").write_text(f"<h1>Synthetic Build fixture {6 * 7}</h1>\\n")\n'
)
_CHECK = (
    f"from pathlib import Path; assert Path('index.html').read_bytes() == {_PAGE!r}; "
    "print('build check passed')"
)
_TOOLS = {"read", "glob", "grep", "write", "edit", "shell"}


def _start_service(port: int) -> str:
    # Bind before forking so returning from shell proves the service is ready. The
    # detached child has no inherited shell pipes for OpenCode to wait on.
    serve = dedent(f"""\
        from http.server import HTTPServer, SimpleHTTPRequestHandler
        import os

        with HTTPServer(('127.0.0.1', {port}), SimpleHTTPRequestHandler) as server:
            if os.fork() == 0:
                os.setsid()
                with open('/dev/null', 'r+') as null:
                    for fd in (0, 1, 2):
                        os.dup2(null.fileno(), fd)
                server.serve_forever()
        print('build service bound')
        """)
    return f"python3 -c {shlex.quote(serve)}"


class _BuildUpstream(_Upstream):
    def __init__(self, service_port: int | None) -> None:
        super().__init__()
        self.service_port = service_port
        self.steps: list[tuple[str, dict[str, JsonValue]]] = [
            ("write", {"path": "/work/build.py", "content": _SOURCE}),
            ("shell", {"command": "python3 build.py", "workdir": "/work"}),
            ("shell", {"command": f"python3 -c {shlex.quote(_CHECK)}", "workdir": "/work"}),
        ]
        if service_port is not None:
            self.steps.append(("shell", {"command": _start_service(service_port)}))

    async def generate(self, request: GatewayRequest) -> AsyncGenerator[NativeFrame, None]:
        self.requests.append(request)
        assert request.workload == "P2"
        assert {tool.function.name for tool in request.tools} == _TOOLS
        grant = (
            "No service port granted."
            if self.service_port is None
            else f"127.0.0.1:{self.service_port}"
        )
        (goal,) = (message.content for message in request.messages if message.role == "user")
        assert grant in goal and goal.endswith(_GOAL)
        history = [message for message in request.messages if message.role == "tool"]
        calls = [call for message in request.messages for call in message.tool_calls]
        step = len(history)
        assert [result.tool_call_id for result in history] == [call.call_id for call in calls]
        assert [call.name for call in calls] == [name for name, _ in self.steps[:step]]
        assert step == len(self.requests) - 1
        if step >= 3:
            assert "build check passed" in history[2].content
        if step == 4:
            assert "build service bound" in history[3].content
        if step < len(self.steps):
            yield _call(*self.steps[step])
            reason = "tool_calls"
        else:
            yield NativeFrame(message=NativeMessage(content=_SUMMARY))
            reason = "stop"
        yield NativeFrame(done=True, done_reason=reason, prompt_eval_count=400, eval_count=20)


@pytest.mark.integration
@pytest.mark.parametrize("service_port", [None, 12345], ids=["artifact", "http-preview"])
def test_pinned_opencode_build(tmp_path: Path, service_port: int | None) -> None:
    image = os.environ.get("EMBER_TEST_BUILD_IMAGE")
    if not image:
        pytest.skip("set EMBER_TEST_BUILD_IMAGE to an installed OpenCode 2.3.2 image ID")
    docker = shutil.which("docker")
    assert docker, "EMBER_TEST_BUILD_IMAGE requires Docker on PATH"
    settings = HarnessSettings(
        provider="opencode",
        command=(str(tmp_path / "unused-inspect-executable"),),
        revision="2.3.2",
        profile_sha256="0" * 64,
        workspace=tmp_path / "unused-inspect-workspace",
        build=BuildSettings(docker=Path(docker), image=image),
    )
    config = _config(tmp_path).model_copy(
        update={"context_tokens": 32768, "max_input_tokens": 16384}
    )
    config.runtime_root.mkdir(parents=True, exist_ok=True)

    async def run() -> None:
        upstream = _BuildUpstream(service_port)
        _, gateway, model = _bridge(config, upstream)
        observed: list[GatewayRequest] = []
        activity = ActivityLog()
        character = uuid4()
        native = TextInspectionTask(ToolRouter(TextInspectionProvider(), character))
        router = TaskRouter(config.runtime_root, character, uuid4(), native, activity=activity)
        try:
            async with open_build(
                config,
                settings,
                model,
                router.build_artifacts,
                router.build_services,
                observe=observed.append,
            ) as provider:
                router.providers["build"] = provider
                router.build_health = lambda: provider.available
                await router.start()
                try:
                    task = await router.submit(
                        "build", _GOAL, operation="build", service_port=service_port
                    )
                    revision = None
                    async with asyncio.timeout(75):
                        while True:
                            events = await router.notifications(revision)
                            if result := next(
                                (item for item in events.items if item.task_id == task.task_id),
                                None,
                            ):
                                break
                            revision = events.revision
                    assert result.state == "succeeded", (
                        result.result,
                        [event.code for event in activity.since(0)],
                        [
                            message.content
                            for request in upstream.requests[-1:]
                            for message in request.messages
                            if message.role == "tool"
                        ],
                    )
                    assert result.operation == "build" and result.result == _SUMMARY
                    assert result.build_artifact
                    receipt = (await router.receipt(task.task_id)).receipt
                    assert receipt and receipt.tool == "build" and receipt.code is None
                    assert receipt.data["tools"] == [
                        {"name": name, "status": "completed"} for name, _ in upstream.steps
                    ]
                    assert len(observed) == len(upstream.requests) == len(upstream.steps) + 1
                    assert [r.request_id for r in observed] == [
                        r.request_id for r in upstream.requests
                    ]
                    assert {r.context_id for r in observed} == {task.task_id}
                    assert {r.workload for r in observed} == {"P2"}
                    archive = await router.read_build_artifact(task.task_id)
                    with ZipFile(BytesIO(archive)) as files:
                        assert files.namelist() == ["build.py", "index.html"]
                        assert files.read("build.py") == _SOURCE.encode()
                        assert files.read("index.html") == _PAGE

                    service = await router.active_build_service()
                    if service_port is None:
                        assert service is None
                    else:
                        assert receipt.data["service_verified"] is True
                        assert service and service.task_id == task.task_id
                        url = httpx.URL(service.url)
                        assert url.scheme == "http" and url.host.endswith(".localhost") and url.port
                        # Resolve locally without depending on OS wildcard localhost DNS.
                        address = url.copy_with(host="127.0.0.1")
                        headers = {"Host": f"{url.host}:{url.port}"}
                        async with httpx.AsyncClient(trust_env=False) as probe:
                            response = await probe.get(address, headers=headers)
                            assert response.status_code == 200 and response.content == _PAGE
                            await router.stop_build_service(task.task_id)
                            with pytest.raises(httpx.ConnectError):
                                await probe.get(address, headers=headers)
                        assert await router.active_build_service() is None
                        assert await router.read_build_artifact(task.task_id) == archive
                finally:
                    await router.aclose()
        finally:
            await model.aclose()
            await gateway.aclose()

    asyncio.run(run())
