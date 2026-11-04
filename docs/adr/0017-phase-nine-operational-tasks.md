# 0017 — Phase 9 operational tasks and gateway admission

Status: in progress. Isolated baseline a15f238. Phase 8A is parked.

Ember Agent owns task state in runtime.db under the runtime root. It binds
submissions to a character, operator session and originating turn. The same
character and request key recover the original handle across a restart. A
changed operation or input is a conflict. The dispatcher claims one task at a
time and keeps at most 32 queued tasks for a character. Interrupted active
work becomes uncertain on restart. Terminal state, result, receipt and outbox
commit together. The outbox survives runtime restarts. The UI acknowledges
each notification explicitly and the event cursor coalesces progress.

The supported task providers count words through a scoped read-only tool,
summarize operator text through the P2 gateway, and invoke existing Deep Recall
with a task-bound context. The memory engine and ABI stay unchanged. The recall
payload remains in the existing volatile cache and receives current permission
validation on inspection. Task results do not enter conversation or canonical
memory automatically. A separate operator action saves a completed non-recall
summary as a managed text artifact. The runtime checks its scope, bytes, digest
and size on download.

[Agent ADR 0007](../../ember-agent/docs/adr/0007-task-runtime-and-agent-admission.md)
records dispatch, admission, safety and scalability decisions.
[Phase 9 runbook](../phase-9-runbook.md) describes the supported entry points.

| Criterion | Current evidence and open work |
|---|---|
| P9-AC-01 | Authenticated operator and explicit conversation requests bind scope. Tool schemas, capabilities, deadlines and receipts are validated. General model-selected dispatch and external providers remain open. |
| P9-AC-02 | Concurrent retries, restart retry, queue limits, progress state, cancellation and terminal CAS are implemented. Provider-side recovery checkpoints remain open. |
| P9-AC-03 | Operational result, receipt, notification and export authorization are durable. Claimed attempts and terminal outcomes are durable. Provider checkpoints and confirmation state remain open. |
| P9-AC-04 | Coalesced changes and durable UI delivery are implemented. Speech-aware announcement and recovery from uncertain playout remain open. |
| P9-AC-05 | Scoped P1/P2 admission, single upstream, live reservations, queue timing, buffered P2 and weighted dispatch are implemented. Real short-turn RVN smoke passed. Full contention and audio measurements remain open. |
| P9-AC-06 | Reconciliation marks interrupted work uncertain, cancellation suppresses late read-only results, and artifact downloads reject modified bytes. Generic provider citation and effect validation remain open. |
| P9-AC-07 | Explicit task-driven Deep Recall reuses the Phase 8 service and separate context. Ordinary chat cannot inspect a task result. |

Windows synthetic checks used an isolated temporary SQLite store and a fake
upstream. Across 250 samples, durable submit measured 37.3 ms median and
58.4 ms p95. Fake-runner foreground latency was 0.67 ms p95 without queued
P2 and 0.64 ms p95 with queued P2. The real RVN Ollama check at 32K used ten
warm short prompts per case. Median time to first text was 96.3 ms without
queued P2 and 95.4 ms with P2 queued, with maxima of 99.6 and 100.8 ms.
P1 and P2 each completed a real request. These samples measure a reserved
foreground lane, with P2 waiting for release. They do not characterize
preemption, physical audio, or long-context throughput.

Review fixed a cancellation/completion race, a lost admission test, detached
progress writes, pending P1 wakeups without eligible reservations, unstable
idempotency across runtime restarts, and duplicate inspector DOM anchors.

The full Python suite with the installed LiveKit server enabled reported
684 passed and seven opt-in skips. The final targeted gateway, task and
browser API rerun reported 44 passed. Ruff, strict Pyright, schema checks,
18 frontend tests, the production build and three installed-Edge browser
checks passed.

A separate managed startup with synthetic data and the installed speech
providers reached LiveKit voice readiness in 11.9 seconds at the configured
128K context. The test runtime and its children were shut down. The LiveKit
SDK emitted a destructor assertion after shutdown. A separate short Ollama
generation verified context 131,072 and 100% GPU residency. Neither check
exercised a near-limit prompt or physical microphone.

The runtime now records each claimed attempt and its completion or uncertain
restart outcome atomically with task transitions. P2 tasks use the gateway's
bounded 180-second wait rather than an eight-second timeout, allowing queued
work to survive a brief live reservation.

Cancellation is now checked at each persisted progress boundary. Once the
operator has requested cancellation, the next step is fenced and the
terminal cancellation and attempt outcome are committed together. A
cancellation racing an off-loop SQLite transition observes the actual
transition before recording the terminal state.

The operational database can persist a pending confirmation with exact
canonical tool arguments, an action hash, character scope and a deadline no
later than the task deadline. Decisions are idempotent and reject changed
arguments, changed decisions, cancelled tasks and other characters. The
confirmation store is not yet wired to a provider or operator interface and
therefore does not close the Phase 9 confirmation criterion.

A single allowlisted count tool can be selected by the existing P0 model on a
user request containing count. The tool's text must be an exact substring of
the initiating input, and trusted runtime code binds task scope. No extra model
call runs for routing. The tool schema is omitted if it would exceed the input
guard or when thinking mode is active. Synthetic tests cover successful routing
and rejection of model-invented text. General tool selection remains open.

A draft-review provider now requests confirmation of the exact draft and the
authenticated task panel displays its arguments before accepting a decision.
Uncertain active work is not replayed after a restart. Explicit conversational
task-update questions use the existing acknowledged text/voice turn and only
report a count; notifications remain pending until dismissed. Automatic
proactive speech is not enabled.

Task results with citations are accepted only when every citation belongs to
the provided memory dependency certificate and the existing character client
validates it. Status, notifications and artifact reads revalidate that
certificate. Invalidated results do not disclose their stored summary.
Synthetic tests cover valid scope, out-of-scope citations, later revocation,
and artifact denial. The provider boundary remains read-only.

The character now rejects model tool calls when no tool schema was advertised
on the initiating gateway request. A synthetic regression covers this alongside
the existing user-text scope checks. General routing remains open.

The LiveKit runtime now registers model-selectable count, summary and Deep
Recall routes. Summary is offered only when the agent gateway is configured.
The existing P0 generation can select one advertised route, with arguments
restricted to text from the initiating user turn. Trusted code supplies the
operation and turn identity. The task still runs through the existing dispatcher.
The focused character, gateway, task-store and browser API run passed 51 tests.
This extends native routing while provider recovery and speech delivery remain open.
