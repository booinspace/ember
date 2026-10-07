# Local Qwen profiles

The phase 0a inventory observed Ollama 0.40.0 and both user-supplied tags on
2026-10-07. Model metadata and build artifacts were read without generation or
loading a different runner. Detailed digests, machine paths and the observation
snapshot are in ignored `.local/config/` and `.local/reports/`.

| Profile | Ollama tag | Initial context | Build setting |
|---|---|---|---|
| Reference | `qwen3.8-heretic-ara:27b-q4_k_m` | 8192 | Q4_K_M text, BF16 projector, draft tokens 0 |
| MTP candidate | `qwen3.8-heretic-ara:27b-q4_k_m-mtp` | 8192 | Same text/projector, separate Q8_0 MTP draft, draft tokens 3 |

The supplied build's `Modelfile` and `Modelfile.mtp` establish these settings.
They specify renderer `qwen3.8`, parser `qwen3.5`, and `REQUIRES 0.40.0`.
The conversion manifest pins source revision
`2dc9b364104881cbb85e390f00195ba6b9d745e9` from
`heretic-org/Qwen3.8-27B-heretic-ara`. Its text-only stage metadata describes the
text conversion; the runtime Modelfiles separately include the vision projector.

Private deployable configs:

```powershell
uv run --locked ember doctor --config .local/config/deployment.non-mtp.toml --require-model-pin
uv run --locked ember doctor --config .local/config/deployment.mtp.toml --require-model-pin
```

The doctor validates configuration. Its runtime readiness remains unverified until
phase 3 probes the selected tag and digest again; a stored snapshot can become stale.
Use one profile at a time for the initial 3090 deployment. Inventory sizes are
stored-file sizes, not measurements of total loaded weights, KV cache, or VRAM.
The model-advertised maximum context does not change Ember's initial 8192 budget.

Phase 3 should compare both profiles on the same pinned prompts and context:
TTFT, streaming tokens/s, peak VRAM/KV behavior, visible/thinking/tool parsing,
structured outputs, cancellation/preemption, context-switch cost, and sustained
interactive latency. Measure cold load separately from warm generation. MTP's
three draft tokens are a build setting; no throughput benefit is assumed.

All generation, including warmup and benchmarks, must pass through the incremental
single inference gateway. P0 establishes interactive behavior first. Background
extraction waits for its scheduled request classes and validation pipeline. Neither
manual direct Ollama generation nor phase 0 inspection implements those contracts.
