# Root ADR 0024 — Phase 13 inference hardening

Conservative admission is the selected Phase 13 mode on managed Strata. Live
preemption requires separate qualification of the selected runner profile.

[PLAN](../../PLAN.md#phase-13-acceptance-criteria-preemption-hardening) assigns
Phase 13 to the existing gateway. The gateway already serializes generation and
holds a realtime reservation for the entire connection. Keep that reservation
while voice is connected, including speech, inference and playback. A connecting
session waits for a running background step to drain. Workflows and non-model
I/O keep their existing task ownership.

A P0 handler could be cancelled while writing
the running marker. A background request removed from its queue for verification
could also start after shutdown or a realtime state change, or be parked into a
full queue. Both paths now use one gateway-owned admission and generation
lifetime. The owner checks the deadline and readiness again before starting
native work. Realtime journal updates serialize reconnect and release, and
preparing work retains capacity to return to its queue. See
[Agent ADR 0014](../../ember-agent/docs/adr/0014-gateway-release-and-realtime-ownership.md).

The LiveKit host checks the authorized participant is still present before
reserving capacity for a prepared turn. Its SDK generation task settles the
matching delivery fence even when the stream closes before the task runs.
The desktop RTC follow-up exercised both disconnects with the updated host.

The active generation retains its slot through native stream completion and
post-generation metadata verification. The gateway discards incomplete P2–P4
output and closes admission when it cannot account for capacity. On crash, the
existing journal requires operator-confirmed quiescence before another generation.
Only a request that has never reached the runner can return to its queue. Task
checkpoints and the tool broker still own external effects.

Managed Strata uses the same single-slot owner. The gateway preserves Strata's
rendered-prompt token budget for coding work. An authenticated tokenizer refusal
before generation leaves the slot available. The scheduler can admit P5 coding
work during an idle realtime connection. A waiting foreground turn goes before
queued P5 work and waits for any active P5 request to drain within its deadline.
The opt-in screenshot profile uses this same owner for image preparation and
text generation. Coding clients can replay image history without a separate
image-count cap. The authenticated coding route and internal P5 transport admit
up to 8 MiB of JSON; per-image checks and Strata's rendered input budget remain
in force. Non-coding limits are unchanged. Vision is not qualified for live
preemption; image-heavy coding work can delay voice until the active request drains.

P5 has a 10-minute request deadline, including queueing. On managed Strata,
expiry detaches the subscriber but preserves the original stream for up to 30
seconds to obtain normal release proof. Verified completion and durable
settlement reopen admission; unverified release retains the journal fence.
This neither stops the runner nor changes voice deadlines or enables preemption.

The Strata voice candidate uses the managed runner's existing native STOP
path. A private authenticated request binds Ember's P5 identity to that
handler's cancellation event. The runner records native DONE on the same
engine process and confirms that the handler released admission. The
gateway also requires the SSE terminal frame, transport exhaustion, loaded
profile verification and durable completion before scheduling voice. A
missing receipt fences admission. The candidate applies to active P5 only.
[Pinned Strata server](https://github.com/Niko1221/Strata/blob/v0.2.18/terminal/serve/server.py)

The normal desktop profile remains conservative. The current pinned profile
passes request-specific cancellation and same-runner slot reuse. Its long
prefill fails the matched first-PCM latency gate. Activation requires a
profile that meets the voice gate and the remaining acceptance criteria.

An exclusive desktop probe verified task-specific cancellation and slot reuse
through the named llama-server child and its log. The application still lacks a
production release contract that binds the requesting deployment to that native
slot. The attached Ollama instance also served independent LAN completions during
qualification failures. A tested exclusive probe cannot authorize a shared
runner to accept live preemption by default.

The [Phase 13 acceptance record](../phase-13-validation.md) maps the current
tests and measurements to each gate and retains the historical Ollama evidence.
The port uses the existing gateway wire format and journal.

## Idle Tasks admission

On the updated gateway, the agent's authenticated Tasks client can mark P2
steps for idle admission. Inspect, Build, Web Research, and text Summary use
this path while Conversation stays connected. Pending foreground requests and
detected speech hold new idle P2 steps, not active native generation. A running
step retains the single slot through verified release and journal settlement.
Unmarked P2 and automatic P3 capture keep conservative admission. The
`idle_managed_tasks_v1` health feature lets the dispatcher distinguish updated
gateways from older ones without changing inference or tool permissions.
