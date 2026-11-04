# Phase 9 native foundations — validation

Date: 2026-11-04. The remote `ember-phase-9` edits were preserved in a checkpoint,
rebased onto main (including continuity, prompt editor and TTS-neutral changes),
and pushed as `codex/phase-nine-completion`. One session conflict was resolved by
retaining speech-delivery fencing and the owned task announcer together.

The acceptance mapping is in [Root ADR 0017](adr/0017-phase-nine-operational-tasks.md).
This accepts native task foundations, not external MCP/harness adapters or live
preemptive inference. The Memory ABI and canonical schema are unchanged.

## Automated checks

The initial full gate passed 744 Python tests on macOS (12 opt-in skips) and 749
on Windows (7 opt-in skips). Mac listener checks needed execution outside the
socket-restricted sandbox; the rerun passed. Windows used the installed LiveKit
binary. Ruff formatting/lint and strict Windows-targeted Pyright passed.

Frontend unit checks passed 21 tests and TypeScript/production build passed.
The added browser test covers exact draft preview and action-hash-bound approval.
Backend coverage includes idempotent retries, additive attempt storage, concurrent
claims, cancellation during persisted progress, confirmed-effect replay fencing,
uncertain writes and speech across restart, citation scope/revocation and denied
artifact reads. Additional announcement guards cover preparation, generation,
pending delivery, user speech, active playback, disconnection and closing.
Benchmark tests use deterministic upstreams to verify completed/incomplete streams,
failed trials, missing usage after cancellation, no probe during uncertain release,
queued-P2 cleanup, preservation of another reservation and paced PCM interruption.

## Installed RTX 3090 observations

Synthetic requests went through the primary gateway; they did not invoke a
character, read memory or enter conversation history. The benchmark temporarily
added its own realtime reservation, leaving existing ones intact. No inference
backend/configuration, context, weights or cancellation policy was changed.

Profile: Windows RTX 3090 (24,576 MiB), driver 616.92, Ollama 0.40.14,
`qwen3.8-rvn:27b-iq3_s-mtp`, IQ3_S GGUF, 131,072 configured context. This is the
current runtime profile, not a matched comparison against another server. The
underlying artifact hash, template/kernel and actual MTP settings still need
explicit pinning for a runner migration comparison.

Thirty warm short synthetic requests with 64-token output ceiling and P2 queued:

| Measurement | Observed |
|---|---|
| Client first visible text | 109 ms median, 110 ms p95 |
| Client request completion | 743 ms median, 812 ms p95 |
| Provider decode tokens/s | 72.05 median |
| Separate first-load warmup | 3.984 s first visible, 4.516 s completion; provider load 3.620 s |
| Sampled dedicated VRAM | 2,521 MiB unloaded snapshot; 23,903 MiB peak |
| One consumer-detach trial | 547 ms to observed gateway release; next P0 first visible 109 ms |

These are short prompts (84 provider input tokens), warm prefix reuse and bounded
outputs, not long-history throughput, sustained multi-user capacity or proof of
native cancellation. The observed peak leaves about 673 MiB of nominal dedicated
VRAM; OS availability and polling resolution limit precision. Do not assume a
second model fits or change residency from this one result. Compare reduced
context/cache profiles before any dual-resident experiment.

An initial three-turn headless voice probe returned real CPU-TTS PCM. The first
turn included SDK/TTS cold overhead (5.859 s to first PCM). Following turns measured
641 and 532 ms to first PCM and about 15.1 seconds of paced playout, while gateway
generation completed in about one second. A separate interruption trial observed
software sink clear within the timer's resolution and gateway release after
485 ms. This demonstrates distinct software audio and model-drain paths; it is
not a physical audio latency result. The harness now excludes a voice warmup
before calculating warm voice aggregates.

Raw reports remain ignored under the remote `.local/` directory. The short-run
p99 values are exploratory. No vLLM/llama.cpp comparison, long-context acceptance,
KV/shared-memory instrumentation, full physical microphone/browser interruption,
or Phase 13 GPU abort/release acceptance is claimed. Those remain explicit future
performance qualification; no throughput claim can override voice correctness.

## Final review corrections

Approval deadlines now inherit the remaining task deadline, and expired tool
contexts are denied before invocation. A queued task cannot gain extra execution
or approval time from a later dispatch.

Repeated interruption exposed a Phonon stream-cleanup gap: the synchronous HTTP
iterator had no guaranteed native-context close on disconnect and could leave
later requests stalled behind its lock. Synthesis now has one owned worker;
response cleanup queues native close on that same worker behind any in-flight
chunk, then releases admission. Busy requests return 503 rather than accumulating
blocked HTTP workers. ASGI 2.3 disconnect cancellation and 2.4 send failures have
regressions proving closure and subsequent synthesis. This fixes CPU speech
resource ownership; it does not implement GPU/model preemption.

An isolated native CPU Phonon service on port 8881 passed three real stream
interruption/recovery cycles, then the SDK benchmark and another synthesis after
interruption. With voice warmup excluded, three first-PCM samples were 547, 485
and 578 ms. The cancellation trial reached first PCM at 516 ms; gateway drain
was observed 328 ms after interruption, and next-P0 first visible text at 94 ms.
Software sink clear was within clock resolution. These remain software-sink,
short-prompt observations, with no physical device/network claim.

The benchmark now rejects completed text with missing PCM, disables TTS retries
for clean failure attribution and bounds SDK cleanup. The earlier stalled
benchmark was stopped and only its uniquely identified reservation was removed;
original sessions were preserved. The fixed isolated service was stopped by its
test owner. An aborted voice run is a failure, not a latency sample.

Windows frontend dependencies were rebuilt from the unchanged frozen lockfile
after stale node_modules prevented tool startup. Prettier, 21 frontend tests,
TypeScript/build and 11 installed-Edge browser tests passed (three live-device
cases skipped). The final Python gate and final hardware run are recorded below.

Final P3 and P4 reservation trials each completed ten warm requests plus the
consumer-detach/next-P0 probe. The respective background queues remained occupied
while the benchmark reservation was held. First-visible p95 was 110 ms for P3
queued and 125 ms for P4 queued. These are blocked-background admission checks,
not simultaneous GPU execution or a sustained fairness benchmark.

The final complete gate passed **758 tests on macOS** (12 opt-in skips) and
**763 on Windows** (7 opt-in skips), with Ruff/strict Pyright passing. The final
normal-EOF cleanup refinement also releases speech admission before signalling
HTTP completion, preventing the next sentence from racing the previous cleanup;
focused disconnect tests were rerun after that refinement.

A matched 30-sample P0-only run measured 109 ms median / 110 ms p95 first visible,
695 ms median / 782 ms p95 completion and 76.87 median decode tokens/s. Queued
P2 first-visible p95 was also 110 ms. These samples support conservative admission
for this short synthetic case; they do not establish Phase 13 preemption.

A final Windows-only pacing assertion exposed coarse `monotonic` clock granularity.
Client benchmark timestamps now use high-resolution `perf_counter`; the pacing
regression uses a longer frame so it tolerates scheduler granularity while still
rejecting unpaced output. All 33 affected tests and strict types passed again on
both platforms. Earlier timing tables retain their original run precision.

The final isolated CPU voice run passed with TTS retries disabled, including
post-interruption synthesis. The opt-in installed gateway/LiveKit character test
also passed: a synthetic remembered GPU fact survived a temporary memory-engine
restart and was recalled by the pinned real model through the sole gateway.
