# Root ADR 0011: planned Phase 7 bounded user profile

Date: 2026-10-08. Status: planned architecture; documentation only, not implemented.
[PLAN section 13](../../PLAN.md#bounded-user-profile-planned-phase-7) defines behavior;
[P7-AC-10 through P7-AC-17](../../PLAN.md#phase-7-acceptance-criteria-native-activation-and-consolidation)
define delivery. This extends planned Phase 7 and integrates with planned Phase 8;
no existing phase is declared complete and no schema/ABI version changes here.

## 1. Desired behavior

Include a small source-backed profile of the identified user's stable facts and
explicit communication preferences on unrelated conversational turns. Preferred
name, pronouns and response style need not match a NAS search to be available.
Deliver in existing terminal/LiveKit text during Phase 7; Phase 8 browser/audio and
source-selection integration reuse the same behavior. Profile inclusion is bounded,
not a promise that every fact appears in every response.

## 2. Ownership

ember-memory owns field eligibility, deterministic selection, exact revision/source
validation, policy/exclusions and pin management in existing character storage.
ember-agent owns prompt formatting, accounting and delivery/history invalidation,
using public typed clients. Composition supplies the existing single engine owner.
A profile cannot approve or seed knowledge, expand source grants or alter personality.
The allowlisted profile is initially enabled for eligible accepted facts, with an
explicit per-character disable setting and field controls; manual pinning is optional.

## 3. Existing abstractions

Reuse PinnedMemory, pin/unpin/get_pinned, ContextRequest.include_pinned,
RetrievalContext, structured facts/entities, temporal metadata and origin/evidence
suppression dependencies. Existing pin records bind exact memory revisions; current
get_pinned filters active/current/temporally eligible records. The character currently
requests include_pinned=False and omits most metadata. Phase 7 must actually wire
profile inclusion into the composer; an existing contract is not shipped behavior.

The planned Pins allocation is already 384 tokens within the 7,000-token input
ceiling. Reuse that category, with at most eight automatic profile entries alongside
manual pins. Ordinary recall retains six distinct slots and one hybrid query.

## 4. Minimum additions and coupling

A small typed/versioned profile selection policy and pin-purpose/manager/dependency
metadata distinguish automatic choices from operator pins and persist field exclusions.
Use additive storage/contract changes only where existing metadata cannot represent
those guarantees, with capability negotiation and legacy defaults. Do not overwrite
manual pin ownership or silently re-pin an excluded field after a new revision.

No second canonical biography, memory copies, profile database/daemon, retrieval API,
LLM summarizer or external framework is needed. The engine returns supporting typed
memories/pins in ordinary retrieve_context; the agent cannot read private SQL or
perform per-field lookup loops. Any cached projection remains derived and rebuildable.
This introduces a bounded pin-selection dependency in prompt composition and reuses
canonical policy invalidation; Phase 8 source controls must include it by origin.

## 5. Invariants

Select only accepted, active, permitted and appropriately attributed current facts
for the identified user. Pending/raw/assistant-only/inferred/hypothetical knowledge
cannot become a profile field. Conflicting alternatives and unknown current-state
claims are omitted rather than resolved by newest import or confidence score.

Preserve source and seeded origins, revision, validity and policy/selection dependency
stamps. Revalidate before prompt inclusion and during delivery; invalidate dependent
cached/history context after correction, forgetting, suppression, expired/revoked
grants, unavailable sources, changed settings or time boundaries. TTL alone is unsafe.
Manual priority/exclusions survive restart, redirects, re-extraction and bundle restore.

Profile/manual pins share the existing category; duplicates across ordinary/deep
context appear once. All values remain attributed untrusted memory data. Preferences
may inform style within trusted rules, never grant authority or replace personality.

## 6. Failure behavior

Omit unavailable, stale, unsupported, conflicting or oversized fields with specific
private-safe reasons; healthy ordinary chat can continue. Canonical integrity errors
retain their existing behavior. Never substitute a last-known denied value or repair
missing sources by searching private raw files during prompt preparation.

Missing FTS/vectors/encoder does not disable canonical profile pins. Work uses the
existing bounded workers. No extra embedding, KNN, source hydration, Qwen call,
competing maintenance owner or realtime-loop blocking is introduced.

## 7. Acceptance and performance

PLAN's stable AC require unrelated-query profile inclusion, accepted/imported/native
origin controls, other-speaker/character isolation, conflicts and correction, manual
pin/exclusion persistence, restart/restore, mid-stream invalidation, injection and
index-independent fallback. Fake clients prove no backend coupling; in-process and
real-Qwen text tests establish integration without requiring a name in every answer.

Measure paired enabled/disabled cold/warm/refresh runs: calls, canonical selection,
validation/formatting, whole recall, event-loop responsiveness, prompt tokens and
prefill/TTFT separately. Initial warm profile p95 target is <=10 ms, retaining the
150 ms fast soft deadline and existing targets. Added context has prefill cost.
Phase 5 historical measurements and open capacity/platform gates remain unchanged.

## 8. Delivery and architecture handoff

This task changes PLAN/ADRs only. Phase 7 implementation must record AC evidence and
own backend/runtime detail in the respective component ADR trees, using the next
available numbers; root ADR 0010's prospective numbering is not reserved. Phase 8
reuses profile controls/dependencies for browser/audio, source selection and deep
answers. Stop for architecture review after each implemented phase.

## Pre-code Phase 7 review

[Root ADR 0012](0012-planned-phase-seven-implementation.md) and the
[implementation proposal](../phase-7-implementation-proposal.md) integrate this profile
with native acceptance, consolidation/time and bounded public reads. The proposal
identifies source-attribution, pin ownership, immutable processing-profile and
bundle/validation changes before code. Status remains planned pending review.
