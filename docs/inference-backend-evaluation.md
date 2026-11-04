# Inference backend evaluation for the RTX 3090

Status: historical investigation, researched 2026-11-04. The operator subsequently
superseded the wait-until-Phase-13 adoption gate and authorized an optional vLLM
adapter after Phase 10, retaining serial admission and drain-on-cancellation.
[Implementation and qualification](vllm-implementation.md) records current code,
installation and actual measurements. The analysis below remains evidence of the
original deployment and tradeoffs; it does not establish comparative performance.
Native preemption and concurrent scheduling remain later acceptance work.

## Recommendation

Keep Ollama as the production baseline. First measure its current configuration,
then compare native CUDA llama.cpp using the identical GGUF. Include vLLM as an
optional WSL2/Linux challenger after the exact artifact passes compatibility and
memory checks. Do not choose a server from aggregate throughput claims alone:
Ember's primary outcome is the latency of an interrupted or warm voice turn.

vLLM can improve cache utilization and scheduling of concurrent requests to one
model. It is not established that it will improve this 27B IQ3 model's single-turn
decode speed, fit more model weights into 24 GiB, or improve voice latency. The
current one-generation gateway deliberately prevents concurrent batches; a backend
adapter and a change to that admission policy are separate pieces of work.

## Observed deployment and existing boundaries

Read-only desktop observations after synchronizing main to `d197715`:

- RTX 3090, 24,576 MiB dedicated VRAM; Windows NVIDIA driver 616.92.
- Ollama reports version 0.40.14 and runner `llamacpp` for the configured model.
- Startup uses `.local/config/deployment.non-mtp.toml`. Its profile label is
  `primary-non-mtp`, but its model tag is `qwen3.8-rvn:27b-iq3_s-mtp`.
  Labels do not prove whether speculative decoding is enabled. Record actual
  runner arguments and MTP acceptance statistics before comparing MTP.
- Metadata reports GGUF, IQ3_S, 27.3B parameters and architecture `qwen35`, including
  attention and recurrent/SSM metadata. Test this hybrid architecture rather than
  assuming the public-facing model name identifies an ordinary Transformer.
- Configured context is 131,072, maximum input 126,976, output reserve 4,096.
  At inspection `/api/ps` contained no resident model and total GPU use was
  2,568 MiB. That is an unloaded desktop snapshot, **not** the model's VRAM
  footprint or proof that another model fits. The next turn may include loading.
- Authenticated gateway health and the HTTPS UI returned 200; voice was ready.
  No generation, service restart or model load was performed for this review.

The Phase 9 ADR already records a small real-runner smoke test at 32K: roughly
96 ms median first-text latency across ten warm short prompts per condition,
with P2 waiting behind a reservation. That is useful prior smoke evidence, not
a new result for this exact current configuration, long prompts or audio.
See [the recorded limits](adr/0017-phase-nine-operational-tasks.md).

The relevant implementation is:

| Owner | Current behavior | Consequence for another runner |
|---|---|---|
| `ember-agent/.../inference/ollama.py` | `Upstream` protocol, Ollama metadata checks, `/api/chat` NDJSON, JSON/tools/thinking mapping, five-minute keep-alive | Reuse the boundary; extract neutral frame/protocol types from this provider-named module |
| `inference/service.py` | Constructs `OllamaUpstream`; owns gateway lifetime/journal | Select exactly one explicitly configured adapter here |
| `inference/gateway.py` | One active generation, allowlisted model/digest, P0–P4 admission, live reservations, weighted P2:P3:P4 dispatch | Preserve semantic admission; a second server must not bypass the owner |
| `inference/contracts.py` and `client.py` | Bounded requests, completed JSON/tools, usage, ordered streams and explicit end | Keep callers on these contracts; normalize provider streams behind them |
| `character/core.py` | Whole-prompt conservative cost ceiling 24,576; separate bounded history and recall | Keep these limits; server context capacity never authorizes sending the whole chat |
| `ember-memory/.../processing_gateway.py` | Scoped P3/P4 client; validates job model pins and completed structured results | No direct model dependency or new memory-side scheduler |

The 24,576 ceiling is a conservative serialized-input accounting limit, not a
claim that each turn contains 24,576 tokenizer tokens. History and memory quotas
are included within the overall bound. Actual tokenizer usage and output reserve
must still fit the selected runner's context, including templates and tools.

## Where vLLM could help

