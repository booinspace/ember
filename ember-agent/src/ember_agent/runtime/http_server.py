"""Uvicorn embedded in Ember's asyncio runner."""

from collections.abc import Generator
from contextlib import contextmanager

import uvicorn


class EmbeddedServer(uvicorn.Server):
    """Leave process signals under the runtime runner's ownership."""

    @contextmanager
    def capture_signals(self) -> Generator[None]:
        yield
