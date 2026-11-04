"""Local provider composition degrades speech independently of character text and memory."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx
from livekit.agents import stt
from livekit.agents import vad as vad_models
from livekit.plugins import openai, silero
from openai import AsyncClient as SpeechTransport
from pydantic import ValidationError

from ..inference.contracts import GatewayError
from ..runtime.config import DeploymentConfig
from ..runtime.speech_health import check_voice
from ..runtime.speech_settings import SpeechOptions, SpeechSelection
from .phonon import PhononSTT
from .whisper import (
    MAX_SPEECH_SECONDS,
    PREFIX_PADDING_SECONDS,
    BoundedCpuSTT,
    CpuWhisperSTT,
)


async def _installed_inputs(
    config: DeploymentConfig,
) -> tuple[BoundedCpuSTT, vad_models.VAD]:
    """One owned startup task retains loaded STT until VAD succeeds or cleanup drains."""
    settings = config.audio
    recognizer: BoundedCpuSTT
    if settings.phonon_endpoint:
        recognizer = await asyncio.to_thread(
            PhononSTT.load, settings.phonon_endpoint, language=settings.language
        )
    else:
        assert settings.stt_model_directory is not None
        recognizer = await asyncio.to_thread(
            CpuWhisperSTT.load,
            settings.stt_model_directory,
            threads=settings.stt_threads,
            language=settings.language,
        )
    try:
        detector = await asyncio.to_thread(
            silero.VAD.load,
            force_cpu=True,
            sample_rate=16000,
            max_buffered_speech=MAX_SPEECH_SECONDS,
            prefix_padding_duration=PREFIX_PADDING_SECONDS,
        )
    except BaseException:
        await recognizer.aclose()
        raise
    return recognizer, detector


@dataclass
class VoiceComponents:
    """CPU STT/VAD and a configured local TTS adapter, with explicit provider lifecycle."""

    recognizer: BoundedCpuSTT
    streaming_stt: stt.StreamAdapter
    vad: vad_models.VAD
    tts: openai.TTS | None
    output_status: str
    transport: SpeechTransport | None = None
    speech_options: SpeechOptions | None = None

    @classmethod
    async def create(
        cls,
        config: DeploymentConfig,
        *,
        start_speech: Callable[[], Awaitable[str]] | None = None,
    ) -> tuple["VoiceComponents | None", str]:
        """Installed inputs load before optional TTS; cancellation drains all owned resources."""
        settings = config.audio
        if not settings.enabled:
            return None, "disabled"
        if settings.stt_model_directory is None and not settings.phonon_endpoint:
            return None, "stt_artifacts_not_configured"
        loading = asyncio.create_task(_installed_inputs(config), name="ember-speech-input-startup")
        try:
            recognizer, detector = await asyncio.shield(loading)
        except asyncio.CancelledError:
            try:
                recognizer, detector = await asyncio.shield(loading)
            except Exception:
                pass
            else:
                await recognizer.aclose()
            raise
        except (GatewayError, OSError, RuntimeError, ValueError):
            return None, "stt_artifacts_unavailable"
        async with AsyncExitStack() as cleanup:
            cleanup.push_async_callback(recognizer.aclose)
            provider = None
            transport = None
            speech_options = None
            output_status = "tts_disabled"
            if config.services.kokoro.mode != "disabled":
                try:
                    output_status = await start_speech() if start_speech else "ready"
                    if output_status == "ready":
                        speech_options = await check_voice(
                            config.services.kokoro.endpoint, settings.tts_voice
                        )
                        transport = SpeechTransport(
                            api_key="ember-local-kokoro",
                            base_url=config.services.kokoro.endpoint.rstrip("/") + "/v1",
                            max_retries=0,
                            http_client=httpx.AsyncClient(
                                timeout=10,
                                follow_redirects=False,
                                trust_env=False,
                                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                            ),
                        )
                        cleanup.push_async_callback(transport.close)
                        provider = openai.TTS(
                            model=settings.tts_model,
                            voice=settings.tts_voice,
                            response_format="pcm",
                            base_url=config.services.kokoro.endpoint.rstrip("/") + "/v1",
                            api_key="ember-local-kokoro",
                            client=transport,
                        )
                        cleanup.push_async_callback(provider.aclose)
                except (httpx.HTTPError, ValidationError, GatewayError, ValueError):
                    if transport is not None:
                        await transport.close()
                        transport = None
                    output_status = "tts_unavailable_text_output"
            adapter = stt.StreamAdapter(stt=recognizer, vad=detector)
            result = cls(
                recognizer, adapter, detector, provider, output_status, transport, speech_options
            )
            cleanup.pop_all()
            return result, "ready"

    def configure_speech(self, selection: SpeechSelection) -> SpeechOptions:
        """Apply validated session preferences between turns without model loads or downloads."""
        options = self.speech_options
        if options is None or self.tts is None:
            raise GatewayError("speech_controls_unavailable")
        if selection.voice not in options.voices:
            raise GatewayError("tts_voice_unavailable")
        self.tts.update_options(voice=selection.voice, speed=selection.speed)
        self.speech_options = options.model_copy(update={"selection": selection})
        return self.speech_options

    async def aclose(self) -> None:
        """Drain CPU speech and close only adapters; attached provider processes remain external."""
        async with AsyncExitStack() as cleanup:
            if self.transport is not None:
                cleanup.push_async_callback(self.transport.close)
            if self.tts is not None:
                cleanup.push_async_callback(self.tts.aclose)
            cleanup.push_async_callback(self.recognizer.aclose)
            cleanup.push_async_callback(self.streaming_stt.aclose)
