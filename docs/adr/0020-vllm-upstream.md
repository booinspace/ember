# 0020 — Selectable, pinned vLLM behind the existing gateway

Status: implemented; native serial-GGUF conformance, real acceptance and full gateway benchmarks recorded.

The operator explicitly superseded the earlier wait-until-Phase-13 decision after
Phase 10. Backend adoption can now proceed after exact-artifact qualification;
serial admission and drain-on-cancellation remain intentional until later phases.
No concurrency or native abort is claimed by this adapter.

Ember keeps one selected `Upstream` behind its existing gateway. Neutral frames
move out of the Ollama module, retaining compatibility exports. Existing callers,
P0–P4 admission, reservations, task/memory ownership and bounded character context
remain unchanged. Configuration defaults to Ollama; an unavailable vLLM profile
fails closed without fallback. Windows uses an explicitly installed external WSL
Linux server. The HTTP client and gateway remain independent of the host OS.
Torch/CUDA/plugin dependencies stay outside Ember's environment.

The pinned vLLM 0.31 profile requires a verified static receipt for actual weights,
tokenizer/config/template bytes, plugin/tooling files, software versions, exact
arguments and qualified execution environment. The launcher prevents abbreviated
or noncanonical flags and inherited kernel/frontend overrides. Its mandatory
Python ASGI guard authenticates every metadata/generation route and denies runner
administration even with the key. Development mode is needed for engine metadata;
the server's own API-key middleware alone does not protect those routes.

A versioned identity combines this receipt with stable effective engine settings.
Instance IDs, allocation observations, worker ports and compilation counters are
not durable model identity. A separate per-generation instance check detects a
restart during execution. Changing precision, weights, template/parser, plugin or
profile creates a new pin, so old extraction jobs cannot silently resume against
a different inference setup. Memory ABI and canonical schemas do not change.

The transport validates bounded SSE, carries fragmented tool identities, separates
opt-in ephemeral reasoning, waits for final usage and `[DONE]`, and closes the
response before reporting native completion. Structured/background/tool output
ending at its token limit fails before publication even when it happens to parse
as JSON. Ordinary bounded text retains existing truncation behavior. Decode rate
uses `(completion_tokens-1)/generation_interval`; aggregate throughput including
prefill is not relabelled decode speed. Missing native timing remains unknown.

[Implementation/runbook](../vllm-implementation.md) owns qualification, operational
limits, independent review findings and measured results. [Agent ADR 0010](../../ember-agent/docs/adr/0010-vllm-upstream.md)
records the component contract. The earlier [investigation](../inference-backend-evaluation.md)
remains historical evidence, with its adoption gate superseded explicitly.
