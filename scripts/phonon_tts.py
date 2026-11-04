"""Serve pinned local Phonon weights through Ember's loopback PCM TTS contract."""

import argparse
import asyncio
import importlib
from _thread import LockType
from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager, asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol, cast

import anyio
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.types import Receive, Scope, Send

type Samples = np.ndarray[tuple[int], np.dtype[np.float32]]


class _Speech(Protocol):
    def __iter__(self) -> Iterator[Samples]: ...


class _Model(Protocol):
    @property
    def voices(self) -> list[str]: ...

    def set_conditions(self, conditions: dict[str, str | float]) -> None: ...

    def add_voice(self, name: str, path: str) -> None: ...

    def stream(self, text: str, *, voice: str) -> AbstractContextManager[_Speech]: ...


class _Factory(Protocol):
    def __call__(
        self, model: str, *, lang: str, quant: str, device: str, voice_catalog: str
    ) -> _Model: ...


class SpeechRequest(BaseModel):
    """Accept only the bounded model, voice, and PCM format Ember uses."""

    model: Literal["tts-1"]
    voice: str = Field(min_length=1, max_length=128)
    response_format: Literal["pcm"]
    input: str = Field(min_length=1, max_length=4096, repr=False)
    speed: float = Field(default=1.0, ge=0.75, le=1.5, allow_inf_nan=False)


class SpeechResponse(StreamingResponse):
    """Close native synthesis on its owned thread even if HTTP streaming disconnects."""

    def __init__(
        self, chunks: Generator[bytes, None, None], worker: ThreadPoolExecutor, slot: LockType
    ) -> None:
        self.chunks, self.worker, self.slot = chunks, worker, slot
        self.cleanup: Future[None] | None = None

        async def audio() -> AsyncIterator[bytes]:
            while True:
                chunk = await asyncio.wrap_future(worker.submit(next, chunks, None))
                if chunk is None:
                    # Release before HTTP EOF so the next sentence cannot race cleanup.
                    await self._close()
                    return
                yield chunk

        super().__init__(audio(), media_type="application/octet-stream")

    async def _close(self) -> None:
        def close() -> None:
            try:
                # The response only accepts the owned generator constructed below.
                self.chunks.close()
            finally:
                self.slot.release()

        # Queue close behind any in-flight native next(); never close it on another thread.
        if self.cleanup is None:
            self.cleanup = self.worker.submit(close)
        with anyio.CancelScope(shield=True):
            await asyncio.shield(asyncio.wrap_future(self.cleanup))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._close()


def create_app(model: _Model) -> FastAPI:
    """Keep one native stream active and expose the existing voice readiness probe."""
    slot = Lock()
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ember-phonon-tts")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        del app
        try:
            yield
        finally:
            await asyncio.to_thread(worker.shutdown, wait=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/v1/audio/voices")
    async def voices() -> dict[str, object]:
        return {
            "voices": model.voices,
            "model": "gradium/phonon-31m-en",
            "controls": "ember_voice_controls_v1",
        }

    @app.post("/v1/audio/speech")
    async def speech(request: SpeechRequest) -> StreamingResponse:
        if request.voice not in model.voices:
            raise HTTPException(status_code=400, detail="voice_not_installed")
        if not slot.acquire(blocking=False):
            raise HTTPException(status_code=503, detail="speech_busy")

        def audio() -> Generator[bytes, None, None]:
            # Phonon's duration conditioning changes pace without changing PCM pitch/rate.
            model.set_conditions({"duration_delta": 1 / request.speed - 1})
            with model.stream(request.input, voice=request.voice) as chunks:
                for samples in chunks:
                    yield np.rint(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()

        return SpeechResponse(audio(), worker, slot)

    return app


def main() -> None:
    """Load explicit local weights and bind only to the loopback interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    args = parser.parse_args()
    model = cast(Path, args.model).resolve()
    if not all(
        (model / file).is_file()
        for file in (
            "config.json",
            "tokenizer.json",
            "model.q8.gguf",
            "default-voice.safetensors",
        )
    ):
        parser.error("Provision the local Phonon model before starting Ember.")
    factory = cast(_Factory, importlib.import_module("ptts").TTS)
    tts = factory(str(model), lang="en", quant="q8", device="cpu", voice_catalog="")
    for voice in sorted((model / "voices").glob("*.safetensors")):
        tts.add_voice(voice.stem, str(voice))
    uvicorn.run(
        create_app(tts),
        host="127.0.0.1",
        port=8880,
        access_log=False,
        log_level="warning",
        limit_concurrency=8,
    )


if __name__ == "__main__":
    main()
