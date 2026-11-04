"""Ollama generation adapter; callers remain behind the owned gateway."""

from collections.abc import AsyncIterator

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from ..runtime.config import DeploymentConfig
from .contracts import GatewayError, GatewayRequest
from .frames import bounded_lines
from .upstream import (
    BackendModel as BackendModel,
)
from .upstream import (
    NativeFrame as NativeFrame,
)
from .upstream import (
    NativeFunction as NativeFunction,
)
from .upstream import (
    NativeMessage as NativeMessage,
)
from .upstream import (
    NativeTool as NativeTool,
)
from .upstream import (
    Upstream as Upstream,
)


class OllamaUpstream:
    """One local origin/model; options are pinned by deployment rather than caller JSON."""

    def __init__(self, config: DeploymentConfig, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self.client = client or httpx.AsyncClient(
            base_url=config.services.ollama.endpoint,
            timeout=httpx.Timeout(180, connect=3),
            trust_env=False,
        )

    async def verify(self, *, loaded: bool = False) -> None:
        """Fresh metadata check before admission, plus loaded digest verification at completion."""
        route = "/api/ps" if loaded else "/api/tags"
        try:
            response = await self.client.get(route, timeout=3)
            response.raise_for_status()
            adapter: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
            value = adapter.validate_json(response.content)
            models = value.get("models")
            if not isinstance(models, list):
                raise GatewayError("invalid_upstream_metadata")
            matches = [
                item
                for item in models
                if isinstance(item, dict) and item.get("name") == self.config.model_tag
            ]
            expected = self.config.model_digest.removeprefix("sha256:")
            if len(matches) != 1 or matches[0].get("digest") != expected:
                raise GatewayError("model_pin_mismatch")
            if loaded and matches[0].get("context_length") != self.config.context_tokens:
                raise GatewayError("runner_context_mismatch")
        except (httpx.HTTPError, ValidationError) as error:
            raise GatewayError("upstream_unavailable") from error

    async def generate(self, request: GatewayRequest) -> AsyncIterator[NativeFrame]:
        """Use configured runner bounds; thinking is an explicit foreground display option."""
        body: dict[str, JsonValue] = {
            "model": self.config.model_tag,
            "messages": [{"role": item.role, "content": item.content} for item in request.messages],
            "stream": True,
            "think": request.thinking,
            "keep_alive": "5m",
            "options": {
                "num_ctx": self.config.context_tokens,
                "num_predict": request.max_output_tokens,
                "temperature": 0.2,
            },
        }
        if request.output == "json":
            body["format"] = request.output_schema if request.output_schema is not None else "json"
        if request.tools:
            adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
            body["tools"] = adapter.validate_python(
                [tool.model_dump(mode="json") for tool in request.tools]
            )
        try:
            async with self.client.stream("POST", "/api/chat", json=body) as response:
                response.raise_for_status()
                async for line in bounded_lines(response):
                    if not line:
                        continue
                    if len(line) > 1024 * 1024:
                        raise GatewayError("upstream_frame_too_large")
                    try:
                        frame = NativeFrame.model_validate_json(line)
                    except ValidationError as error:
                        raise GatewayError("invalid_upstream_stream") from error
                    if frame.error is not None:
                        raise GatewayError("upstream_generation_failed")
                    if frame.model is not None and frame.model != self.config.model_tag:
                        raise GatewayError("upstream_model_mismatch")
                    if not request.thinking and frame.message.thinking:
                        frame = frame.model_copy(
                            update={"message": frame.message.model_copy(update={"thinking": ""})}
                        )
                    yield frame
        except httpx.HTTPError as error:
            raise GatewayError("upstream_transport_failed") from error

    async def aclose(self) -> None:
        """Do not stop or unload the independently managed Ollama service."""
        await self.client.aclose()
