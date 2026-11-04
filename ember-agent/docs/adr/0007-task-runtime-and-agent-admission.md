# 0007 — Operational dispatch and agent inference admission

Status: implemented native Phase 9 foundations. See
[Root ADR 0017](../../../docs/adr/0017-phase-nine-operational-tasks.md) and
[validation](../../../docs/phase-9-validation.md) for scope and evidence.

TaskRouter owns submit/status/cancel/result/event lifecycle and one dispatcher.
Installed providers execute bounded native steps; ToolRouter checks capabilities,
authority, typed input and receipt context. Model routing uses the existing P0
call and one advertised literal-input route, with no auxiliary routing model.
External MCP and harness adapters belong to their later phases.

SQLite runs off the realtime loop. Transactions bind idempotency, attempts,
progress, confirmations, effect fences, results and terminal outbox. Exact action
hashes bind operator approval. Fenced effects cannot repeat after cancellation,
transport failure or restart. Active attempts become uncertain, while untouched
queued requests retain their original scope and deadline. No speculative provider
recovery or automatic tool replay occurs.

Dependency-bearing results are validated before completion and every disclosure.
Citations must belong to the supplied certificate. Revoked or malformed results
cannot expose summaries or artifacts. Stored receipts are excluded from public
snapshots. Task state never becomes canonical autobiographical memory.

EventBus coalesces updates into a revision and bounded notification batch. SDK
announcements require full turn/playback idleness, current session/turn and no
pending confirmation, including rechecks after store awaits. Speech dispatch is
persisted first. Interrupted or uncertain delivery stays UI-only without replay;
UI acknowledgement does not imply speech. The generic notice contains no memory
or task result text.

The sole gateway retains one upstream, distinct contexts and scoped credentials.
P1 needs a live reservation; P2 waits for release. Weighted P2:P3:P4 dispatch is
4:1:1. Detachment removes pending work but drains an active generation. The
benchmark modules use public gateway contracts, retain failed trials and separate
visible text, provider decode, PCM sink interruption and observed slot release.
They do not implement a second scheduler or certify native GPU preemption.
