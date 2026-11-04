# CPU Phonon voices and browser read-ahead

This is an independently deliverable audio/browser amendment to the delivered
Phase 8 application while Phase 9 continues. It introduces no Phase 9 dependency
or new phase gate, and does not rewrite historical acceptance evidence.

## Behavior and ownership

The browser shows validated reply text as it becomes available, ahead of speech.
The existing hosted LiveKit room owns both channels. `ember.reply` carries the
read-ahead text to the authenticated `ember-user` participant; the SDK's existing
synchronized `lk.transcription` channel continues to provide played-prefix receipts.
The browser ignores assistant copies of the synchronized channel to avoid duplicates,
while microphone final transcripts still use that channel.

Simply disabling `sync_transcription` is insufficient: in LiveKit Agents 1.8.5 it
also removes the transcript synchronizer that supplies interrupted audio receipts.
The new transcription node writes each currently authorized chunk immediately and
then yields it to the unchanged synchronization chain. It owns and closes its SDK
writer on completion, cancellation and validation failure. Generated but unheard
suffixes do not become spoken delivery or completed history.

The existing authenticated browser control API offers installed Phonon voices and
an approximate 0.75–1.50 speaking-speed control. The hosted room rejects changes
during generation, pending delivery or buffered speech. Preferences apply to the
next reply in the current Ember runtime; a restart restores deployment defaults.
They do not enter character memory, the inference request or task state.

## CPU synthesis and explicit setup

The provider remains the installed `ptts==0.6.0` CPU/Q8 Phonon 31M service on
loopback. Its voice response explicitly advertises `ember_voice_controls_v1`;
providers without that capability keep working and expose no unsupported controls.
`services.kokoro` and the `tts-1` transport label remain compatible with existing
deployments. The browser cannot choose paths, download weights or switch providers.

Run `scripts/setup_phonon_voices.ps1` explicitly to install six named English voices
from the pinned Apache-2.0 Gradium catalog. `-Voice` accepts additional catalog
names. The setup receipt records source revision and SHA-256 for the installed
files under ignored `.local/`; restart Ember to load them. Runtime catalog lookup
and downloads remain disabled, and unknown voices are rejected before synthesis.

Each synthesis request sets native `duration_delta = 1 / speed - 1` while holding
the existing single-stream lock. This changes model-guided pace without altering
PCM sample rate or pitch. The multiplier is approximate, not a guaranteed duration
ratio. Default requests explicitly restore normal pace. No additional model is
loaded, and no Qwen GPU or scheduler settings change.

## Compatibility and failures

Memory ABI/schema, canonical stores, gateway wire/admission, tasks, grants and
native capture are unchanged. The only new boundary is optional app-control
callbacks into the existing hosted audio owner. Controls preserve origin/cookie
checks, validate finite bounded rates and installed voices, and report failed saves.
Unavailable speech still leaves existing text/degraded operation intact.

Acceptance covers streamed PCM, installed-voice and rate validation, normal-rate
reset, operator authentication/origin checks, mid-turn rejection, immediate text
publication, revocation/writer cleanup, played-prefix regressions, browser save/error
interaction and narrow-screen layout. Live Windows and full quality-gate results
are recorded below after execution. Physical microphone/headset quality remains an
operator listening check; synthetic audio is not physical-device acceptance.

## Executed validation: 2026-11-04

- Windows installed quality gate: Ruff format/lint and strict Pyright passed;
  **708 Python tests passed, six opt-in cases skipped**, with installed LiveKit enabled.
- macOS Python: **702 passed, 12 opt-in skips** with canonical `TMPDIR=/private/tmp`.
  The default macOS `/var` alias is rejected by existing canonical-store safety checks;
  Windows-target strict typing also passed locally. No storage/type-policy change was made.
- Production frontend builds and TypeScript checks passed on macOS and Windows;
  **19 unit tests** and **nine browser tests** passed on each platform (installed
  Edge on Windows), with three opt-in live-browser cases skipped.
  Browser tests include confirmed/failed voice saves, sender/duplicate filtering,
  input/scroll behavior and five narrow/short/desktop layouts.
- Real Windows CPU synthesis used the installed default/Juniper voices. For the same
  synthetic phrase, Juniper produced 9.52 s of audio at normal pace, 8.32 s at the
  1.25 setting and 10.40 s at 0.80. First PCM arrived in 60–110 ms across four probes;
  complete synthesis took 0.83–0.95 s. These are samples, not percentile measurements.
- Existing synthetic gateway baseline passed text, JSON and tool cases on the pinned
  Qwen IQ3_S/MTP tag at 131,072 context. The cold text case loaded in 3.58 s and first
  returned visible text at 3.95 s. The warm JSON case first returned text in 156 ms
  and decoded at 97.9 tokens/s. These short cases do not certify long-context latency
  or prove that speculative/MTP decoding is active.
- The desktop was restarted through `start.ps1`, independently of the SSH lifetime.
  A fresh connection confirmed persistent browser/LiveKit text readiness, CPU
  Phonon-2 input health, CPU Phonon 31M output, all seven voice choices (including
  default), and normal initial pace. The live authenticated settings API accepted Juniper/1.25
  and restored default/1.0. Deployment context/model settings were preserved.

Physical microphone/headset, preferred voice quality and final HTTPS browser
read-ahead/listening acceptance are left to the operator, as requested. No private
source text, prompts, reasoning or audio was added to tracked reports or fixtures.
