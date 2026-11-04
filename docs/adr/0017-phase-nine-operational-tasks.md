# 0017 — Phase 9 operational tasks and gateway admission

Status: implemented native foundations, accepted by the automated and installed
checks in [Phase 9 validation](../phase-9-validation.md). Phase 8A remains parked.
MCP providers, external harness workers and live model preemption remain Phases
10, 11 and 13 respectively; this phase enables none of them.

Ember Agent owns task lifecycle in runtime.db, independently of canonical memory.
TaskRouter provides durable submit/status/cancel/results and coalesced events;
installed native providers execute bounded steps under that owner. This is the
native implementation of the delegated-task boundary, without introducing a
second remote job service. Character, session and turn identities come from
trusted runtime code, never model arguments. Model-selected routes are restricted
to advertised count, summary and Deep Recall operations over literal text from
the initiating user input. Thinking mode does not advertise these tools.

The operational store persists original requests, attempts, exact-action
confirmations, effect receipts, result dependency certificates, artifact hashes,
terminal outbox and separate speech-delivery state. Completion and terminal
notification commit together. Queued requests survive restart; active attempts
become uncertain and are never automatically repeated. That conservative recovery
is deliberate for these single-step providers: no external provider checkpoint
or generic multi-step replay is claimed. Later providers must supply their own
verified recovery contract through the same task owner.

A draft-review operation proves the confirmation path: the authenticated Tasks
panel displays the exact draft and submits its action hash. A durable dispatch
fence precedes the managed artifact write. Changed/expired/rejected/cancelled
approvals cannot dispatch; ambiguous effects become uncertain. Completed or
uncertain effects cannot be replayed. This grants no host-shell or network-write
authority. Artifacts remain owner-bound, escaped private text with digest checks.

Routine result announcements use the existing SDK session and CPU TTS. Speech
is journalled before dispatch, is interruptible, and requires no active user
speech, preparation, model turn, pending delivery, playback or confirmation.
Only the originating session and current turn are eligible; older-turn results
remain available in Tasks. Readiness is rechecked after asynchronous store work.
Interrupted or crash-uncertain announcements are never replayed automatically;
UI acknowledgement is independent. Explicit task-update questions return a fixed
count through the normal text/voice receipt path, without disclosing result text.

The existing gateway admits P1 only under live reservation, and P2–P4 only when
unreserved in conservative mode. It retains one active upstream and weighted
4:1:1 P2:P3:P4 FIFO dispatch. Background results remain buffered until completed
validation. HTTP/coroutine cancellation does not assert GPU release; the runner
currently drains. The new backend-neutral benchmark suite measures that behavior
and preserves the seam for intended future vLLM adoption after Phase 13.

| Criterion | Acceptance evidence |
|---|---|
| P9-AC-01 | Typed native provider/router boundaries, advertised schemas, literal-input routing, scoped context/receipts, capability denial and bounded execution tests |
| P9-AC-02 | Idempotent submit and changed-input conflict, bounded queues, persisted progress/cancellation, atomic terminal CAS, attempts and uncertain restart reconciliation |
| P9-AC-03 | Operational attempts, requests, confirmations, receipts, results/outbox, artifact authorization and speech journal remain outside the Memory ABI |
| P9-AC-04 | Coalesced events, durable UI delivery, idle/current-turn announcement fences, pending confirmation and interrupted/crash speech tests |
| P9-AC-05 | Scoped P1/P2 auth, reservation, queue timing, bounded waits, buffered P2 and weighted dispatch tests; real 3090 gateway benchmark with P2 queued |
| P9-AC-06 | Timeout/cancel/completion races, replay fencing, mismatched receipts, citation scope/current-access validation, revoked artifact denial and provider failure isolation |
| P9-AC-07 | Task-bound Deep Recall reuses the existing scoped service and result cache; optional later P1/P2 planning retains gateway ownership |

[Agent ADR 0007](../../ember-agent/docs/adr/0007-task-runtime-and-agent-admission.md)
owns component details. [Runbook](../phase-9-runbook.md) documents operator use.
No Memory ABI/schema change, automatic memory promotion, vLLM switch or concurrent
GPU execution is introduced. Hardware limits and unmeasured physical audio remain
explicit in the validation record rather than inferred from synthetic tests.

The repeated-interruption benchmark also found and corrected local Phonon native
stream cleanup. The same-thread close/admission fence and real CPU recovery
checks are recorded in validation. This speech-resource correction is independent
of Phase 13 GPU cancellation. Task tool and approval contexts also retain the
remaining task deadline rather than resetting their budget on dispatch.
