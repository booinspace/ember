# Phase 11 gateway qualification — 2026-11-04

## Ollama baseline

All **210 measured warm trials and seven detach/drain/completed-next-P0 probes**
passed. Each case retains 30 trials, an excluded warmup and one recovery probe.
[Configuration and source hashes](ollama-configuration.json) identify the measured
source before its containing commit; individual JSON files retain original report
hashes and every content-free sample. No private prompts, replies, credentials,
audio or hidden reasoning are included.

Windows 11 build 26200, Python 3.12.15, Ollama 0.40.14, RTX 3090 24,576 MiB,
driver 616.92. Existing IQ3_S GGUF, 131,072 configured context, 126,976 input guard,
4,096 output reserve and 64-token synthetic benchmark output. CPU Phonon Juniper
at 1.1x supplies paced SDK PCM. Ordinary character and Hermes task input limits
remain much smaller than the runner capacity.

The primary app was stopped at the operator's request to clear a stuck realtime
reservation; only the isolated gateway generated. No deliberate competing GPU
workload ran. All requests used the authenticated gateway, including queued
P2/P3/P4. Queue cases wait behind a realtime reservation, then release it; they
do not measure concurrent GPU decoding or native preemption.

Command: `python scripts/benchmark_gateway_suite.py --config <isolated profile>
--output-directory <new ignored directory> --samples 30 --voice-samples 30`.

| Case | First visible p95 ms | Turn p95 ms | Decode median tokens/s | Sampled global peak MiB | Detach to ready ms (one probe) |
| --- | ---: | ---: | ---: | ---: | ---: |
| p0-short | 125.3 | 807.3 | 72.30 | 22333 | 545.7 |
| p0-medium | 140.4 | 894.2 | 77.36 | 22348 | 670.8 |
| p0-long | 142.0 | 1015.4 | 80.75 | 22329 | 628.8 |
| p2-queued | 123.6 | 807.6 | 72.45 | 22329 | 634.4 |
| p3-queued | 105.8 | 791.4 | 72.67 | 22329 | 665.7 |
| p4-queued | 109.2 | 848.9 | 71.16 | 22329 | 664.5 |
| voice-short | 430.4 | 15052.0 | 69.87 | 22446 | 482.4 |

Voice first PCM: median 558.9 ms,
p95 660.1 ms. SDK sink clear after interruption:
1.043 ms (one probe). These are
headless paced SDK measurements: they exclude microphone/STT, LiveKit networking,
physical device buffers and speaker silence. Full voice turn includes paced playout.

Percentiles use nearest rank; p99 with 30 samples is exploratory. Decode uses
Ollama eval_count/eval_duration. GPU sampling is global dedicated memory every
250 ms, may miss peaks and does not measure shared-memory spill or model-only
allocation. Windows ambient allocations/clocks/thermals were uncontrolled. Earlier
benchmark artifacts retain their own profiles; timing differences are not a
controlled claim that Phase 11 improves inference speed.

## Hermes and alternate backend acceptance

Native worker and refreshed vLLM profile qualification are recorded separately in
[the Phase 11 acceptance record](../../phase-11-validation.md). The original
Phase 8A production-adoption prerequisite remains explicit.

## Refreshed vLLM profile

The final vLLM 0.31.0 profile also passed **210 warm trials plus seven
cancellation/drain/recovered-P0 probes**, the original real Phase 6/7 tests,
ten conformance cases, brokered multi-round tool history and a real native Hermes
task. [Evidence](vllm/evidence.json), [environment/pin](vllm/environment.json)
and [all individual samples](vllm/benchmark/p0-short.json) retain exact hashes.
The runner remains in its existing WSL Ubuntu 24.04 environment; Hermes runs
natively on Windows. The existing GGUF is symlinked, without another weights copy.
The refreshed processing digest intentionally differs from the previous receipt.

Same GPU/GGUF/context/output fixture, serial one-sequence BF16 configuration,
GPU utilization 0.90 and 512 batched tokens. Measurements are separate runs with
uncontrolled Windows allocations/clocks/thermals, not a controlled hardware study.

| Case | First visible p95 ms | Turn p95 ms | Decode median tokens/s | Sampled global peak MiB | Detach to ready ms (one probe) |
| --- | ---: | ---: | ---: | ---: | ---: |
| p0-short | 378.4 | 1643.5 | 36.31 | 22865 | 1293.0 |
| p0-medium | 972.7 | 2441.2 | 35.83 | 22863 | 1291.3 |
| p0-long | 743.1 | 2269.5 | 36.78 | 22868 | 1163.6 |
| p2-queued | 382.2 | 1663.8 | 35.78 | 22868 | 1183.8 |
| p3-queued | 382.7 | 1698.2 | 37.50 | 22868 | 1337.8 |
| p4-queued | 386.5 | 1737.9 | 34.81 | 22868 | 1331.3 |
| voice-short | 733.3 | 15704.5 | 35.84 | 22872 | 947.7 |

Voice first PCM median 952.2 ms, p95 1029.3 ms; one SDK sink-clear probe 0.853 ms.
Physical audio stop is unmeasured. Cancellation still detaches and drains; this
phase does not add native abort or simultaneous generation. This GGUF configuration
was slower than Ollama and used more sampled dedicated VRAM, so Ollama stays default.

Per-request cleanup flags passed. Final isolated gateway shutdown exceeded 30 s
and required termination/drain of its retained owned Windows Job; the model runner
exited normally and GPU/listeners were released. This separate operational limit
is preserved in [cleanup.json](vllm/cleanup.json), including the unresolved cause.

## Native Hermes task and boundary

The final [Ollama task](hermes-ollama/final.json) passed in 24.37 s end to end
(task 23.344 s), and the [vLLM task](vllm/hermes.json) in 26.68 s
(task 25.610 s). Both used two P2 model calls and one actual query against the
operator-installed NAS SearXNG. These single task runs include worker startup,
search, inference, receipt validation and purge; they are not inference throughput
benchmarks. Historical Ollama trials retain their exact earlier mapper hashes.
The [native boundary proof](native-boundary.json) and
[quality gates](quality-gates.json) document independent isolation/CPU and full
regression checks. The four failed model attempts remain recorded in
[actual-task-attempts.json](actual-task-attempts.json); the final guard, citation
checks and original assertions were retained.
