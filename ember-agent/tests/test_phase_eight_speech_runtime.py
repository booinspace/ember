"""Synthetic CPU HTTP provider proves startup ownership; it does not prove Kokoro synthesis."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from ember_agent.runtime.config import load_config
from ember_agent.runtime.livekit_runtime import RuntimeServices, port_open
from ember_agent.runtime.processes import OwnedProcess

from .test_livekit_runtime import EXAMPLE, free_port

SERVER = """
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import sys
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/v1/audio/voices":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"voices":["Juniper"]}')
    def log_message(self, *args):
        pass
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(os.environ.get("CUDA_VISIBLE_DEVICES", ""), encoding="utf-8")
HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""


def test_managed_speech_starts_and_stops_only_original_child(tmp_path: Path) -> None:
    provider = tmp_path / "synthetic_provider.py"
    provider.write_text(SERVER, encoding="utf-8")
    port = free_port()
    config = load_config(EXAMPLE)
    config = config.model_copy(
        update={
            "runtime_root": tmp_path,
            "audio": config.audio.model_copy(update={"enabled": True}),
            "services": config.services.model_copy(
                update={
                    "tts": config.services.tts.model_copy(
                        update={
                            "endpoint": f"http://127.0.0.1:{port}",
                            "mode": "managed",
                            "executable": sys.executable,
                            "arguments": (str(provider), str(port), str(tmp_path / "device.txt")),
                            "working_directory": tmp_path,
                        }
                    )
                }
            ),
        }
    )

    async def exercise() -> None:
        runtime = RuntimeServices(config, EXAMPLE)
        try:
            assert await runtime.start_speech() == "ready"
            assert len(runtime.children) == 1
            assert (tmp_path / "device.txt").read_text(encoding="utf-8") == "-1"
            child = runtime.children[0]
            assert child.component == "tts" and child.process.poll() is None
        finally:
            await runtime.aclose()
        assert child.process.poll() is not None
        # The Windows job also owns the virtualenv launcher's native Python child.
        async with asyncio.timeout(2):
            while await asyncio.to_thread(port_open, config.services.tts.endpoint):
                await asyncio.sleep(0.01)
        await runtime.aclose()

    asyncio.run(exercise())


def test_verified_existing_speech_is_attached_and_not_stopped(tmp_path: Path) -> None:
    provider = tmp_path / "synthetic_provider.py"
    provider.write_text(SERVER, encoding="utf-8")
    port = free_port()
    child = OwnedProcess(
        [sys.executable, str(provider), str(port)],
        root=tmp_path,
        component="external-synthetic",
        instance=uuid4(),
    )
    config = load_config(EXAMPLE)
    config = config.model_copy(
        update={
            "audio": config.audio.model_copy(update={"enabled": True}),
            "services": config.services.model_copy(
                update={
                    "tts": config.services.tts.model_copy(
                        update={"endpoint": f"http://127.0.0.1:{port}", "mode": "managed"}
                    )
                }
            ),
        }
    )

    async def exercise() -> None:
        async with asyncio.timeout(5):
            while not await asyncio.to_thread(port_open, config.services.tts.endpoint):
                child.check()
                await asyncio.sleep(0.01)
        runtime = RuntimeServices(config, EXAMPLE)
        assert await runtime.start_speech() == "ready"
        assert runtime.children == []
        await runtime.aclose()
        assert child.process.poll() is None
        unavailable = config.model_copy(
            update={"audio": config.audio.model_copy(update={"tts_voice": "unknown"})}
        )
        assert await RuntimeServices(unavailable, EXAMPLE).start_speech() == (
            "tts_unavailable_text_output"
        )
        assert child.process.poll() is None

    try:
        asyncio.run(exercise())
    finally:
        child.stop()


def test_disabled_audio_never_starts_managed_speech() -> None:
    config = load_config(EXAMPLE)
    config = config.model_copy(
        update={
            "services": config.services.model_copy(
                update={"tts": config.services.tts.model_copy(update={"mode": "managed"})}
            )
        }
    )
    runtime = RuntimeServices(config, EXAMPLE)
    assert asyncio.run(runtime.start_speech()) == "disabled" and runtime.children == []


def test_speech_health_supports_named_voice_objects_without_matching_display_names() -> None:
    from unittest.mock import patch

    import httpx
    import pytest

    from ember_agent.inference.contracts import GatewayError
    from ember_agent.runtime.speech_health import check_voice

    responses = [
        {"voices": [{"id": "af_alloy", "name": "Alloy", "language": "en"}]},
        {"voices": [{"id": "unavailable", "name": "af_alloy"}]},
    ]
    client_type = httpx.AsyncClient

    async def exercise() -> None:
        for index, payload in enumerate(responses):
            transport = httpx.MockTransport(
                lambda request, payload=payload: httpx.Response(200, json=payload)
            )
            with patch(
                "ember_agent.runtime.speech_health.httpx.AsyncClient",
                return_value=client_type(transport=transport),
            ):
                if index == 0:
                    await check_voice("http://127.0.0.1:8880", "af_alloy")
                else:
                    with pytest.raises(GatewayError, match="tts_voice_unavailable"):
                        await check_voice("http://127.0.0.1:8880", "af_alloy")

    asyncio.run(exercise())
