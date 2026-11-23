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
  schedule. Of these, only authenticated idle Tasks P2 and [native capture P3](#native-capture-permit)
  can start during a realtime connection. They yield to pending foreground requests
  and detected speech before native start. A preparing step counts against its
  class's queue limit until it reaches the runner. A parked step returns to the
  head of its queue with the same deadline and request identity; fenced capture
  steps defer instead.
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
  active timeout without verified release and failed release verification close
  admission. Managed Strata P5 expires the subscriber after 10 minutes (including
  queue time) and keeps the original stream open for up to 30 more seconds to
  verify release. Late results are discarded. Other workloads keep the existing
  deadline behavior. Journal failure blocks new work; journal settlement still
  belongs to the same owner. Running and uncertain markers survive process loss.

Serial ownership and crash recovery apply to deployments that route generation
through one gateway and reopen its durable runtime root. An attached runner
remains independently managed. The gateway closes its
transport on shutdown and has no authority to unload the model or stop the
service. Existing task and memory owners continue to account for their own I/O
and external effects. The phase adds no background retry after native start.

## Native capture permit

The gateway advertises `idle_native_capture_v1` when configured with a distinct
`gateway-capture.token`. That credential admits only P3 with `context_id` matching
the sole realtime reservation and its active permit. Only the foreground credential
can set the permit via `PUT /v1/realtime/{session_id}/capture`. P3/P4 submitted
with `gateway-background.token` still start only without a realtime reservation.
Imports and unmarked requests cannot use the permit.

Reservation changes, foreground preparation/admission, and detected speech fence
the permit. Queued or preparing capture steps defer with `idle_capture_deferred`
and cannot carry over into the next idle gap. Once native work starts, the step
still owns the slot through verified drain and journal settlement. This does not
use Strata's P5 preemption path. See [Agent ADR 0006](0006-phase-eight-calendar-and-capture.md#connected-idle-native-capture)
for resumption and acceptance.