Paged cache allocation can use the cache budget more effectively across requests.
Prefix caching can avoid repeating common prompt prefill. Chunked prefill can
interleave prompt processing with decode, and continuous batching can increase
aggregate throughput. Those are useful candidates for future P2/P3 work, but
sharing the GPU can still worsen an individual voice turn. These benefits depend
on the selected model and supported feature combination. See the official
[optimization guide](https://docs.vllm.ai/en/latest/configuration/optimization/).

Stable shared instructions and character prompts are potential prefix-cache hits.
Changing recall, clipped history, templates or prompt versions can break the shared
prefix. Cache reuse accelerates prefill, not the generation of an arbitrary new
answer. The current hybrid model needs its recurrent-state caching tested too;
the [prefix-cache documentation](https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/)
describes additional hybrid-model constraints. Keep scopes/authorization intact
and never enlarge or reorder private context solely to improve cache hits.

vLLM has a priority scheduler, but it cannot own Ember's authorization, voice
reservation, durable task state or memory-job fairness. Memory-pressure
recomputation inside a runner is also different from Ember proving that an
interrupted request released capacity. See
[scheduler and memory arguments](https://docs.vllm.ai/en/latest/configuration/engine_args/).

### GGUF and Windows qualification

Current official docs call GGUF support experimental and under-optimized, and now
direct users to the out-of-tree `vllm-gguf-plugin`. Generic model support does not
establish support for a particular GGUF. The plugin documents Qwen 3.5 GGUF and
embedded MTP examples; its published coverage does not certify this custom 27B
IQ3_S artifact. Pin vLLM, plugin, PyTorch/CUDA, tokenizer and chat template; verify
tensor mapping, all quantized tensor types, recurrent layers, structured output,
tools and reasoning separation before timing it. Use the matching base tokenizer
and explicit trusted template, avoiding guessed conversion.
[GGUF docs](https://docs.vllm.ai/en/latest/features/quantization/gguf/),
[plugin coverage](https://github.com/vllm-project/vllm-gguf-plugin#tested-model-coverage).

Official CUDA installation targets Linux; native Windows is unsupported. WSL2 is
the documented Windows route. The 3090 meets the general CUDA compute-capability
requirement, but that does not certify every quantization or recurrent kernel.
Run a separate pinned environment/service, not PyTorch/vLLM inside Ember's Python
environment. Measure WSL memory, startup, networking and shutdown overhead too.
[Installation requirements](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/).

Do not assume a 4-bit AWQ/GPTQ replacement is equivalent to IQ3_S. Different
weights, quantization or templates are a separate quality-and-memory experiment,
not a matched runner comparison. Do not dequantize and requantize this GGUF as a
shortcut to obtaining an allegedly equivalent checkpoint.

## Memory and multiple models

Use the measured budget:

`weights + attention KV + recurrent state + workspaces/graphs + other models + desktop headroom < usable VRAM`

PagedAttention does not compress all weights or eliminate per-request state.
Two independent model servers normally retain two sets of weights. Two
characters using the same weights and different prompts need isolated contexts,
not two copies of the model. A genuinely different second model needs its own
weight/cache/workspace budget and a shared admission policy even on another port.

Do not apply the full-attention KV formula to all 65 reported blocks: this
artifact is hybrid. Read actual attention-layer layout and measured runner state
allocations, including MTP, before predicting 128K memory. Also distinguish GGUF
file size, loaded weights, cache capacity, peak dedicated VRAM and shared-memory
spill. WDDM and WSL reporting can make per-process accounting incomplete.

First experiments, changing one variable at a time:

1. Compare 8K and 32K against the existing 128K profile using input that fits each
   profile. A smaller context is a candidate memory saving, not a pre-approved
   production reduction. Preserve explicit overflow rejection and output reserve.
   The separate Hermes 64K qualification remains separate; do not spoof its size.
2. Measure supported Flash Attention/cache precision choices and actual GPU
   offload. Lower-precision cache requires recall/tool/JSON accuracy checks.
   Keep CPU speech and embeddings off the GPU.
3. Measure five-minute keep-alive expiry versus bounded residency. Keeping weights
   resident may improve the next turn while occupying memory needed by another
   model. Report cold and warm results separately.
4. Test MTP on/off independently. Extra state and verification can cost more than
   the saved decode steps on short answers; record accepted draft-token counts.
5. Only after single-model acceptance, measure a smaller second model with explicit
   total VRAM budgets and load/unload policy. Do not promise two 27B models fit.
   CPU offload is an explicit latency tradeoff, not a free capacity increase.

Ollama already supports parallel requests/multiple loaded models when memory
permits; it is Ember's current admission contract that keeps this deployment
serial. Ollama documents the context-memory multiplier for parallel requests.
[Ollama FAQ](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests).
For vLLM, set explicit memory budgets per instance; its utilization setting is
per instance, not a shared allocator across processes. The cache-byte setting
controls cache, not a hard cap on all process VRAM. Derive headroom from measured
peaks rather than using each server's automatic near-full-GPU allocation.

Native CUDA [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)
is the first challenger because it accepts the existing artifact and exposes
cache/offload controls, batching, JSON and tools. Ollama already uses llama.cpp
for this model, so a large improvement is not guaranteed; a different build or
better configuration could explain any gain. Compare these separately.

## P0–P4 and durable memory processing

These labels are scheduling classes, not successive extraction stages:

| Class | Work | Rule retained during a backend experiment |
|---|---|---|
| P0 | Active conversation | Highest priority; bounded streaming text/voice |
| P1 | Essential live-turn model work | Requires a valid live reservation; no untrusted priority promotion |
| P2 | Delegated workflow model steps | Bounded fair admission and completed buffered results; currently waits while any live session is reserved |
| P3 | Extraction, consolidation, summaries | Validated structured completion; no admission while a live session is reserved in conservative mode |
| P4 | Bulk model processing | Same conservative restriction; bounded queue and immutable checkpoints |

CPU recall, canonical memory storage, dates/evidence and policy do not become
vLLM responsibilities. A backend change cannot repair missing event dates or
retrieval coverage. It may change extraction speed and the claims emitted, even
with identical weights, because templates, samplers and kernels can differ.

Existing extraction specs pin model/digest, source snapshot, prompt, schema and
policy. The current digest is verified against an Ollama model manifest; it is
not automatically the GGUF file hash. Preserve old job specs and receipts. Map
the underlying artifact for comparisons, and create an explicitly versioned new
processing profile/job for a backend change. Do not fabricate Ollama metadata or
silently resume an old job on another provider. Include runner/template/tokenizer
and sampling identity in the new profile and checkpoint validation; any required
processing-contract change needs its normal versioning tests. No wholesale
re-extraction or canonical schema migration is implied by adding an adapter.

Continue buffering background JSON until successful validated completion. Abort
must discard partial claims and retry only the immutable model step; it must not
replay tools or grant generated claims write authority. With concurrency enabled
later, profile changes, retries and model routing must remain attributable.

Today `Generation.events()` detaches the subscriber while `_pump` continues
draining the upstream. This protects single-runner ownership, but an interrupted
reply may still consume GPU time; a new P0 request can see `busy`. Stopping audio,
stopping streamed text and freeing model capacity are different observations.
Do not replace this with HTTP disconnect and assume the GPU is free.

## Small implementation sequence

1. **Measure the baseline first.** Add a synthetic gateway benchmark using the
   existing `HttpGatewayClient` and usage fields. Add only the missing stage
   timestamps, raw metric output and GPU sampling needed below. Existing Phase
   7/8 scripts measure memory/recall contention; they do not establish this runner
   comparison or end-to-end interruption performance.
2. **One alternate upstream.** Extract neutral protocol/frame types without
   changing semantics. Add explicit backend selection to deployment parsing,
   service construction, health/model identity and installed-service lifecycle.
   Implement llama.cpp first, then vLLM if its artifact qualification passes.
   Keep one primary runner selected at startup and Ollama rollback; no runtime
   model browser, silent fallback or multi-provider registry is necessary.
3. **Compatibility tests.** Exercise SSE/NDJSON framing, fragmented tools, JSON
   schema, hidden reasoning separation, usage, stop/truncation, overflow, model
   pins, transport failure and incomplete EOF. An OpenAI-compatible URL alone
   does not establish behavior. Unknown metrics remain null, not invented values.
4. **Measure on Windows hardware.** Run the paired suite below through the sole
   gateway with isolated data/runtime roots and one resident runner. Provision
   explicitly; no dependency downloads or environment changes during startup.
5. **Cancellation and concurrency separately.** Phase 9 keeps its existing task
   boundaries. Phase 13 qualifies actual backend abort/release per pinned
   hardware profile. Only then propose bounded concurrent admission and
   multi-model routing as a further PLAN amendment. Internal runner scheduling
   cannot bypass Ember's live reservation or release journal.

The adapter is contained work. Accurate identity/metrics and tools/JSON/template
compatibility make it more than an endpoint edit. Concurrent models and reliable
preemption are the larger change; keep them out of the first adapter diff.

## Benchmark contract before any production switch

The baseline gateway/SDK benchmark is now implemented in Phase 9; see its
[commands and limits](phase-9-runbook.md) and [measurements](phase-9-validation.md).
The broader multi-runner comparison below remains an acceptance methodology,
not comparative evidence that a candidate is faster. Use synthetic prompts, memories and audio; no primary-memory root, private
conversation capture or hidden reasoning in reports. Every model request,
including warmup and contention, passes through the one gateway. Do not point
vendor load generators directly at the primary runner.

| Measurement | Required definition |
|---|---|
| TTFT | Client submission to first generated token where observable; record queue/admission and upstream start separately. Also record first **visible** text; a reasoning frame or SSE chunk is not a visible token |
| Decode tokens/s | Actual generated-token count divided by provider's decode duration where available. Also report an explicitly labelled client-observed rate over first-to-last generated token; never count chunks as tokens or substitute total-request time for decode time |
| Warm turn | Synthetic speech end → final STT → recall/prompt ready → queue → first visible UI text → first audible speech; record last text and playback completion separately |
| VRAM/RAM | Unloaded desktop, loaded idle, post-warmup, peak prompt/decode/contention, after cancellation and after idle expiry; GPU total dedicated/shared and available process metrics, host/WSL RAM, CPU spill and sampling interval |
| Interruption | Injected user speech/stop event → last actually audible sample, text suppression, adapter abort acknowledgement, and verified capacity reusable by next P0. Record all four, including timeout/unknown release |
| Throughput/fairness | Successful completed requests/output tokens per second by workload, queue delay/tails, deadline misses, retries and preemptions; show P0 latency beside any aggregate gain |
| Correctness | Tools, JSON schema, instruction/personality and bounded-session continuity, recalled synthetic dates/evidence, isolation, no partial background commits or late audio/text after interruption |

The existing gateway's `first_visible_seconds` starts at `_pump`, so it excludes
queue time. `tokens_per_second` uses Ollama's eval counters/duration; other servers
may not report equivalent decode timings. Add content-free stage measurements and
explicit metric origin rather than coercing incompatible timings into that field.
Buffered P2/JSON publication is completion latency, not backend TTFT; instrument
the upstream boundary for those cases. Count reasoning tokens separately if
reported, without retaining their content.

Pin artifact SHA, Ollama manifest association, tokenizer/template, runner/plugin
builds, CUDA/driver, sampling/seed, MTP, cache precision/capacity, context, offload,
prompt/output lengths, CPU speech versions and desktop GPU load in a run manifest.
Keep matched GGUF tests separate from alternate quantizations and models. Sample
NVML/`nvidia-smi` at a documented interval and retain runner allocation statistics;
polling alone can miss short peaks and low GPU utilization does not prove release.

Run fixed short/medium/long prompt buckets (about 256/2K/8K actual input tokens,
plus near-boundary rejection cases), short 64-token and longer 256-token replies,
stable-prefix and changed-recall multi-turn cases. An 8K input plus output does
not fit an 8K context: reject or use a smaller matching bucket. Exercise the real
character path within its smaller bound; larger inputs test only gateway/runner
capacity. Include 64K qualification separately only where the planned harness
requires it. Compare contexts and MTP one variable at a time.

Use at least three cold starts and 30 warm samples per condition as an initial
screen, alternate runner order, then at least 100 warm samples per finalist case
for p50/p95 acceptance. Publish sample counts/raw sanitized timings and variation;
label p99 exploratory unless enough repetitions support it. Record failures and
timeouts instead of dropping them. Baseline/candidate must receive the same
synthetic requests and comparable output lengths; run repeated paired blocks.

Contention cases: P0 alone; P2 backlog; P3/P4 backlog with a reserved session;
voice reservation during an already active background step; cancellation during
prefill and decode; repeated barge-in; reconnect and uncertain release. In the
initial conservative profile queued background work must stay blocked. Benchmark
actual shared execution only after a separately authorized concurrent profile
exists. An abort test cannot clear an uncertain journal just to run its probe:
the runner adapter must establish release before the gateway admits that probe.

Retain Phase 8A's existing switch gate: at least **15% lower warm p95 first-visible
latency** on the matched interactive suite, meeting existing realtime targets and
without material voice/tool/JSON/cancellation regressions. A memory-saving or
throughput-only winner requires an explicit revised decision, not a disguised
latency win. Keep Phase 13's **at most 200 ms added warm voice p95** under contention
for any profile advertised as supporting live preemption. Establish measured
baseline and explicit audio-stop/capacity-release budgets before implementing
that profile; report unsupported cancellation honestly and retain conservative
mode. Do not use throughput gains to waive voice or correctness failures.

Publish sanitized raw samples, methodology, pass/fail decisions and rollback in
an acceptance ADR. Until then the result is a proposal, not evidence that vLLM is
faster or that multiple models fit.
