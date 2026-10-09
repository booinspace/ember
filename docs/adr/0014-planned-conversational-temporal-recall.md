# Root ADR 0014: planned conversational temporal recall and event-date enrichment

Date: 2026-10-09. Status: accepted design; Phase 8 implementation pending.
Authority: [PLAN.md](../../PLAN.md#conversational-temporal-recall-planned-phase-8-architecture).
Extends [Root ADR 0010](0010-planned-deep-associative-recall-and-phase-acceptance.md);
reuses [Memory ADR 0008](../../ember-memory/docs/adr/0008-native-consolidation-and-profile.md)
and [Agent ADR 0005](../../ember-agent/docs/adr/0005-bounded-profile-and-delivery-validation.md).

## 1. Desired behavior and current gap

Ordinary "memories from summer 2026" becomes a scoped valid/event-time query without
requiring Deep Associative Recall. "What did I tell you then?" selects observed time;
"What did you believe before that correction?" selects a supported recorded cutoff.
The application supplies retrieved, labelled context before the final Qwen answer;
Qwen does not issue SQL or acquire memory permissions.

Inspection of the main Ember checkout found existing TemporalQuery filters but an
unscoped MemoryQuery in CharacterCore.prepare. Prompt rows already include time.
General extraction assigns observed_at/source_observation_not_validity without
generally extracting event dates. Current canonical/default derived validity filters
and certificates must distinguish an episode's occurrence from state validity;
batched delivery currently requires latest active revisions and wall-clock validity.
An interpreter alone would not close these storage/selection/delivery gaps.

## 2. Ownership and coupling

ember-agent owns one typed local resolver, explicit clock/timezone injection,
conversation-local follow-up scope, optional interpreter admission and prompt/UI labels.
ember-memory owns query compilation/filtering, ranking, exact evidence and temporal
metadata revisions, current policy/source selection, index outbox and validation.

Use existing injected clients and the gateway. No engine/private SQL import enters the
character core, and no extra canonical owner, daemon, graph service or scheduler is added.
The minimum added coupling is explicit resolved selector/uncertainty in query composition
and selector-aware dependency validation across the existing ABI boundary.

## 3. Existing abstractions

Reuse MemoryQuery/TemporalQuery/ContextRequest, structured/lexical/BGE retrieval,
coverage, immutable revisions, grants/suppression/origins, processing provenance,
pinned jobs and interactive review, compare-and-swap UpdateRequest, index generations
and semantic vector reuse. Keep selected exact dependencies under 256 refs/64 KiB.
Do not introduce a query-planning framework or separate enrichment database.

## 4. Necessary extensions

Add a small agent-owned typed resolved query/clarification contract and bounded previous
scope. Resolve dates/months/seasons/years/calendar-relative expressions deterministically.
Default this deployment to Europe/London; summer is local June 1 through September 1,
exclusive. Explicit setup supplies IANA timezone data, including tzdata on Windows;
readiness checks the configured zone without downloads or host-timezone substitution.
The installed root environment currently lacks tzdata; no dependency is installed by
this design task. Source-relative extraction uses the original message clock, not that
query clock or import date.

Version new extraction contracts/hashes to carry source-supported date evidence and
precision/basis. Reuse processing/review/revision infrastructure for a separate,
resumable, explicitly reviewed temporal enrichment of selected accepted character
memories. Extend public delivery validation only as needed to retain exact historical
selectors separately from current profile/pin dependencies; negotiate capability and
strict serialization compatibility when implementing, without weakening legacy checks.

An independently opt-in Qwen fallback defaults off, makes at most one schema-constrained
bounded P0 call through the gateway, and validates output as proposed intent. It has no
SQL, identity, grant or mutation authority. It is neither deep planning nor background
extraction: P8-AC-13 and conservative P3/P4 scheduling remain unchanged.

## 5. Contracts and invariants

Filter temporal eligibility before ranking/candidate limits. Date-only requests use
structured reads; topical requests reuse existing hybrid search. Retain six ordinary
selected memories and existing prompt allocations. Historical/current facts and
observation/recording/event dates remain labelled. Coarse dates are not precise events
or continuous validity; plans/hypotheticals/narratives do not become completed events.

A past episode remains rememberable after its event interval ends; state facts obey
current validity. Validate old selected revisions against their requested selector,
while forgetting, grants, source availability/selection and suppression use current
policy. Current profile pins remain independently current. Do not reactivate history.

Enrichment preserves IDs, semantics, approval history and seed origins, appending
reviewed evidence/provenance and recorded revisions. Library changes do not rewrite
seeded characters. Resume is idempotent; conflicts require revalidation. Existing
extractor jobs, hashes, windows and candidates are not altered.

## 6. Failure behavior

Unknown dates stay unknown; missing years/timezones/anchors or competing antecedents
clarify or return qualified limited coverage. Missing IANA data or an invalid configured
zone reports an unavailable temporal capability. A no-match result is not proof no event
occurred. Failed/invalid/timed-out interpreter output cannot fall back to unfiltered
historical claims. Missing historical validation fails closed for that capability.
Revocation/suppression during delivery safely stops dependent output/history.

Use existing ownership, bounded workers, cancellation and background admission.
Enrichment does not acquire a competing writer or run background generation as P0.
No maintenance/migration/download occurs as a side effect of this documentation task.

## 7. Acceptance and performance

Append PLAN P8-AC-23 through P8-AC-30; preserve P8-AC-01 through P8-AC-22.
Require sanitized DST/leap-year/coarse/unknown/source-relative date cases, ended episodes,
superseded states, recorded history, follow-ups, reviewed enrichment/restart/conflicts,
legacy jobs, degraded indexes, interpreter failures/cancellation and mid-delivery
permission changes. Exact temporal evidence and authorization cases must all pass.

Measure resolver, scoped retrieval, historical validation, formatting and optional
interpretation separately with cold/warm/refresh/contention comparisons. Preserve
the 150 ms soft retrieval deadline, six selected memories and existing regression caps.
Measure optional serial inference through final-answer TTFT; existing Phase 7 Qwen
prefill/TTFT results do not benchmark an interpreter. Keep fallback off by default.
No new benchmark or runtime acceptance claim is made in this record.

## 8. Delivery and remaining gaps

This task updates the authoritative design/criteria and cross-links the owning decisions.
Implementation remains Phase 8; ABI/schema/gateway versions and production data are
unchanged. Future implementation must map each AC to evidence and stop for phase review.

Catalogue ordinary provenance routing, bounded timeline browsing, unsupported event
anchors and contextual entity disambiguation as remaining conversational gaps without
adding them to this amendment's delivery gates. Reminders require later task/event
delivery rather than merely storing an intention. Existing automatic native capture,
corrections and source controls stay part of Phase 8.
