"""Serve pinned local Phonon weights through Ember's loopback PCM TTS contract."""

import argparse
import importlib
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol, cast

import numpy as np
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

type Samples = np.ndarray[tuple[int], np.dtype[np.float32]]


class _Speech(Protocol):
    def __iter__(self) -> Iterator[Samples]: ...


class _Model(Protocol):
    def stream(self, text: str) -> AbstractContextManager[_Speech]: ...


class _Factory(Protocol):
    def __call__(
        self, model: str, *, lang: str, quant: str, device: str, voice_catalog: str
    ) -> _Model: ...


class SpeechRequest(BaseModel):
    """Accept only the bounded model, voice, and PCM format Ember uses."""

    model: Literal["tts-1"]
    voice: Literal["default"]
    response_format: Literal["pcm"]
    input: str = Field(min_length=1, max_length=4096, repr=False)
    speed: float = Field(default=1.0, ge=1.0, le=1.0)


def create_app(model: _Model) -> FastAPI:
    """Keep one native stream active and expose the existing voice readiness probe."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    slot = Lock()

    @app.get("/v1/audio/voices")
    async def voices() -> dict[str, list[str]]:
        return {"voices": ["default"]}

    @app.post("/v1/audio/speech")
    async def speech(request: SpeechRequest) -> StreamingResponse:
        def audio() -> Iterator[bytes]:
            with slot, model.stream(request.input) as chunks:
                for samples in chunks:
                    yield np.rint(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()

        return StreamingResponse(audio(), media_type="application/octet-stream")

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
