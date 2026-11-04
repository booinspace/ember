# Phase 12 qualification and latency

This is an integration qualification record, not a replacement for the gateway's
large inference benchmark suite. No inference backend/model/context setting changed.
Catalog discovery, document inspection and enablement run outside the GPU inference
path. Browsing cached metadata does not run a model. Full gateway benchmark scenarios
remain available through the existing benchmark runner.

The desktop uses native Windows Ember/Core/voice/memory/gateway and an isolated
Ubuntu WSL2 Hermes worker, SDK revision `71736c9f32597bbca9d89cede5aa136e55946b2f`.
The existing RTX 3090 model is `qwen3.8-rvn:27b-iq3_s-mtp`, digest
`5b869f160df08fa13860487d8c341d3db22a9b3aca48e67dced4b53370a3cfd2`.
Embedded MTP remains active. No second GPU model/server or duplicate weight file was
created. Juniper/1.1x CPU speech and existing data roots are preserved.

See `qualification.json` for final platform counts, profile and bridge asset hashes,
actual-model task latency, serialized prompt cost and browser proof flags. Timings
are single qualification samples including worker verification, native loading,
model/tool rounds and result authorization; they are not p50/p95 throughput claims.
The catalog includes 188 packages; 136 are unavailable under current requirements
or resource limits and 52 are available for operator review. Only shared `text-brief` and `page-summary` are
enabled; discovery does not independently qualify all bundled skills.

Native SDK tests use three synthetic completions to prove progressive loading and
repeat-load behavior without allocating GPU inference. Actual text/page tasks run
separately through Ember's P2 gateway. EOF/cancel/controller-crash tests each deny
11 forbidden operations and verify namespace cleanup and temporary-context purge.
Their cleanup times describe the worker boundary, not a physical GPU generation abort.

Before qualification passed, the real SDK exposed a built-in skill dispatch bypass;
real Windows tests exposed an event-publication race; real gateway tasks exposed
receipt headroom and an unsupported schema variant. Those trials are documented in
the [acceptance record](../../phase-12-validation.md); failures were fixed rather
than counted as passes. Startup's transient realtime reservation must drain before
P2 tests; no active user reservation is released to force the result.

No private memory excerpts, raw prompts, hidden reasoning, tokens, deployment TOML,
transcript screenshots or task outputs are included here. Physical microphone,
Desktop and OpenClaw runtime qualification remain outside this phase.

The real API qualification measured 2.360 seconds for lazy document inspection.
The two real P2 cases each measured 57.094 seconds end to end in this run; final
serialized inputs were 8,480 and 10,470 units against 16,384. These asynchronous
worker timings are not realtime voice latency. Plain literal URL summaries use
one foreground fetch/reply and do not wait for P2 admission.

Final gates: macOS 1,214 passed / 29 skipped (118.50 s); Windows 1,212 passed /
31 skipped (309.40 s), including Ruff and strict Pyright. Frontend 31 unit tests,
TypeScript/Vite build and 20 browser checks passed (3 opt-in checks skipped).
Native pinned-SDK/WSL suite: 8 passed in 36.63 s. The installed Edge HTTPS proof
confirmed catalog/raw procedure/use controls, conversational selected-skill task
completion, public-page activity and exact P0 input (9,569 / 24,576 units),
read-ahead text while speech was active and successful Stop speaking. No page errors.
