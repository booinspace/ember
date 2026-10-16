"""Bounded local speech readiness; no synthesis, prompt or model load is requested."""

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..inference.contracts import GatewayError


class _Voice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    id: str = Field(min_length=1, max_length=128)


class _Voices(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)
    voices: tuple[str | _Voice, ...] = Field(max_length=256)


async def check_voice(endpoint: str, voice: str) -> None:
    """Validate configured voice over loopback without redirects, proxies or unbounded bodies."""
    async with httpx.AsyncClient(timeout=2, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", endpoint.rstrip("/") + "/v1/audio/voices") as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 65536:
                    raise GatewayError("tts_health_bound")
                chunks.append(chunk)
            voices = _Voices.model_validate_json(b"".join(chunks))
            identifiers = tuple(
                item if isinstance(item, str) else item.id for item in voices.voices
            )
            if voice not in identifiers:
                raise GatewayError("tts_voice_unavailable")
