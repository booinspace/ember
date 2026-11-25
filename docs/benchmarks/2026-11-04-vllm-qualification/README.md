# Gateway inference comparison — 2026-11-04

Both final suites completed **210/210 measured warm trials + 7/7
consumer-detach/drain/completed-next-P0 probes**, with successful cleanup. Each of
seven cases has 30 measured trials, an excluded retained warmup and one recovery
probe. Samples, failed setup/acceptance attempts and original report SHA-256
fingerprints are retained. No private conversations, credentials, audio or hidden
reasoning are included. Operational session IDs are replaced with counts/presence.

## Configuration and method

Windows 11 build 26200; gateway Python 3.12.15; RTX 3090 24,576 MiB; driver 616.92.
Ollama 0.40.14 versus vLLM 0.31.0 in the isolated EmberVLLM Ubuntu 24.04 WSL2
environment (Python 3.12.16, Torch 2.13.0+cu130, GGUF 0.19.0, plugin
0.0.5+e454affe). Both use the **same existing 12,246,459,840-byte IQ3_S GGUF**,
131,072 runner context, 126,976 input guard, 4,096 output reserve, 64-token benchmark
output cap and temperature 0.2. CPU Phonon Juniper at 1.1x supplies paced SDK PCM.
The benchmark never sends generation directly to either runner.

[Configuration and source hashes](configuration.json),
[live conformance](conformance.json), [original real acceptance](real-acceptance.json)
and [six paired profile replies](phase7-paired-native.json) supply provenance.
The machine-specific software receipt is kept private.
The Ollama measured-source snapshot is preserved in
[the earlier manifest](before-native-timing/configuration.json); the final
manifest records the vLLM timing extraction/label change. The measurement algorithm,
fixtures and Ollama adapter did not change between these runs.

Command: `python scripts/benchmark_gateway_suite.py --config <explicit isolated
profile> --output-directory <new directory> --samples 30 --voice-samples 30`.
The primary app's background lane was reserved; these isolated gateways ran
sequentially with no deliberate competing GPU generation. Queued cases park one
P2/P3/P4 request behind the realtime reservation; they do not measure concurrent
or already-running background generation. Voice measures paced SDK playback,
excluding microphone, STT, network, device buffers and physical speaker silence.

## Final results

First visible means the client's first nonempty visible text, not a GPU first-token
timer. Turn time includes final validation; voice includes paced playout. Percentiles
use nearest rank; with 30 trials, p99 is exploratory. Decode is Ollama's
`1e9*eval_count/eval_duration_ns` versus vLLM's
`1000*(completion_tokens-1)/generation_time_ms`.
Ollama uses its unchanged prompt-evaluation counter, documented by the adapter
and this README; its earlier raw manifest predates the new label. The final vLLM
manifest explicitly labels scheduled-to-first-token first-step wall time.
They are not asserted to be identical timers or pure GPU work.

| Case | Backend | First visible p95 ms | Turn p95 ms | Decode median tokens/s | Sampled peak used MiB | Detach → ready ms (one probe) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| p0-short | ollama | 110.0 | 800.5 | 72.48 | 21634 | 561.0 |
| p0-short | vllm | 378.9 | 1679.0 | 35.98 | 22537 | 1300.8 |
| p0-medium | ollama | 109.1 | 850.8 | 76.49 | 21656 | 592.0 |
| p0-medium | vllm | 986.3 | 2432.6 | 36.07 | 22537 | 1299.1 |
| p0-long | ollama | 112.4 | 867.0 | 81.27 | 21656 | 531.4 |
| p0-long | vllm | 764.6 | 2317.1 | 35.21 | 22539 | 1204.6 |
| p2-queued | ollama | 108.8 | 844.7 | 70.37 | 21656 | 686.0 |
| p2-queued | vllm | 395.3 | 1753.7 | 35.74 | 22539 | 1323.0 |
| p3-queued | ollama | 105.4 | 790.0 | 70.89 | 21656 | 604.5 |
| p3-queued | vllm | 381.5 | 1702.2 | 35.12 | 22539 | 1294.2 |
| p4-queued | ollama | 104.9 | 791.0 | 71.31 | 21658 | 717.7 |
| p4-queued | vllm | 382.6 | 1744.1 | 35.03 | 22539 | 1330.9 |
| voice-short | ollama | 431.1 | 15566.0 | 69.45 | 21658 | 574.4 |
| voice-short | vllm | 709.8 | 15672.7 | 35.76 | 22539 | 972.0 |

