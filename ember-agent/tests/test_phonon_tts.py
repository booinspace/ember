"""Synthetic synthesis proves offline voice bounds, conditioning and streamed PCM."""

import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from scripts.phonon_tts import Samples, create_app


class Model:
    voices = ["default", "Juniper"]

    def __init__(self) -> None:
        self.conditions: dict[str, str | float] = {}
        self.calls: list[tuple[str, str, dict[str, str | float]]] = []

    def set_conditions(self, conditions: dict[str, str | float]) -> None:
        self.conditions = conditions

    def add_voice(self, name: str, path: str) -> None:
        raise AssertionError("Synthesis must not load a voice.")

    @contextmanager
    def stream(self, text: str, *, voice: str) -> Iterator[Iterator[Samples]]:
        self.calls.append((text, voice, self.conditions.copy()))
        yield iter([np.array([-2, 0, 2], dtype=np.float32)])


def test_installed_voices_and_speed_are_applied_to_native_stream() -> None:
    model = Model()
    with TestClient(create_app(model)) as client:
        choices = client.get("/v1/audio/voices").json()
        assert choices["voices"] == model.voices
        assert choices["controls"] == "ember_voice_controls_v1"
        body = dict(model="tts-1", voice="Juniper", response_format="pcm", input="Synthetic.")
        fast = client.post("/v1/audio/speech", json={**body, "speed": 1.25})
        assert fast.status_code == 200
        assert np.frombuffer(fast.content, dtype="<i2").tolist() == [-32767, 0, 32767]
        assert model.calls[-1][:2] == ("Synthetic.", "Juniper")
        assert model.calls[-1][2]["duration_delta"] < 0
        client.post("/v1/audio/speech", json={**body, "speed": 0.8})
        assert model.calls[-1][2]["duration_delta"] > 0
        client.post("/v1/audio/speech", json=body)
        assert model.calls[-1][2] == {"duration_delta": 0.0}


def test_uninstalled_voices_and_invalid_rates_never_reach_native_synthesis() -> None:
    model = Model()
    with TestClient(create_app(model)) as client:
        body = dict(model="tts-1", voice="default", response_format="pcm", input="Synthetic.")
        assert (
            client.post("/v1/audio/speech", json={**body, "voice": "not-installed"}).status_code
            == 400
        )
        for speed in (0, 0.7, 1.6):
            assert client.post("/v1/audio/speech", json={**body, "speed": speed}).status_code == 422
        assert not model.calls


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_disconnected_stream_closes_native_context_on_same_thread_and_allows_next_request(
    spec_version: str,
) -> None:
    class Tracked(Model):
        closed = False
        worker: int | None = None

        @contextmanager
        def stream(self, text: str, *, voice: str) -> Iterator[Iterator[Samples]]:
            self.worker = threading.get_ident()
            try:
                yield iter([np.ones(240, dtype=np.float32)] * 10)
            finally:
                assert threading.get_ident() == self.worker
                self.closed = True

    async def exercise() -> None:
        model = Tracked()
        app = create_app(model)
        body = dict(model="tts-1", voice="Juniper", response_format="pcm", input="Synthetic.")
        sent_body = False
        first = asyncio.Event()

        async def receive() -> dict[str, object]:
            nonlocal sent_body
            if sent_body:
                await first.wait()
                return {"type": "http.disconnect"}
            sent_body = True
            return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

        async def send(message: dict[str, object]) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                first.set()
                if spec_version == "2.4":
                    raise OSError("Synthetic client disconnect")
                await asyncio.sleep(0)

        async with app.router.lifespan_context(app):
            from starlette.requests import ClientDisconnect

            with pytest.raises(ClientDisconnect) if spec_version == "2.4" else nullcontext():
                await app(
                    {
                        "type": "http",
                        "asgi": {"spec_version": spec_version},
                        "method": "POST",
                        "path": "/v1/audio/speech",
                        "query_string": b"",
                        "headers": [(b"content-type", b"application/json")],
                    },
                    receive,
                    send,
                )
            assert model.closed
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/v1/audio/speech", json=body)
                assert response.status_code == 200 and len(response.content) == 4800

    asyncio.run(exercise())
