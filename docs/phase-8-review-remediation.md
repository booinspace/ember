# Phase 8 review remediation — 2026-10-16

Baseline: current `ember` checkout at `1107679`, including its existing staged,
unstaged and untracked Phase 8 work. The earlier `ember-phase-eight` clone is not
the review baseline. Production stores and services are outside this validation.

## Behavior and ownership before implementation

Native acceptance must consider the original source's qualification, even when
an extractor selects a shorter exact quote and labels it factual. The existing
canonical native acceptance service owns this check; model output has no new
authority. Unsupported hypothetical, conditional, fictional or reported claims
remain deferred for operator review. Direct supported assertions still pass.
This corrects enforcement of the existing native policy without changing pinned
job/model/prompt/schema hashes, replaying receipts or modifying accepted history.

Reuse exact source resolution and the existing transactional acceptance path.
No database, scheduler, writer, provider or public ABI is introduced. Source
inspection is bounded by the existing source-part contract and happens on the
canonical worker, outside realtime generation.

Regression tests must exercise durable source capture, structured extraction and
native acceptance with an extractor that deliberately omits the qualifier. Cover
both legacy and dated profiles, factual controls, restart visibility and no
accepted belief for the unsupported claim. Use synthetic sources only.

The current root test gate also fails during collection because a LAN test imports
a later-collected test module from a colliding `tests` package. Give that test its
own minimal control mock rather than relying on collection order.

The final review will distinguish implemented behavior, verified defects, fixes,
remaining acceptance evidence and later-phase recommendations. Existing benchmark
reports are historical evidence; this work does not claim new Qwen, microphone,
large-corpus or end-to-end latency measurements.

## Selected prompt dependencies and capture resumption

The composer currently retains source references and expiry from all retrieval
candidates after selecting a much smaller prompt. Reuse the existing metadata-only
`validate` operation with an optional negotiated `refresh_dependencies` flag. The
engine reconstructs hard evidence closure and effective time/grant boundaries for
the exact selected references, preserving explicit excerpt/archive dependencies,
historical selectors and current policy/profile epochs. The response uses its
existing dependency field. No source body, relationship or vector is hydrated.
Old clients keep conservative validation; old request serialization is unchanged
when the flag is absent. All reference/row/call/byte bounds remain enforced.

The composer retains profile-decision and retained-history time boundaries, then
requests this certificate only after whole-row/whole-turn trimming. Raw deep
excerpts are retained by their selected observation indices. Current pins are
excluded from deep rows before condensation. Regression tests must prove unused
expiry cannot block output, selected expiry/revocation still stop delivery,
support closure remains certified, old capability fallback works, and pins are not
duplicated.

Persisting capture policy is still composition-owned. The authenticated browser
signals the existing connection lifecycle after a successful policy write. The
same owned task resumes disconnected backlog; connected sessions still reserve
realtime and prohibit P3. No new poller, queue or inference scheduler is added.

## Degraded browser operation

PLAN section 4 requires browser text/control to survive unavailable LiveKit, and
control operation to survive unavailable gateway/model services. Startup currently
exits before publishing either. Split existing service checks so gateway readiness
can be established independently. When room setup is unavailable, serve the same
browser/control owner with a bounded HTTP text adapter over the existing core.
An unavailable gateway exposes controls with text explicitly disabled; no direct
upstream generation fallback is permitted.

The HTTP adapter owns one browser session lease, one active request and at most
128 replay identifiers. An authenticated heartbeat renews the transport lease;
disconnect, lease expiry and shutdown cancel/drain generation before releasing the
existing realtime reservation. This is transport ownership, not an inference
scheduler. Responses are buffered in text fallback; only a browser acknowledgement
records delivered output. Failed/unacknowledged output is discarded, while original
input remains durable. Deep answers reuse the same injected investigation service.

Tests must cover actual authenticated HTTP routes with synthetic canonical stores,
replay/overlap, explicit acknowledgement, disconnect/expiry draining, unavailable
model controls, and startup selection without contacting production services.
