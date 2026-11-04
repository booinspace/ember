"""One OpenAI-compatible adapter supports provider-specific models, voices and config pace."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from openai import AsyncClient

from ember_agent.audio.components import VoiceComponents
from ember_agent.audio.whisper import CpuWhisperSTT
from ember_agent.runtime.config import load_config
from ember_agent.runtime.speech_settings import SpeechOptions, SpeechSelection

from .test_livekit_runtime import EXAMPLE


@pytest.mark.parametrize(
    "model,voice,speed", [("tts-1", "Juniper", 1.1), ("kokoro", "af_alloy", 1.0)]
)
def test_compatible_tts_adapter_uses_configured_model_voice_and_pace(
    model: str,
    voice: str,
    speed: float,
) -> None:
    config = load_config(EXAMPLE)
    config = config.model_copy(
        update={
            "audio": config.audio.model_copy(
                update={
                    "enabled": True,
                    "tts_model": model,
                    "tts_voice": voice,
                    "tts_speed": speed,
                    "stt_model_directory": EXAMPLE.parent,
                }
            )
        }
    )
    requests: list[dict[str, object]] = []
    unused: list[httpx.AsyncClient] = []

    def synthesize(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/speech"
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=b"\x00\x00" * 12000,
            headers={"content-type": "audio/pcm", "x-request-id": "synthetic"},
        )

    def transport(**settings: object) -> AsyncClient:
        client = settings["http_client"]
        assert isinstance(client, httpx.AsyncClient)
        unused.append(client)
        settings["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(synthesize))
        return AsyncClient(**settings)

    async def exercise() -> None:
        recognizer = CpuWhisperSTT(Mock())
        options = (
            SpeechOptions(
                model="gradium/phonon-31m-en",
                voices=(voice, "other"),
                selection=SpeechSelection(voice=voice, speed=speed),
            )
            if model == "tts-1"
            else None
        )
        health = AsyncMock(return_value=options)
        with (
            patch(
                "ember_agent.audio.components._installed_inputs",
                AsyncMock(return_value=(recognizer, Mock())),
            ),
            patch("ember_agent.audio.components.check_voice", health),
            patch("ember_agent.audio.components.SpeechTransport", transport),
        ):
            voice_components, status = await VoiceComponents.create(config)
        assert (
            voice_components is not None and voice_components.tts is not None and status == "ready"
        )
        try:
            health.assert_awaited_once_with(config.services.tts.endpoint, voice, speed=speed)
            if options:
                assert voice_components.speech_options.selection.speed == 1.1
            async with voice_components.tts.synthesize("Synthetic speech.") as stream:
                frames = [event.frame async for event in stream]
            assert frames and requests[-1]["model"] == model
            assert requests[-1]["voice"] == voice and requests[-1]["speed"] == speed
            if options:
                voice_components.configure_speech(SpeechSelection(voice="other", speed=1.25))
                async with voice_components.tts.synthesize("Another synthetic sentence.") as stream:
                    _ = [event async for event in stream]
                assert requests[-1]["voice"] == "other" and requests[-1]["speed"] == 1.25
                assert config.audio.tts_voice == "Juniper" and config.audio.tts_speed == 1.1
        finally:
            await voice_components.aclose()
            for client in unused:
                await client.aclose()

    asyncio.run(exercise())
