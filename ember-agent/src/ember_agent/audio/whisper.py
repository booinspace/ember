"""CPU-only faster-whisper with bounded admission and explicit owned native completion."""

import asyncio
import importlib
from abc import abstractmethod
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import numpy as np
from livekit import rtc
from livekit.agents import APIConnectOptions, stt
from livekit.agents.language import LanguageCode
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer

from ..inference.contracts import GatewayError


class _Segment(Protocol):
    text: str
    start: float
    end: float


class _Information(Protocol):
    language: str


class WhisperModel(Protocol):
    """Narrow the third-party transcriber immediately; no provider objects enter character logic."""

    def transcribe(
        self,
        audio: np.ndarray[tuple[int], np.dtype[np.float32]],
        *,
        language: str | None,
        beam_size: int,
        vad_filter: bool,
        condition_on_previous_text: bool,
    ) -> tuple[Iterable[_Segment], _Information]: ...


class _ProviderFactory(Protocol):
    def __call__(
        self,
        directory: str,
        *,
        device: str,
        compute_type: str,
        cpu_threads: int,
        num_workers: int,
        local_files_only: bool,
    ) -> WhisperModel: ...


@dataclass(frozen=True)
class _Transcript:
    text: str
    language: str
    duration: float


class BoundedCpuSTT(stt.STT[object]):
    """At most one native utterance; caller cancellation does not release CPU admission.

    The caller loads a local model during explicit runtime composition off-loop.
    No download, CUDA selection, previous-turn prompt or hidden reasoning is used.
    """

    def __init__(self, language: str = "en") -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self.language = language
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ember-cpu-stt")
        self.pending: Future[_Transcript] | None = None
        self.closed = False

    @abstractmethod
    def transcribe(self, pcm: bytes, language: str) -> str:
        """Decode one bounded 16 kHz mono PCM utterance on the owned worker."""

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        del conn_options
        if self.closed or (self.pending is not None and not self.pending.done()):
            raise GatewayError("stt_busy")
        # Materialize only a bounded mono utterance. SDK VAD is configured for 16 kHz / 30 s.
        frames = [buffer] if isinstance(buffer, rtc.AudioFrame) else buffer
        samples = sum(frame.samples_per_channel for frame in frames)
        if (
            not frames
            or samples <= 0
            or any(frame.sample_rate != 16000 or frame.num_channels != 1 for frame in frames)
            or samples > 16000 * 30
        ):
            raise GatewayError("stt_audio_bound")
        selected_language = language if isinstance(language, str) else self.language

        def transcribe() -> _Transcript:
            frame = rtc.combine_audio_frames(frames)
            text = self.transcribe(bytes(frame.data), selected_language).strip()
            if len(text) > 1600:
                raise GatewayError("stt_transcript_bound")
            return _Transcript(text, selected_language, samples / 16000)

        self.pending = self.pool.submit(transcribe)
        wrapped = asyncio.wrap_future(self.pending)

        def observed(completed: asyncio.Future[_Transcript]) -> None:
            if not completed.cancelled():
                completed.exception()

        wrapped.add_done_callback(observed)
        async with asyncio.timeout(30):
            result = await asyncio.shield(wrapped)
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=str(uuid4()),
            alternatives=[
                stt.SpeechData(
                    language=LanguageCode(result.language),
                    text=result.text,
                    end_time=result.duration,
                )
            ],
        )

    async def aclose(self) -> None:
        """Drain the actual native future before its executor is joined off-loop."""
        if self.closed:
            return
        self.closed = True
        if self.pending is not None:
            await asyncio.gather(asyncio.wrap_future(self.pending), return_exceptions=True)
        await asyncio.to_thread(self.pool.shutdown, wait=True)
        await super().aclose()


class CpuWhisperSTT(BoundedCpuSTT):
    """Local faster-whisper provider."""

    def __init__(self, model: WhisperModel, *, language: str = "en") -> None:
        super().__init__(language=language)
        self._model = model

    @classmethod
    def load(cls, directory: Path, *, threads: int = 2, language: str = "en") -> "CpuWhisperSTT":
        """Composition calls off-loop; missing artifacts degrade speech rather than download."""
        if not directory.is_dir() or not (directory / "model.bin").is_file():
            raise GatewayError("stt_artifacts_unavailable")
        # Narrow the optional provider's untyped constructor at this boundary.
        factory = cast(_ProviderFactory, importlib.import_module("faster_whisper").WhisperModel)
        model = factory(
            str(directory),
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
            num_workers=1,
            local_files_only=True,
        )
        return cls(model, language=language)

    def transcribe(self, pcm: bytes, language: str) -> str:
        """Decode with the explicitly provisioned CPU Whisper model."""
        values = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / np.float32(32768)
        segments, _ = self._model.transcribe(
            values,
            language=language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        parts: list[str] = []
        size = 0
        for segment in segments:
            size += len(segment.text)
            if size > 1600:
                raise GatewayError("stt_transcript_bound")
            parts.append(segment.text)
        return "".join(parts)
