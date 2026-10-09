# Agent ADR 0005: shared profile pins and bounded delivery validation

Date: 2026-10-09. Status: implemented experimentally; review checkpoint.
See [Root ADR 0013](../../../docs/adr/0013-phase-seven-native-consolidation-and-profile.md)
and [Memory ADR 0008](../../../ember-memory/docs/adr/0008-native-consolidation-and-profile.md).

## Decision and ownership

Reuse the existing CharacterCore for terminal and HostedRoom/LiveKit text. Composition
constructs the memory engine/client and supplies a stable user binding to native dialogue
archival; the business core imports only public ABI contracts. Native archive v0.2 carries
trusted user/assistant attribution, stable conversation identity and original visible text.
Hidden reasoning and source/provider identity do not become user authority.

Capabilities negotiate profile pins and optional batch validation. One retrieve_context
contains pins and one ordinary lookup; zero ordinary resolve/expand calls, full manifests,
extra encoders, archive body reads or per-profile-field get calls. Legacy backends retain
bounded serial checks. Automatic capture/extraction scheduling remains Phase 8; connected
realtime sessions still conservatively block P3/P4.

## Budgets and delivery

Manual pins precede automatic pins in the same 384 conservative serialized-token budget,
including the actual data wrapper/role/JSON overhead. Profile predicates render as compact
untrusted observations; personality remains the trusted system message. Six distinct
ordinary memories, current user input and the 7000-token ceiling remain. Whole history pairs
are trimmed before current recall where possible; budget/eligibility omissions are inspectable.

Selected memory/source references, policy/selection epochs and time boundaries are unioned
with retained history, with at most 256 distinct refs and 64 KiB metadata. Oversized or stale
turns are dropped as whole units. Validate once at preparation, again before generation,
and before visible text/tools/end. Progress metadata does not trigger checks. If authorization
or dependency state changes mid-stream, visible delivery stops and no completed stale turn
is saved. Canonical failures fail closed; there is no unbounded per-ID retry fallback.

## Measurement compatibility

The gateway remains the sole model-generation owner. Opt-in request version 0.3.0 exposes
upstream prefill_seconds alongside TTFT. Health remains 0.2.0; request versions 0.1/0.2
are accepted and omit the added usage field entirely, preserving old ingest clients.
No scheduler/preemption policy changed. Fake upstream tests verify the wire distinction.

Actual SDK room transport tests exercise profile inclusion after restart with fake generation.
Real Qwen tests separately exercise native extraction, policy acceptance and the same core,
with six paired unrelated text requests and measured prefill/prompt/TTFT. They reserve no
realtime session, so background imports are not blocked for a session lifetime. A known
unadmitted HTTP rejection may retry; uncertain transport outcomes never silently retry.
Detailed results and caveats are in the [validation record](../../../docs/phase-7-validation.md).
No voice/browser or DeepRecallService implementation is implied by these text checks.