Additional voice measurements use the same 30 paced trials and one interruption
probe; clear latency is an SDK-sink signal, not physical speaker silence.

| Backend | First PCM p50 ms | First PCM p95 ms | Interruption → sink clear ms (one probe) | Next P0 first visible ms (one probe) |
| --- | ---: | ---: | ---: | ---: |
| ollama | 572.7 | 638.2 | 1.060 | 122.6 |
| vllm | 920.1 | 995.0 | 0.919 | 376.0 |

Ollama remains the default. In these matched runs it decodes roughly twice as
fast, gives earlier readable text and uses less sampled dedicated VRAM. The
vLLM feature remains available as an explicit qualified backend for future work;
this is not a demonstrated performance upgrade for this GGUF on this card.

GPU values are global dedicated-memory samples every 0.25s, including the excluded
warmup; they can miss spikes and do not measure WDDM shared-memory spill or
model-only allocation. vLLM loaded before the measurement window; cold-start peaks
are not compared. Ambient Windows allocations/clocks/thermals were not controlled.
Use this **refreshed** Ollama baseline, not the older Phase 10 run's larger global
memory peak, for this table. No multi-model residency or near 128K prompt stress
is established. Lower context plus explicit lower GPU/KV budgets require a new
profile and their own allocation/performance tests.

Stop strings, templates, samplers and MTP are relevant differences. vLLM uses the
verified embedded/HF template with explicit thinking/stops; Ollama uses its built-in
qwen3.8 renderer/qwen3.5 parser and reports draft_num_predict2. Actual draft
acceptance was not instrumented. vLLM MTP is off. The native stop-string policy
buffers 11 characters and affects tiny-response visibility. Source inspection found
IQ3_S CUDA MMVQ for batch-one decode and dequantization/matmul for larger prefills;
these are relevant implementation differences, not a proved sole cause of the gap.
Generated utterances/token counts can differ under the same 64-token cap; full voice
duration is not a pure TTS-throughput comparison.

## Retained corrections and limits

The first full vLLM suite is retained in `vllm-before-native-timing/` with its
setup/source snapshots in `before-native-timing/`. It passed all 210+7 probes, but
its adapter left native prefill unknown. An unchanged original Phase 7 test exposed
the missing mapping; an independent source audit proved vLLM publishes exactly
its native prefill interval as time_to_first_token_ms. The tested unit conversion
then passed both unchanged original real-model tests and the final suite above.
No test assertion was weakened and no old timing number was backfilled.

Setup failures (toolkit/header mismatch, unsupported glibc, missing JIT linker
names, obsolete CLI flag, insufficient initial128K cache budget, missing pip,
vendored-metadata inventory collision) and the actual full-schema admission failure
are retained in the adjacent JSON evidence. Grammar guidance omits unsupported
propertyNames only at schema nodes; the original complete schema remains the final
gateway validator. Invalid extension keys never publish successful output.

Gateway admission remains serial and cancellation drains generation. The recovery
figures require an actually completed, visible next P0 turn; sink clear does not
claim physical speaker silence. The [same-profile idle restart](same-profile-idle-restart.json) passed: a new
worker instance recovered ready health with the identical processing pin and
completed a new P0 probe. No old job or configured pin was relabelled.

The failed Ubuntu 26 candidate environment created during setup was removed
(about 9.01 billion allocated bytes), with diagnostics preserved privately. The
original Ubuntu distro and shared Ollama blob remain. This does not claim an
immediate shrink of the host VHD; see [cleanup evidence](storage-cleanup.json).
