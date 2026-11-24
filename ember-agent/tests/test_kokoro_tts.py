"""The Kokoro launcher keeps upstream lifecycle and speech while advertising saved voices."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from scripts.kokoro_tts import create_app


def test_kokoro_picker_lists_saved_blend_and_streams_through_upstream() -> None:
    running = False
    pcm = b"\x00\x00\x00\x01"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal running
        running = True
        yield
        running = False

    upstream = FastAPI(lifespan=lifespan)

    @upstream.get("/v1/audio/voices")
    async def list_voices() -> dict[str, object]:
        assert running
        return {"voices": [{"id": "bf_emma"}, {"id": "bf_ember"}]}

    @upstream.post("/v1/audio/speech")
    async def synthesize(request: Request) -> Response:
        assert running
        assert (await request.json())["voice"] == "bf_ember"
        return Response(pcm, media_type="audio/pcm")

    with TestClient(create_app(upstream, list_voices)) as client:
        voices = client.get("/v1/audio/voices").json()
        assert voices == {
            "voices": [{"id": "bf_emma"}, {"id": "bf_ember"}],
            "model": "kokoro",
            "controls": "ember_voice_controls_v1",
        }
        response = client.post("/v1/audio/speech", json={"voice": "bf_ember"})
        assert response.status_code == 200
        assert response.content == pcm
    assert not running
