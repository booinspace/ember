# Root ADR 0012: proposed Phase 7 implementation

Date: 2026-10-08. Status: proposed; awaiting the user's pre-code architecture review.
[PLAN Phase 7](../../PLAN.md#phase-7-acceptance-criteria-native-activation-and-consolidation)
is authoritative. The [implementation proposal](../phase-7-implementation-proposal.md)
records inspected code, ownership, contracts, scalability and the AC evidence matrix.
This extends Root ADRs [0010](0010-planned-deep-associative-recall-and-phase-acceptance.md)
and [0011](0011-planned-phase-seven-user-profile.md), without claiming implementation.

## 1. Desired behavior

Deliver engine-policy native acceptance and consolidation/time, bounded typed
relationship prerequisites, and the source-backed profile in terminal/LiveKit text.
Continuous capture, browser/audio/deep orchestration and preemption remain in their
planned later phases. The existing conservative gateway admission remains in force.

## 2. Ownership

Memory owns source/claim authority, canonical transactions, support/origins, temporal
and relationship validation, profile selection and worker admission. Agent composition
binds native attribution; the character core formats public data and revalidates delivery.
The CLI operates through the existing exclusive owner. No additional canonical store,
memory daemon, graph service, model planner or GPU scheduler is proposed.

## 3. Existing abstractions

Reuse ProcessingService/activation, sources/evidence, immutable revisions, supersession,
suppression/grants, receipts/audit/outbox, bundles, pins/context and bounded async workers.
Current native source selection misses delivery Messages; role-only attribution is
insufficient for policy acceptance. Current generic mutations require human authority,
candidate conflict checks omit accepted state, and the character excludes pins.

## 4. Necessary primitives and coupling

Add a narrow trusted native acceptance operation, indexed accepted-claim projection,
append-only support and missing conflict/relationship metadata, durable profile policy/
pin management, and the PLAN's optional bounded related lookup/validation manifest.
Use existing mutation helpers without labelling model proposals as confirmed_user.

These additions couple canonical transactions to derived claim/profile projections,
support/origin closure to bundles, and the prompt to public dependency validation.
Keep engine details outside character code and preserve scope before lookup limits.
Additional processing profiles preserve legacy pinned jobs when new predicates or
transition hints are introduced.

## 5. Contracts and invariants

Exact evidence and speaker/origin authority precede acceptance. Imports retain human
review/independent seed semantics. Duplicate support does not create duplicate logical
beliefs or quadratic JSON revisions. Time axes, distinct episodes, conflicts, manual
decisions and every origin remain traceable. Similarity is never transition evidence.

Profile/manual pins share the existing 384-token category and ordinary recall retains
six slots. Cache/history invalidation covers revisions, policy, grants, origin and
time boundaries. Optional reads are character-bound, capability-negotiated and bounded.
Detailed contracts and budgets live in PLAN and the proposal, not duplicate services.

## 6. Failure behavior

Unsupported, ambiguous, conflicting or denied proposals stay inspectable without an
accepted write. Receipt/decision/canonical changes are atomic. Stale profile fields
are omitted; unsafe delivery stops. Native work retains ownership through cancellation.
Incomplete bundles/source access expose partial coverage and cannot expand authority.

## 7. Acceptance

The proposal maps all 17 Phase 7 AC to sanitized tests and measurements. Profile
warm p95 <=10 ms and ordinary 150 ms soft behavior remain gates; full recall, support
growth, mutation, index-refresh contention and prompt prefill are measured separately.
Existing performance misses and unrun scale/platform gates remain explicit.

The user's review specifically requires measured value and strict limits for profile,
relationship-manifest and dependency machinery. Ordinary turns have one context call,
no resolve/expand traversal or full relationship manifest, and at most 256 selected
memory/source references in 64 KiB validation metadata. One bounded validation batch
replaces serial calls at preparation/generation/visible delivery where supported,
preserving authority and expiry checks. Metadata-only progress needs no relationship
checks. Start
with indexed selection; extra caches/queues/subscriptions require measured evidence.
Profile benefit and complete streaming-validation cost are paired acceptance evidence,
not assumptions from the presence of an abstraction. Failed gates stay open.

No runtime tests, migrations, private extraction/approval or benchmark were run by
this proposal. Document/link checks establish only that the draft is reviewable.

## 8. Implementation and handoff

The user requested an implementation draft before code. Stop at that checkpoint.
After review, implement in bounded increments and record root/component ADR evidence,
compatibility/migration/schema changes and a runbook. No phase is complete until its
AC evidence supports that claim; stop before Phase 8.
