# Engine-neutral TTS configuration

Shared runtime settings are `TTSServiceSettings` and `services.tts`. The endpoint,
installed command, arguments, working directory and process ownership describe a
TTS service. They do not select Kokoro in character logic. Both Kokoro-FastAPI and
CPU Phonon use the existing OpenAI-compatible LiveKit TTS adapter; no extra provider
registry or inference path is needed. Engine-specific setup scripts keep their names.

## Startup defaults

The active deployment TOML supplies the provider request model, installed voice ID
and speaking pace. The Phonon example and primary desktop defaults are:

```toml
[services.tts]
endpoint = "http://127.0.0.1:8880"
mode = "managed"
# Keep the installed provider executable/arguments/working_directory for this machine.

[audio]
tts_model = "tts-1"
tts_voice = "Juniper"
tts_speed = 1.1
```

`tts_model` is the model label sent to the local speech API. The service's installed
command/arguments select the actual synthesis engine and weights. `tts_voice` selects
a voice installed in that engine. Juniper is a Phonon voice; it is not a Kokoro voice.
`tts_speed` is a model-guided multiplier, with the existing 0.75–1.50 range.

Python fallback settings stay provider-neutral: model label `tts-1`, voice `default`,
normal speed `1.0`. A parsed deployment must explicitly name its voice; omitted speed
keeps `1.0` so existing deployments retain their pace. Juniper / 1.1 are explicit
Phonon deployment choices, not a hardcoded rule applied to every backend.

Startup sends all three values to the adapter and initializes the browser's voice
selection with the same speed. Browser edits apply between turns for the current
runtime only; they do not rewrite TOML. Restarting restores the TOML defaults.
Changing a voice or pace does not reload the model or change personality/history.

## Kokoro and compatibility

Kokoro remains supported by pointing `services.tts` at the installed Kokoro-FastAPI
command/endpoint and choosing its model/voice, for example:

```toml
[audio]
tts_model = "kokoro"
tts_voice = "af_alloy"
tts_speed = 1.0
```

The previous `[services.kokoro]` section remains accepted as a parsing alias. Runtime
objects, health identifiers and new process metadata use `tts`. Legacy files omitting `tts_model` keep the historical `kokoro` request label.
Supplying both sections is rejected to prevent ambiguous configuration. Model labels are bounded strings rather
than a hardcoded Kokoro/Phonon enum. Unknown installed voices still fail readiness;
startup does not download providers or silently switch voices.

The existing provider capability advertisement determines whether browser voice/speed
controls are available. A compatible provider without that advertisement still synthesizes
using its configured model, voice and speed. This amendment does not promise new UI
capabilities or synthesis quality for a provider whose installed service was not exercised.

## Verification

Configuration tests cover legacy sections, explicit Phonon defaults, unchanged Kokoro
model/voice/pace, conflicting sections, and invalid or non-finite speed. Adapter tests
exercise PCM HTTP synthesis for both provider configurations and verify the outgoing
model, voice and speed; an in-session override leaves deployment defaults unchanged.
The managed-service tests retain ownership/degraded-startup checks under neutral names.

Validation on 2026-11-04: the local full suite passed 722 tests (12 optional skips).
The Windows quality gate passed 728 tests (6 optional skips), followed by a passing
33-test configuration/adapter rerun for the final legacy omitted-model compatibility
case. Formatting, lint and strict types passed. A real CPU Phonon request using the
primary TOML sent `model=tts-1`, `voice=Juniper`, `speed=1.1` and returned 61,680 PCM
samples. The active browser selection was applied through the existing idle-turn
control and reports Juniper / 1.1. The older Kokoro deployment profile was preserved;
actual Kokoro model inference was not run in this amendment.
