# Phase 13 acceptance record

The gateway implements conservative inference hardening. The 22 November port
keeps live preemption disabled on the managed Strata profile. The earlier
Ollama measurements are preserved below. The
[PLAN acceptance criteria](../PLAN.md#phase-13-acceptance-criteria-preemption-hardening)
and [Root ADR 0024](adr/0024-phase-thirteen-inference-hardening.md) define the gates.

## Strata voice preemption candidate — 22 November 2026

The candidate runs on the managed single-slot SC117 Strata profile. A trusted
LiveKit speech reservation signals the exact active P5 request. Ember waits
for the native DONE and admission release on the same engine process. It also
checks the normal SSE boundary, re-attests the loaded profile and settles the
journal before admitting voice. OpenCode gets `voice_preempted` and the
gateway never replays the interrupted step.

Miku completed 12 interleaved warm trials per condition using installed CPU
TTS and the paced PCM sink. Timing starts at the synthetic speech reservation
and includes foreground generation and first PCM. Every coding trial had a
verified native abort and a successful P0 on the same runner. No interrupted
trial published a tool call. The test preserved the normal context length,
MTP and int8 KV settings.

| Condition with `--prefill auto` | First PCM p50 / p95 | Added p95 |
| --- | ---: | ---: |
| Idle | 208 / 356 ms | — |
| Active coding decode | 300 / 364 ms | 8 ms |
| Active long coding prefill | 1,125 / 1,190 ms | 834 ms |

The production profile failed P13-AC-03 during prefill. The native engine
checks STOP at prompt chunk boundaries, so a stop can wait on an in-flight
chunk before returning DONE. The production profile stays conservative.

A separate test pin
`1e3f7e2a2a250d309060b8539ca04dda592334e3ba3694de81d185fd045e218b`
changed only the prompt chunk size to `--prefill 256`. It completed the same
12 trials per condition. All 24 coding requests again produced an exact
native abort receipt and a successful foreground turn. The prefill probes
varied their offset after observing native activity.

| Condition with `--prefill 256` | First PCM p50 / p95 | Added p95 |
| --- | ---: | ---: |
| Idle | 197 / 343 ms | — |
| Active coding decode | 300 / 350 ms | 7 ms |
| Active long coding prefill | 618 / 782 ms | 439 ms |

The smaller chunk improved handoff time and still failed the 200 ms gate.
The configuration loader permits only isolated qualification against a
matching test pin. Coding retry cost and device playout remain unqualified.

The 256-token candidate passed both paired recall regressions (P13-AC-07).
Each warm condition completed 1,000 ordinary reads with 11 or 12 overlapping
native abort and paced PCM trials. Ordinary p95 was 32.08 ms with deep recall
disabled, 32.52 ms with deep recall idle and 33.71 ms with deep recall active.
The disabled and active conditions had zero reads over 150 ms. One idle
read exceeded the budget. Deep recall deferred all 454 investigations.

The final loopback Edge check started with active synthetic coding work.
The simulated microphone triggered the LiveKit speech reservation, the
exact P5 request ended with verified native abort and `voice_preempted`,
and the browser received both the expected memory-backed answer and inbound
speech audio. This check used a synthetic recording and establishes the
end-to-end handoff. It contributes no percentile to the paced PCM matrix.

The first pilot independently confirmed native abort but fenced the handoff
because the gateway suppressed the terminal frame alongside coding output.
A regression test now covers terminal frame, EOF and receipt order, plus
the unknown-receipt path. The corrected timing matrix used the fixed gateway.

Raw synthetic trial and receipt logs are under `.local/phase13-voice-preemption-r2/`,
`.local/phase13-voice-preemption-prefill256/` and
`.local/phase13-voice-preemption-browser/` on Miku. The final code passed
1,318 tests with 29 skips, plus Ruff formatting, lint and strict Pyright.
Miku returned to the normal profile with conservative gateway health and
a responding web UI.

## Exclusive Strata desktop acceptance — 22 November 2026

The patched `d96ad10` source overlay ran on Miku in the existing desktop
checkout. It used the pinned `deployment.strata-mtp.toml` profile described below,
a synthetic character and separate runtime and memory roots. The gateway, runner
and browser were loopback-only during the run. Strata 0.2.18 used OpenAI-compatible
SSE. Installed Edge passed both voice checks. Text and a synthetic microphone
recording each returned the expected memory-backed answer and produced inbound
LiveKit audio.

### Warm text and normal drain

Each case completed eight measured turns after warmup. Each queued case
confirmed that its background request stayed queued and withdrew the request
before releasing the realtime reservation. After each case a separate probe
detached from P0 at first visible text, waited for verified drain and
successfully submitted another P0 to the same runner.

| Case | First visible text p50 / p95 | Detach to verified drain |
| --- | ---: | ---: |
| Short baseline | 58 / 71 ms | 225 ms |
| Medium baseline | 55 / 66 ms | 186 ms |
| Long baseline | 56 / 60 ms | 190 ms |
| P2 queued | 54 / 55 ms | 188 ms |
| P3 queued | 55 / 56 ms | 193 ms |
| P4 queued | 53 / 55 ms | 188 ms |

Dedicated host VRAM peaked at 23,744 MiB of 24,576 MiB. The pinned profile
uses an int8 KV cache. Its manifest pins both GGUF shards at
`40f369eca301ec6ea318dbb671b3132c26a2f0b89aa5458a7051b33215ee296c` and
`316b46f3a2dbd68c900f43136ab9449f9dcc3725dfd8c794847c204bc161e113`.
An independent read of both local shard files matched those SHA-256 pins.
The host samples report total VRAM usage. Strata per-request KV ownership
and live native abort are outside this conservative run.

### Warm voice with queued background work

Each case completed 12 turns after warmup using the installed TTS and a
paced PCM sink. The timed interval includes SDK setup. The realtime reservation
held P2, P3 and P4 in their queues throughout their respective cases.

| Case | First visible text p50 / p95 | First PCM p50 / p95 | Added first PCM p95 |
| --- | ---: | ---: | ---: |
| Idle | 328 / 404 ms | 476 / 533 ms | — |
| P2 queued | 293 / 412 ms | 431 / 540 ms | 7 ms |
| P3 queued | 337 / 405 ms | 462 / 540 ms | 6 ms |
| P4 queued | 294 / 484 ms | 428 / 623 ms | 90 ms |

The deltas use the unrounded observations. These probes measure software
delivery through the first PCM frame. The Edge checks exercise the LiveKit
audio path with a synthetic microphone input.

### Voice behind active P5 coding

Three paired runs used a synthetic P5 request and the same paced PCM sink.
The long prompts had about 11,070 rendered tokens. The short prompts had
68–70. Both requested 512 output tokens. The probe saw the exact active
request in gateway health and checked whether visible coding output had begun.

| Condition | Voice gateway admission | First PCM |
| --- | ---: | ---: |
| Warm idle | 0.016–0.020 s | 0.410–0.568 s |
| Before first coding text, long prompt | 9.677–11.190 s | 10.094–11.810 s |
| After first coding text, short prompt | 2.789–3.055 s | 3.272–3.568 s |

All turns completed. Voice began at the gateway within 9 ms of coding's
completion event in these six trials. A separate P5 disconnect left the
same request ID owning the native slot. The following P0 waited 3.32 s for
admission, reached first PCM in 3.77 s and completed on the same runner.

### Recall isolation under coding and voice

The Phase 8 paired benchmark used a 1,000-record indexed synthetic corpus
and one BGE CPU thread. Each warm condition completed 1,000 ordinary reads
and overlapped three synthetic coding and paced PCM trials. Deep recall
deferred 439 times and returned one partial result. Both relative
regression checks passed.

| Condition | Ordinary p50 / p95 / p99 | Over 150 ms | Degraded |
| --- | ---: | ---: | ---: |
| Deep disabled | 19.77 / 41.31 / 77.54 ms | 1 / 1,000 | 1 / 1,000 |
| Deep enabled and idle | 19.73 / 34.52 / 68.81 ms | 0 / 1,000 | 0 / 1,000 |
| Deep active | 19.95 / 35.95 / 74.87 ms | 1 / 1,000 | 1 / 1,000 |

The 150 ms recall contract and foreground guard stay in force. The active
condition had a 0.17 ms median increase against a 0.99 ms allowance. Its
p95 and p99 decreased, and its over-budget and degraded rates matched the
baseline.

### Reconnect and crash recovery

The first reconnect probe found a retained reservation from the test browser's
LiveKit host. The focused run started a clean gateway on the same pinned
Strata profile and reserved realtime capacity during active P2, P3 and P4
requests. The reservation calls waited 4.68 s, 2.28 s and 2.76 s respectively.
Each background stream published its JSON only after drain and each following
P1 completed. The gateway reported no active request when each reservation
returned.

The focused run terminated the isolated gateway during a verified active
P5 with a durable running marker. The reopened journal held uncertainty.
The restarted gateway reported `quiescence_required` and refused a P0
request with HTTP 409. It left the fence intact.

Review reproduced two LiveKit ways to retain a reservation after disconnect.
A late turn preparing input could reassert connectivity after the participant
left, and closing the SDK stream before its task began could leave the drain
event unset. The room now checks participant presence before reserving for
generation. The SDK task settles the matching turn's drain event even when
cancelled before generation. Regression tests cover both paths. Review also
found that the queued PCM suite's synthetic request could expire before the
default sample count. The probe now gives its synthetic queued request and
HTTP read the lifetime of its bounded benchmark case.

The updated source was tested on `db239ea`, including its OpenCode memory
changes. A separate loopback RTC room repeated both Edge checks with the
installed speech providers and a deterministic upstream. Both browser
disconnects returned the gateway to zero reservations.
The restored `db239ea` runtime also passed the configured HTTPS coding
smoke check, including the bearer challenge and a streamed completion with
OpenCode-style tool metadata.

The full macOS suite passed 1,313 tests with 29 skips on the updated main
revision. Ruff formatting, lint and strict Pyright passed. Independent review
checked the LiveKit corrections, synthetic benchmark lifetime and Strata SSE
completion coverage after the fast-forward.

### Conservative acceptance against PLAN

| Criterion | Evidence |
| --- | --- |
| P13-AC-01 | Pinned Strata, GGUF, transport, context and GPU profile. Matched warm TTFT, VRAM, drain and paced voice measurements cover queued P2–P4. Active P5 probes cover coding wait. |
| P13-AC-02 | Real Strata drains before same-runner P0 reuse and before returning a realtime reservation over P2–P4. The controlled crash left a durable fence that refused P0 after restart. The SSE test holds the body open after `[DONE]`. |
| P13-AC-03 | Browser microphone-to-memory-to-audio checks passed. The disconnect rerun released both RTC reservations. The paced matrix measures first PCM, and the P5 wait is recorded above. Physical playout and the 200 ms live-preemption target remain unqualified. |
| P13-AC-04 | P2–P4 publish validated JSON after completion. Automated faults cover incomplete output and tool arguments. Requeue applies only before native start. P5 continues to drain when its subscriber leaves. |
| P13-AC-05 | Automated cases cover queue bounds, expiry, fairness, transport and journal failure, and shutdown. The native drain and isolated crash checks exercise recovery with the real runner. |
| P13-AC-06 | Strata reports `scheduling: conservative`. Configuration rejects an enabled live-preemption setting. |
| P13-AC-07 | The controlled mixed-load recall comparison passed both relative gates with the 150 ms budget and foreground guard intact. |

Sanitized receipts and raw synthetic trial reports are under
`.local/phase13-strata-acceptance/` on Miku. The normal runtime was restored
with the existing deployment configuration and its gateway and browser
readiness were verified.

## Earlier shared-runtime measurements — 22 November 2026

The running desktop used commit `d96ad10` and `deployment.strata-mtp.toml`
with SC117 IQ3_S, a 131,072-token context, int8 KV, automatic prefill,
MTP and an RTX 3090 with driver 621.24. The gateway reported the exact
runner profile SHA-256
`f8db318d253b9f480674399e3cba4b19003770a8056d9251c5c8ebb4b1f1980c`.
The original probes read client settings from the non-MTP TOML. Its model
label, runtime directory, gateway endpoint and TTS settings matched the
actual launch. The probes ran against the MTP process. Future voice-wait
probes verify the live model, digest and context before submitting work.
The benchmark left the live gateway, Strata and TTS processes running. An
isolated Windows source overlay exercised the port in automated tests.

### Voice behind active coding

The probe submitted short synthetic P0 turns to the existing gateway and used
the installed TTS with the SDK's paced PCM sink. The deployed gateway waits
for active P5 and rejects P0 as busy during other active workloads. The
contended probes observed an active request before starting and then waited
for P0 admission. The observations identify coding contention without
collecting the coding client's prompts, responses or identity.

| Condition | Gateway start | First visible text | First PCM |
| --- | ---: | ---: | ---: |
| Idle, warm | 0.017 s | 0.300 s | 0.476 s |
| Coding active, trial 1 | 2.496 s | 2.830 s | 3.512 s |
| Coding active, trial 2 | 31.745 s | 32.028 s | 32.176 s |
| Coding active, trial 3 | 30.037 s | 30.319 s | 30.469 s |

The timer starts at probe kickoff and includes SDK setup. The median contended
admission time was 30.04 s. The three samples show the potential wait with
conservative scheduling. They cannot establish a p95 or distinguish coding
prefill from decode. The synthetic matched run yielded to other requests, so
its completed warm idle row is a reference point only. The result supports
prioritizing the active P5 case if native preemption is qualified later.

The queued-workload sweep also encountered coding during its nominal idle
control. All three warm control probes completed, with 30.52–33.03 s to
gateway start. Dedicated VRAM peaked at 23,753 MiB in the host samples.
This measures shared-desktop usage and leaves per-request KV occupancy unknown.
Use these control rows as additional contention observations.

The deployed gateway confirmed one queued request in each P2, P3 and P4 case.
All nine warm foreground probes completed. First PCM ranged from 34.04–38.46 s
with P2 queued, 34.92–43.02 s with P3 queued and 0.44–43.90 s with P4 queued.
Each case withdrew its synthetic background request and settled its reservation.
The coding traffic and the varied gateway admission times prevent a matched
comparison of the queued workloads.

Run [the voice wait probe](../scripts/benchmark_strata_voice_wait.py) with the
running Strata config and a fresh report path. Use `--observe-active` to submit
only voice probes when existing work is active. The default mode uses
synthetic coding requests. Local reports are under
`.local/phase13-strata-live/` on the desktop.

### Recall isolation and automated validation

The desktop reran the 1,000-record synthetic recall fixture with one CPU BGE
thread. Each condition completed 1,000 ordinary reads. No call exceeded 150 ms,
and there were no degraded reads. Deep recall deferred 488 times. With deep
active, ordinary p50 rose from 20.06 to 21.36 ms, p95 from 30.26 to 42.40 ms
and p99 from 38.03 to 55.57 ms. The relative regression check failed at all
three percentiles. The live machine was shared, so this run cannot isolate
the effect of coding or audio from the deep comparison.

The full locked macOS suite passed 1,303 tests with 29 skips. The patched
Windows overlay passed 244 focused tests. Independent review confirmed Strata
closes its SSE transport after the terminal stream message and found an
active-expiry response that needed a typed error. The fix and its regression
test preserve the restart fence. The review also clarified the probe timing
boundary and withdrawal of an unsuccessful synthetic coding subscriber.

## Historical Ollama acceptance — 21 November 2026

| Criterion | Evidence and remaining gate |
| --- | --- |
| P13-AC-01 | The temporary desktop benchmark records the resident model digest, runner version, context, transport and GPU. It measures first visible text, decode and best-effort dedicated VRAM sampling. Per-request KV ownership and retry after a native abort still need a qualified runner. |
| P13-AC-02 | Tests hold the slot after a terminal frame while the native stream remains open. P0 is busy and P1 waits. A trailing frame or transport failure suppresses background output and survives restart as uncertainty. The follow-up directly observed native cancellation, same-task slot release and a subsequent P0 on the same runner in an exclusive window. Integrated live admission across crashes and reconnects remains open. |
| P13-AC-03 | A connected session reserves the runner before the foreground turn and continues through playback. Paced PCM completed with queued P2 and during reconnect over all three running workloads. The exclusive follow-up completed queued P3 and P4. The three-pair diagnostic added 113 ms at p95 from a synthetic onset marker to first PCM. Production speech-onset reservation, playback guard and the full 200 ms voice gate remain open. |
| P13-AC-04 | P2–P4 buffer text/JSON and tools until validation and completion. Transport failure emits only an error. Parking is limited to an immutable request that has never started upstream. All three exclusive native aborts exposed zero buffered output events. Existing task and tool receipt tests cover effect ownership. Automatic requeue after verified abort and its replay races still need integration coverage. |
| P13-AC-05 | Tests cover pending capacity during verification, expiry before native start, queue fairness, subscriber disconnect, journal failure, shutdown during a running-marker write and realtime reconnect during disconnect. The gateway never stops the attached runner. |
| P13-AC-06 | The configuration rejects live preemption and health reports `scheduling: conservative`. Background work waits for realtime disconnection. |
| P13-AC-07 | Existing deep/ordinary recall and CPU audio isolation tests run in the full gate. The earlier P2 overlap missed its relative p50 gate. The recall-only rerun and the exclusive P3/P4 overlaps passed their relative gates. P3 had one ordinary deep-active call over 150 ms. The 150 ms deadline and foreground resource guard remain in force. |

## Desktop profile and failure recovery

The Windows fixture uses Ollama 0.40.16 over loopback `/api/chat` NDJSON and the
`llamacpp` runner. The installed model was `qwen3.8-rvn:27b-iq3_s-mtp-128k`
(IQ3_S GGUF, digest `3b1b5094a8f0d8d91fc4da0870e1755afd7368383c8d01f602f8091e85414303`).
The model metadata reported 131,072 context tokens and two draft tokens. The
RTX 3090 reports 24,576 MiB dedicated VRAM and driver 621.24. PCM uses the installed
CPU Phonon TTS, Juniper at 1.1×. The probes use synthetic inputs and isolated
runtime, gateway journal and recall roots.

The first run recorded 18 completed text trials, then
`upstream_generation_failed` and two `quiescence_required` rejections around the
desktop reboot. The first report keeps those failed trials. Its tail is excluded
from the matched latency comparison. No process or model restart is used by
the gateway to recover from uncertainty.

The post-reboot run completed the text cases and exposed a `slow_consumer` error
on the first PCM warmup. Ollama can deliver a burst large enough to fill the
32-event queue before the HTTP consumer is scheduled. The gateway now waits
up to 100 ms for queue space. A truly stalled subscriber still detaches and its
native generation retains the slot until drain. Regression tests cover both cases.

## Text and normal drain measurements

Each case completed 20 measured P0 text turns after its own warmup and one
subscriber-detach trial. P2, P3 and P4 stayed queued in their respective cases.
The gateway verified normal completion before the next P0 probe. The driver
sampled total dedicated VRAM, with a maximum of 22,706 MiB across the text runs.

| Case | First visible text p50 / p95 | Decode median | Detach to gateway drain | Next P0 first visible |
| --- | ---: | ---: | ---: | ---: |
| Baseline | 108 ms / 10,829 ms | 70.6 tokens/s | 591 ms | 101 ms |
| P2 queued | 105 ms / 112 ms | 67.8 tokens/s | 622 ms | 106 ms |
| P3 queued | 105 ms / 110 ms | 73.4 tokens/s | 629 ms | 107 ms |
| P4 queued | 106 ms / 110 ms | 62.7 tokens/s | 606 ms | 100 ms |

Eight baseline trials took more than one second to produce visible text.
Those trials remain in the percentile. The provider's reported prefill for them
was 81–89 ms, so the run does not isolate the cause of the tail. The queued
cases establish serial admission and recovery. The timing variation prevents a
causal comparison of queued work against the baseline. The drain measurement includes
gateway health polling and is distinct from native abort or per-request KV release.

## Paced PCM baseline

All 20 measured 64-token turns completed after the burst fix. First PCM was
1.21 s p50 and 39.06 s p95 from submission to the SDK sink. The corresponding
first visible text was 0.99 s p50 and 38.84 s p95, so the long tail appeared
before playout. The interruption trial took 1.3 ms to clear the simulated sink
and 438 ms to observe gateway drain. These measure distinct stages of the
conservative path. Microphone, network, and audible-device interruption require
their own measurements.

## Reconnect and recall measurements

Nine synthetic reconnects arrived during active P2, P3 or P4 generation. All nine
background requests completed and the first P0 reached the PCM sink after the
reservation returned. The reservation wait was 4.21–5.06 s for P2, 4.68–23.09 s for
P3, and 4.82–18.27 s for P4. Including the foreground turn, first PCM arrived
4.61–32.30 s after reconnect was requested. This is the cost of conservative
drain before foreground admission.

In the shorter 16-token sample, first PCM was 343 ms p50 and 350 ms p95 for the
baseline. It was 357 ms p50 and 71.20 s p95 with P2 queued and recall running
during the same case. Each condition had eight successful warm turns, so p95 is
the slowest sample. The P2 outlier was already present at first visible text
(70.97 s). During the next queued P3 case, the installed manifest still matched
but the expected loaded model disappeared from runner metadata. The gateway
reported `model_pin_mismatch` and retained its uncertainty fence. The queued P3/P4
PCM matrix ended with seven successful P3 turns and one timed-out probe. P4
queued PCM was unreached. The 200 ms added-latency gate remains open.

The isolated recall corpus has 1,000 synthetic records and uses one CPU BGE
thread. With only recall running, ordinary p95 was 34.65 ms with deep disabled
and 33.75 ms with deep active. Under the overlapping voice/P2 case, the
corresponding p95 values were 35.72 ms and 32.41 ms. Two deep-disabled calls in
the overlapping run exceeded 150 ms and one degraded. In the deep-active
condition, all 1,000 ordinary calls returned within 150 ms and deep recall
reported 441 deferrals and one partial result. The active p50 rose by 1.13 ms
against a relative limit of 1.01 ms, so the combined resource regression gate
remains open. The gateway does not change recall budgets in response.

The raw numeric reports are in the workspace's ignored
`.local/phase13-validation` directory. The installed desktop model and audio
assets were exercised from the temporary checkout using synthetic data.

## Exclusive desktop follow-up

Rotated Ollama logs explain why the installed pin could still match while the
loaded check failed. At 00:34:19 the runner was evicted. Ollama then launched
8,192- and 131,072-token runners and recorded a runner exit with status 1.
The logs show LAN generation requests interleaved with the gateway's loopback
requests. The Ollama server process was replaced at about 00:35. During the
first follow-up, the server was replaced again at about 00:49 amid LAN
generation. The temporary gateway recorded `upstream_transport_failed` and
ended its P3 case after two successful turns. The installed model tag and
digest still matched after the replacement. The logs establish overlapping
traffic and a change in loaded runtime state. They do not identify the exact
cause of either server process exit.

The operator paused the other clients for a dedicated test. The probe used
Ollama 0.40.16, the same resident 128K manifest and an Ollama-owned llama-server
with one slot. All synthetic generation still went through the temporary
gateway. The probe read only numeric slot state from the child runner. It
observed six visible upstream chunks and an active native task before closing
that request's upstream transport. It waited for the same slot and task ID
to report idle, independently verified the runner's task-specific cancellation
and release log entries, and rechecked the loaded pin and process identities.

| Workload | Transport close call | Close request to native slot idle | Buffered output events |
| --- | ---: | ---: | ---: |
| P2 | 0.18 ms | 76.43 ms | 0 |
| P3 | 0.16 ms | 73.89 ms | 0 |
| P4 | 0.15 ms | 76.45 ms | 0 |

The natural 512-token control took 7.17 s. The three aborted requests ended
before their native terminal frames. The gateway retained its uncertainty
fence on each abort. The test harness acknowledged quiescence in its temporary
journal only after the independent release checks. A 16-token P0 completed
through the same native runner after the last release. This verifies slot reuse
on the tested process under exclusive access. It supplies no measurement of
per-request KV bytes or of production voice admission, guard timing and replay.
Ollama's public loaded-model endpoint reports residency. The diagnostic
`/slots` endpoint and logs belong to the separately identified child runner.
[Ollama 0.40.16 child adapter](https://github.com/ollama/ollama/blob/v0.40.16/llm/llama_server.go),
[Ollama loaded-model API](https://docs.ollama.com/api/ps).

The recall-only follow-up completed 1,000 ordinary reads per condition using the
same CPU BGE corpus. Deep-active p95 was 25.64 ms versus 25.31 ms with deep disabled.
The active regression gate passed with 391 deep deferrals, no over-budget
ordinary calls and no degraded results.

### Queued PCM and recall rerun

The same exclusive runner completed all 12 warm turns in each short 16-token
case and recovered from a subscriber-detach trial in each case. At 12 samples,
nearest-rank p95 selects the slowest sample.

| Case | First PCM p50 | First PCM p95 | p95 delta from baseline |
| --- | ---: | ---: | ---: |
| Baseline | 342.0 ms | 354.4 ms | |
| P3 queued | 352.8 ms | 478.0 ms | +123.7 ms |
| P4 queued | 351.9 ms | 401.9 ms | +47.5 ms |

The 1,000-record recall checks used one CPU BGE thread and ran throughout
approximately 69 seconds of each queued case. The P3 deep-active ordinary
p95 was 34.02 ms, 2.08 ms above its deep-disabled control. Its p50 delta was
+0.32 ms against a +0.99 ms limit. One ordinary call exceeded 150 ms. Deep
recall reported 423 deferrals and one partial result. The P4 deep-active
ordinary p95 was 32.95 ms, 0.65 ms below its control, and its p50 was 0.27 ms
below its control. All its deep-active ordinary calls finished within 150 ms,
and deep recall reported 426 deferrals. Both cases passed the existing relative
regression checks. The ordinary recall budget and admission rules were unchanged.

### Active-cancellation PCM diagnostic

A separate test-only gateway held the native slot until it saw the same loaded
pin, same idle slot and matching cancellation and release logs. It then
settled the temporary journal and admitted P0. A synthetic onset marker
started the realtime reservation, which stayed held through CPU PCM playback.
The first diagnostic fixture also interrupted its own foreground request.
The corrected fixture limited the abort injection to background work and
independently verified the prior task's release before resuming.

After excluding one warmup, three matched pairs completed with the same
resident runner. Baseline onset-to-first-PCM was 349–361 ms. The active P3/P4
abort trials were 460–474 ms and observed native slot idle in 70–75 ms.
The nearest-rank p95 difference was +113 ms. The greatest individual paired
delta was +124 ms. The comparison bypasses physical VAD and device playback and
uses a test-only release hook. It measures latency for that hook on the
exclusive fixture. Production admission through speech onset, TTS guard,
requeue and crash recovery still needs end-to-end evidence.

The installed Ollama endpoint also serves clients outside Ember. Its public
chat transport does not provide the request-to-slot binding used in this
controlled test. The current deployment stays conservative until that binding,
exclusive runner ownership and the production voice and recall gates are
verified. The follow-up retained numeric reports under `.local/phase13-validation`
and passed 75 focused gateway, configuration and benchmark tests.

## Independent review corrections

The reviewer reproduced a disconnect that reached ASGI during P0 preflight, a
shutdown that left queued streams open, and a completion write that could
incorrectly leave the journal marked failed after the client deadline. The
review also found an unbounded metadata preflight at request expiry and a
startup timeout that could leave a committed realtime reservation behind. A
recheck found that incomplete output could bypass the loaded pin check.

The HTTP route now watches for disconnect during P0 preflight. Both HTTP and
process-signal shutdown close queued streams before Uvicorn waits on
connections. Metadata verification respects the request deadline and final
journal settlement stays with the owner. A failed startup settles the in-flight
reservation exchange before releasing any committed reservation. Loaded pin
verification now runs before buffered output is finalized. Regression tests use
gated I/O to exercise these races. The PCM benchmark now retains the
typed upstream failure and drains its speech task when the SDK is waiting for
playout. The desktop hardware figures above were recorded before these final
failure-path corrections.

## Automated checks

Locked setup, Ruff format/lint and strict Pyright passed. The macOS full gate
passed 1,227 tests with 28 skips on the final snapshot. The Windows full gate
passed 1,217 tests with 36 skips. After the final pin-order correction, 273
focused Windows tests passed and the temporary gateway and TTS listeners were
confirmed closed.

## Repeating the checks

Run the locked Python gate from the workspace root. On macOS, if the default
temporary directory contains a symlink component, use `TMPDIR=/private/tmp` so
the canonical SQLite tests exercise a local root.

For desktop measurements, use a temporary runtime root, the pinned resident
runner, the installed CPU TTS service and a fresh report directory. Run
`python -m scripts.benchmark_gateway_suite --config PATH --output-directory PATH`.
The suite reports client-visible latency and PCM sink timing, and labels
subscriber cancellation separately from native abort. For the independent recall
baseline, use `python scripts/benchmark_phase_eight.py --root PATH --encoder PATH --output PATH`.
Use synthetic inputs and persist timing, counters and fixed outcome codes only.
