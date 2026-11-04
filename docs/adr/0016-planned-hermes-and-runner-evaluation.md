# 0016 — Evaluate Hermes Agent and Windows inference runners

Status: proposed; no runtime changes or benchmark results.

The [2026-11-04 RTX 3090 review](../inference-backend-evaluation.md) expands the
runner evaluation with a conditional vLLM GGUF/plugin trial and explicit voice,
VRAM and cancellation benchmarks. Native Ollama remains the baseline and native
CUDA llama.cpp the first matched-artifact challenger. A backend adapter preserves
serial admission; multi-model concurrency and measured abort/release require
separate acceptance. This proposal does not complete or reorder Phase 9.

Use [Phase 8A](../../PLAN.md#phase-8a-acceptance-criteria-hermes-and-windows-runner-evaluation)
to qualify Hermes as the preferred harness. Reuse Hermes chat and app adapters,
with Ember retaining realtime voice, canonical memory and model admission.
Keep the Odysseus design as prior research, not a second adapter commitment.

Keep native Windows Ollama as the baseline. Compare native CUDA llama.cpp with
matched GGUF artifacts before considering a switch. Verify effective context and
tool behavior on the pinned Hermes release; a reported context size alone is
insufficient. Phase 11 implements the selected adapter only after successful
feasibility and the Phase 9/10 boundaries it needs. A future acceptance ADR
records benchmark evidence, private context handling and rollback.

Hermes Desktop replaces general text UI after acceptance. LiveKit remains the
voice transport with a minimal Ember room client. A future Hermes voice client
must explicitly integrate with that room; native Hermes speech is a separate
path. Qualify Phonon-2 on Windows CPU as a replaceable STT backend, retaining
Kokoro, VAD and the Whisper rollback profile. Phase 8A records the separate
client and STT acceptance gates; neither is deployed by this decision.

The experimental Phonon attachment is implemented behind explicit configuration.
It reuses the existing bounded utterance worker and LiveKit VAD adapter, with a
loopback health/model check and bounded transcription response. The default is
still Whisper. Physical microphone qualification, provider provisioning and
Hermes text integration remain open.
