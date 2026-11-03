"""Explicitly attached, loopback-only Phonon-2 endpoint for VAD-finalized audio."""

import io
import wave

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..inference.contracts import GatewayError
from .whisper import BoundedCpuSTT


class _Transcript(BaseModel):
    text: str = Field(max_length=1600, repr=False)


class _Health(BaseModel):
    status: str
    model: str
    kind: str


class PhononSTT(BoundedCpuSTT):
    """Send bounded utterances to a separately provisioned local Phonon process."""

    @classmethod
    def load(cls, endpoint: str, *, language: str) -> "PhononSTT":
        """Require the expected local speech model before voice startup."""
        if endpoint != "http://127.0.0.1:8010" or language != "en":
            raise GatewayError("phonon_configuration_invalid")
        try:
            health = _Health.model_validate_json(cls._request(endpoint + "/health"))
        except ValidationError as error:
            raise GatewayError("phonon_health_invalid") from error
        if (health.status, health.model, health.kind) != (
            "ok",
            "FermionResearch/Phonon-2",
            "speech",
        ):
            raise GatewayError("phonon_model_unavailable")
        return cls(endpoint)

    def __init__(self, endpoint: str) -> None:
        super().__init__(language="en")
        self.endpoint = endpoint

    @staticmethod
    def _request(url: str, audio: bytes | None = None) -> bytes:
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
                with client.stream(
                    "GET" if audio is None else "POST",
                    url,
                    files={"file": ("turn.wav", audio, "audio/wav")} if audio is not None else None,
                    data={"model": "phonon-2"} if audio is not None else None,
                ) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        if len(body) + len(chunk) > 16384:
                            raise GatewayError("phonon_response_bound")
                        body.extend(chunk)
                    return bytes(body)
        except httpx.HTTPError as error:
            raise GatewayError("phonon_unavailable") from error

    def transcribe(self, pcm: bytes, language: str) -> str:
        """Transcribe one utterance without storing audio or accepting interim events."""
        if language != "en":
            raise GatewayError("phonon_language_unsupported")
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(pcm)
        try:
            return _Transcript.model_validate_json(
                self._request(self.endpoint + "/v1/audio/transcriptions", audio.getvalue())
            ).text
        except ValidationError as error:
            raise GatewayError("phonon_transcript_invalid") from error
