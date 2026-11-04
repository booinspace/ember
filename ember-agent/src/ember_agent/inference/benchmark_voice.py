"""Headless SDK/TTS latency probe; the paced PCM sink is not a physical audio device."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from time import perf_counter

from livekit import rtc
from livekit.agents import Agent, AgentSession, APIConnectOptions
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.agents.voice.io import AudioOutput, AudioOutputCapabilities
from livekit.plugins import openai

from ..runtime.config import DeploymentConfig
from .contracts import GatewayError


class PacedOutput(AudioOutput):
    """Consume decoded PCM at its sample rate without retaining audio or transcripts."""

    def __init__(self) -> None:
        super().__init__(
            label="benchmark-paced-pcm", capabilities=AudioOutputCapabilities(pause=False)
        )
        self.first = asyncio.Event()
        self.first_at: float | None = None
        self.cleared_at: float | None = None
        self.position = 0.0

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        """Pace delivery so interruption includes the SDK's output cancellation path."""
        await super().capture_frame(frame)
        if self.first_at is None:
            self.first_at = perf_counter()
            self.first.set()
        duration = frame.samples_per_channel / frame.sample_rate
        await asyncio.sleep(duration)
        self.position += duration

    def flush(self) -> None:
        """Acknowledge completed sink segments to the SDK playout fence."""
        super().flush()
        if self._pending_playback_count:
            self.on_playback_finished(playback_position=self.position, interrupted=False)

    def clear_buffer(self) -> None:
        """Record sink cancellation separately from actual speaker/device silence."""
        self.cleared_at = perf_counter()
        super().flush()
        if self._pending_playback_count:
            self.on_playback_finished(playback_position=self.position, interrupted=True)


async def play(
    text: AsyncGenerator[str, None], config: DeploymentConfig, *, started: float, interrupt: bool
) -> tuple[float | None, float | None, float | None]:
    """Feed gateway text to the installed CPU TTS and measure first PCM and SDK clear."""
    provider = openai.TTS(
        model=config.audio.tts_model,
        voice=config.audio.tts_voice,
        speed=config.audio.tts_speed,
        response_format="pcm",
        api_key="ember-local-tts",
        base_url=config.services.tts.endpoint.rstrip("/") + "/v1",
    )
    session: AgentSession[object] = AgentSession(
        tts=provider,
        conn_options=SessionConnectOptions(
            tts_conn_options=APIConnectOptions(max_retry=0, timeout=10)
        ),
    )
    sink = PacedOutput()
    session.output.audio = sink
    cancelled_at: float | None = None
    try:
        # The SDK overload's unused capture_run result has an unspecified generic.
        await session.start(  # pyright: ignore[reportUnknownMemberType]
            Agent(instructions="Synthetic benchmark."), record=False, session_host=False
        )
        async with aclosing(text):
            speech = session.say(text, allow_interruptions=True, add_to_chat_ctx=False)
            async with asyncio.timeout(180):
                if interrupt:
                    await sink.first.wait()
                    cancelled_at = perf_counter()
                    await session.interrupt(force=True)
                await speech.wait_for_playout()
                if speech.exception() is not None:
                    raise GatewayError("benchmark_speech_failed")
        return (
            sink.first_at - started if sink.first_at is not None else None,
            cancelled_at,
            max(0.0, sink.cleared_at - cancelled_at)
            if sink.cleared_at is not None and cancelled_at is not None
            else None,
        )
    finally:
        try:
            async with asyncio.timeout(10):
                await session.aclose()
        finally:
            await provider.aclose()
