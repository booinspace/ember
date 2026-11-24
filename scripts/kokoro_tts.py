"""Adapt the installed Kokoro-FastAPI app to Ember's existing voice picker."""

import importlib
import os
from collections.abc import Awaitable, Callable
from typing import cast

from fastapi import FastAPI


def create_app(
    upstream: FastAPI, list_voices: Callable[[], Awaitable[dict[str, object]]]
) -> FastAPI:
    """Advertise voice controls and leave synthesis and preview with Kokoro."""
    app = FastAPI(lifespan=upstream.router.lifespan_context, docs_url=None, openapi_url=None)

    @app.get("/v1/audio/voices")
    async def voices() -> dict[str, object]:
        return {**await list_voices(), "model": "kokoro", "controls": "ember_voice_controls_v1"}

    app.mount("/", upstream)
    return app


def load_app() -> FastAPI:
    """Load the pinned provider from its own environment for loopback use by Ember."""
    os.environ.update(
        ENABLE_WEB_PLAYER="true", ALLOW_LOCAL_VOICE_SAVING="true", CORS_ENABLED="false"
    )
    upstream = cast(FastAPI, importlib.import_module("api.src.main").app)
    list_voices = cast(
        Callable[[], Awaitable[dict[str, object]]],
        importlib.import_module("api.src.routers.openai_compatible").list_voices,
    )
    return create_app(upstream, list_voices)
