# Agent ADR 0014 — Gateway release and realtime ownership

This decision covers conservative admission and the Strata voice candidate. See
[Root ADR 0024](../../../docs/adr/0024-phase-thirteen-inference-hardening.md)
and [Phase 13 validation](../../../docs/phase-13-validation.md).

- The gateway owns a request from verification through the last journal write.
  P0 still reports preflight rejection as an HTTP error. Dropping that HTTP
  handler withdraws the subscriber and allows the owner to settle the journal.
  The HTTP route observes disconnects during P0 preflight. The gateway bounds
  metadata verification by the request deadline and rechecks closure, deadline,
  subscription and realtime eligibility before any native generation.
- P1 requires its own realtime reservation. P2–P4 use the existing weighted FIFO
  schedule and run when the connected set is empty. A preparing step counts
  against its class's queue limit until it reaches the runner. When it loses
  eligibility before native work, it returns to the head of its own queue with
  the same deadline and request identity.
- P5 can use an idle realtime reservation. A P0 arrival parks a preparing P5
  before native work or waits for an active P5 to drain. A waiting P0 deadline
  withdraws the waiter while the active P5 keeps its owner. Coding streams
  remain client-visible during generation and are never replayed by the gateway.
- Optional Strata screenshot input stays on the bearer-authenticated P5 route.
  The OpenAI ingress and agent-authenticated P5 transport cap JSON at 8 MiB,
  without a separate image-count cap. The gateway validates each bounded inline
  raster off the realtime loop; Strata expands image tokens before enforcing the
  coding input limit. Non-coding workloads keep their existing limits. Pin the reader
  and projector. Vision profiles keep live preemption disabled; unexpected
  native failures still fence admission.
- In the Strata candidate, the connected LiveKit host claims a short renewable
  reservation on detected speech onset. The host holds it through preparation,
  model work, playback and a two-second quiet guard. A stale reservation expires.
  The gateway blocks new P5 work while the lease is held and cancels an active
  P5 through the owned runner. It reports `voice_preempted` only after verified
  release and durable completion. It discards remaining output and tool calls.
- Realtime connect and disconnect serialize with the durable session update.
  Reconnect cannot be lost behind a disconnect write. Failed runtime startup
  settles its acquisition before releasing any committed reservation. The
  startup deadline rejects late acquisition and cleanup can still wait for the
  owned drain. HTTP shutdown and process-signal shutdown retire queued responses
  before Uvicorn waits on connections. Shutdown joins both preparing and running
  work. HTTP response cleanup also covers disconnect before the first event.
- A hosted LiveKit turn checks its participant is still present after recording
  the input and before reacquiring the reservation. The matching SDK generation
  task settles the host's drain event even when cancelled before its body runs.
  The gateway still owns native completion if a subscriber disconnects.
- The live stream keeps its 32-event queue. A saturated queue gives the subscriber
  up to 100 ms to make space. A stalled subscriber detaches and the generation
  drains under the same owner.
- The gateway requires a terminal native frame, clean stream exhaustion and a
  verified loaded profile before treating a stream as drained. P2–P4 publish
  validated output only after the successful completion path. Transport loss,
  active timeout and failed release verification close admission. Journal failure
  reports an unavailable journal and prevents new work. Queued steps get a fixed
  failure code. Completion journaling settles under the same owner after a client
  deadline. An expired request publishes an error once settlement finishes. A
  persisted running or uncertain marker survives process loss.

Serial ownership and crash recovery apply to deployments that route generation
through one gateway and reopen its durable runtime root. An attached runner
remains independently managed. The gateway closes its
transport on shutdown and has no authority to unload the model or stop the
service. Existing task and memory owners continue to account for their own I/O
and external effects. The phase adds no background retry after native start.
