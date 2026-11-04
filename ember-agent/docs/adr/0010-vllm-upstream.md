# 0010 — Explicit vLLM upstream profile

Status: implemented and qualified for the recorded single-GPU serial profile.

`inference.upstream` owns neutral internal frames and the existing `Upstream`
protocol. `ollama` preserves old imports. `service.upstream_for` selects exactly
one configured adapter; no caller gains runner administration or direct-model
fallback. Default configuration remains Ollama.

`vllm` supports the qualified 0.31 text/GGUF profile, not a generic OpenAI proxy.
It freshly verifies authenticated setup receipt, engine configuration, model,
context and artifact metadata before admission and after completed generation.
Its stable versioned processing pin differs from Ollama's manifest digest. Active
instance attestation is separate from durable identity. Unsupported speculative,
distributed or transfer profiles fail closed until separately qualified.

The existing gateway owns scheduling and cancellation. Subscriber detach still
drains; a transport failure with uncertain runner state still blocks admission.
Structured/background/tool truncation cannot publish a completed step. Native
per-request timing is normalized without inventing unknown counters. Bounded tool
fragments cannot execute before existing complete-call/schema validation.

The external launcher and authenticated route guard are explicit operator setup;
startup never installs vLLM, CUDA, a plugin or a model. They share a static receipt
that verifies input bytes and software versions and pins semantic environment.
Windows-specific WSL setup stays outside the cross-platform gateway and Core.
No SDK, backend or scheduler dependency enters Memory ABI.

See [Root ADR 0020](../../../docs/adr/0020-vllm-upstream.md) and
[validation/runbook](../../../docs/vllm-implementation.md).
