"""Independent reproductions for process ownership and the declared worker protocol."""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from time import time
from uuid import uuid4

import psutil
import pytest
from pydantic import JsonValue

from ember_agent.runtime.searx_tools import public_url
from ember_agent.runtime.worker_process import owned_stdio


def test_launcher_gets_native_install_paths_without_ambient_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = {"APPDATA": str(tmp_path / "roaming"), "LOCALAPPDATA": str(tmp_path / "local")}
    for name, value in paths.items():
        monkeypatch.setenv(name, value)
    for name in ("OPENAI_API_KEY", "EMBER_VLLM_TOKEN", "HERMES_HOME"):
        monkeypatch.setenv(name, "synthetic-must-not-inherit")

    async def run() -> None:
        names = (*paths, "OPENAI_API_KEY", "EMBER_VLLM_TOKEN", "HERMES_HOME")
        code = f"import json,os; print(json.dumps({{k:os.environ.get(k) for k in {names!r}}}))"
        async with owned_stdio((sys.executable, "-c", code)) as process:
            assert process.stdout is not None
            values = json.loads(await process.stdout.readline())
            assert {name: values[name] for name in paths} == paths
            assert all(values[name] is None for name in names if name not in paths)

    asyncio.run(run())


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group ownership regression.")
def test_exited_launcher_does_not_leave_its_child_running() -> None:
    async def run() -> None:
        child: psutil.Process | None = None
        try:
            code = (
                "import subprocess,sys; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                "print(child.pid,flush=True)"
            )
            async with owned_stdio((sys.executable, "-c", code)) as process:
                assert process.stdout is not None
                child = psutil.Process(int(await process.stdout.readline()))
            try:
                assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
            except psutil.NoSuchProcess:
                pass
        finally:
            if child is not None:
                try:
                    if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                        child.kill()
                    child.wait(timeout=3)
                except psutil.NoSuchProcess:
                    pass

    asyncio.run(run())


def test_repeated_cancellation_reaps_the_owned_worker_before_returning() -> None:
    async def run() -> None:
        started = asyncio.Event()
        worker: psutil.Process | None = None

        async def execute() -> None:
            nonlocal worker
            async with owned_stdio(
                (
                    sys.executable,
                    "-c",
                    "import os,time; print(os.getpid(),flush=True); time.sleep(30)",
                )
            ) as process:
                assert process.stdout is not None
                worker = psutil.Process(int(await process.stdout.readline()))
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(execute())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert worker is not None and not worker.is_running()
        finally:
            if worker is not None and worker.is_running():
                worker.kill()
                worker.wait(timeout=3)

    asyncio.run(run())


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost./x",
        "http://127.1/x",
        "http://127.0.0.1./x",
        "http://0177.0.0.1/x",
        "https://public.example:bad/x",
        "https://public.example:99999/x",
    ],
)
def test_public_citations_reject_local_aliases_and_invalid_ports(url: str) -> None:
    assert not public_url(url)


@pytest.mark.parametrize("field, value", [("purged", "1"), ("returncode", "False")])
def test_purge_receipt_requires_exact_boolean_and_integer_types(
    tmp_path: Path, field: str, value: str
) -> None:
    fixture = Path(__file__).parent / "fixtures/hermes_protocol_worker.py"
    wrapper = tmp_path / "wrong_purge_type.py"
    wrapper.write_text(
        "import json,runpy\n"
        "original=json.dumps\n"
        "def dumps(value,*args,**kwargs):\n"
        " if isinstance(value,dict) and value.get('type')=='stopped':\n"
        f"  value={{**value,{field!r}:{value}}}\n"
        " return original(value,*args,**kwargs)\n"
        "json.dumps=dumps\n"
        f"runpy.run_path({str(fixture)!r})\n"
    )
    asyncio.run(_reject_invalid_purge(tmp_path, wrapper))


async def _reject_invalid_purge(tmp_path: Path, wrapper: Path) -> None:
    from ember_agent.inference.contracts import GatewayEvent, GatewayRequest, GenerationUsage
    from ember_agent.runtime.config import HermesSettings, load_config
    from ember_agent.runtime.hermes_tasks import HermesTask
    from ember_agent.runtime.task_router import ToolContext, ToolReceipt, ToolRouter
    from ember_agent.runtime.task_store import TaskConflict, TaskState, TaskSubmission

    class Model:
        async def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayEvent]:
            for sequence, kind in enumerate(("start", "text", "end")):
                yield GatewayEvent(
                    request_id=request.request_id,
                    context_id=request.context_id,
                    sequence=sequence,
                    kind=kind,
                    text="Synthetic" if kind == "text" else "",
                    usage=GenerationUsage(
                        prompt_tokens=1,
                        output_tokens=1,
                        total_seconds=0.0,
                        first_visible_seconds=0.0,
                        tokens_per_second=None,
                    )
                    if kind == "end"
                    else None,
                )

    class Tools:
        capabilities = frozenset({"searx_search"})

        async def invoke(
            self, name: str, arguments: dict[str, JsonValue], context: ToolContext
        ) -> ToolReceipt:
            return ToolReceipt(
                context=context,
                tool=name,
                data={
                    "results": [
                        {
                            "id": "s1",
                            "url": "https://docs.searxng.org/dev/search_api.html",
                            "title": "Search API",
                            "snippet": "GET and POST queries",
                        }
                    ]
                },
            )

    example = Path(__file__).parents[1] / "config/deployment.example.toml"
    path = tmp_path / "deployment.toml"
    path.write_text(
        example.read_text()
        .replace('tag = ""', 'tag = "synthetic:27b"')
        .replace('expected_digest = ""', 'expected_digest = "' + "a" * 64 + '"')
    )
    config = load_config(path, require_model_pin=True).model_copy(
        update={
            "hermes": HermesSettings(
                command=(sys.executable, str(wrapper), "normal", str(tmp_path / "worker.pid")),
                revision="71736c9f32597bbca9d89cede5aa136e55946b2f",
                profile_sha256="a" * 64,
                search_endpoint="http://installed.invalid",
            )
        }
    )
    context = ToolContext(
        character_id=uuid4(),
        session_id=uuid4(),
        turn_id=uuid4(),
        task_id=uuid4(),
        deadline_at=time() + 8,
    )

    async def progress(state: TaskState) -> None:
        pass

    provider = HermesTask(config, Model(), ToolRouter(Tools(), context.character_id))
    with pytest.raises(TaskConflict, match="purge_unverified"):
        await provider.execute(
            TaskSubmission(
                character_id=context.character_id,
                session_id=context.session_id,
                turn_id=context.turn_id,
                goal="Find official search documentation",
                idempotency_key="purge",
                operation="delegate",
            ),
            context,
            progress,
        )
    assert not psutil.pid_exists(int((tmp_path / "worker.pid").read_text()))
