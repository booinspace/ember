# Agent ADR 0004: conservative P3/P4 admission

Date: 2026-10-08. Status: implemented and validated; operator architecture review pending.
See [Root ADR 0009](../../../docs/adr/0009-phase-six-extraction-and-background-inference.md).

Extend the existing gateway owner with bounded FIFO P3/P4 queues and explicit trusted
realtime session state. Background credentials cannot submit P0 or modify sessions;
foreground credentials cannot masquerade as background. Prompt text has no authority.

A connected realtime session blocks background admission even in apparent idle gaps.
Foreground startup reserves ownership first and waits for an already running background
step to complete safely. No cancellation/preemption claim is introduced. Outputs from
background work are buffered until complete and validated; partial JSON never reaches
memory jobs. Queued disconnect/expiry stops pending work, while an active subscriber
closing retains runner ownership until its native generation actually drains.

Session state is durable and fails closed across gateway restart; explicit verified
quiescence clears stale ownership. Runtime cleanup releases only its own reservation.
P1/P2 fairness/accounting and measured live preemption remain later phases. Record the
new transport/health compatibility here: requests accept 0.1.0/0.2.0, health emits
0.2.0 and adds queue/session/scheduling diagnostics. Strict old health consumers must
update. New processing requests carry an output JSON schema; the existing Ollama
adapter forwards it as native format and the same gateway validates completed output.

P3/P4 each have eight pending FIFO slots; class dispatch alternates when both are queued.
This is conservative bounded admission, not Phase 9's full priority/accounting scheduler.
Each queued request has a deadline timer and bounded progress heartbeat, retained by the
gateway. Its subscriber detaching removes pending work. Active subscriber detachment does
not cancel native generation or release the runner; the owned task drains normally.

The foreground lifecycle route reserves a session before asynchronous metadata/journal
checks can race background dispatch. Work still in metadata admission is parked; work
already generating is awaited to its actual completion. Terminal chat and the LiveKit
host retain this reservation through their whole connected lifetime. The host blocks
background even when the participant is idle/disconnected until runtime shutdown.
Ownership is released during tracked cleanup. One failed session journal blocks new
admission; health exposes failure. Session identities are persisted off-loop on the
existing bounded state worker. They do not expire optimistically after a gateway crash.

Canonical engine maintenance/root locking and inference admission are independent.
Extraction is an operator maintenance command using background credentials against
`ember gateway`; it does not run alongside a root-owning live character. CLI job queues
record progress and do not allocate GPU resources. Startup can attach to the same gateway,
wait for safe drain and then construct the existing character owner. No Python dependency
from memory engine to agent was introduced; only the optional HTTP adapter consumes the
versioned protocol. Character business logic remains unchanged.

Ruff/Pyright and the full suite results are recorded in Root ADR 0009. Scheduling tests
exercise connected idle blocks, explicit release, running-step drain, reservation during
metadata admission, bounded queues/expiry/detach, FIFO/class service, schema-mismatch
buffering, credential separation, durable crash state and journal failure. Real synthetic
Qwen extraction uses P3 and the same model digest/context as P0; the reply goes through
LiveKit's actual agent/LLM hook after review/seed/restart/semantic retrieval. Existing
actual server/room tests also remain in the full gate. Voice/TTS/browser and full runtime
integration remain Phase 8; no preemption or sustained GPU contention claim is made.
