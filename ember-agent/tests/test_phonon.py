"""Bounded Phonon integration uses synthetic local provider responses."""

import io
import wave

import httpx
import pytest

from ember_agent.audio.phonon import PhononSTT
from ember_agent.inference.contracts import GatewayError


def test_phonon_checks_identity_and_encodes_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(
                200, json={"status": "ok", "kind": "speech", "model": "FermionResearch/Phonon-2"}
            )
        assert request.url.path == "/v1/audio/transcriptions"
        assert b"RIFF" in request.read()
        return httpx.Response(200, json={"text": "synthetic speech"})

    transport = httpx.MockTransport(respond)
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=transport, **kwargs))
    recognizer = PhononSTT.load("http://127.0.0.1:8010", language="en")
    try:
        assert recognizer.transcribe(bytes(320), "en") == "synthetic speech"
        audio = io.BytesIO()
        with wave.open(audio, "wb") as writer:
            writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        with pytest.raises(GatewayError, match="phonon_language_unsupported"):
            recognizer.transcribe(bytes(320), "fr")
    finally:
        recognizer.pool.shutdown()


def test_phonon_rejects_bad_model_and_remote_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        PhononSTT,
        "_request",
        staticmethod(lambda *_: b'{"status":"ok","kind":"speech","model":"wrong"}'),
    )
    with pytest.raises(GatewayError, match="phonon_model_unavailable"):
        PhononSTT.load("http://127.0.0.1:8010", language="en")
    with pytest.raises(GatewayError, match="phonon_configuration_invalid"):
        PhononSTT.load("http://example.com:8010", language="en")
