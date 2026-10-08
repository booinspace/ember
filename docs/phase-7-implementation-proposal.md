# Phase 7 implementation proposal

Date: 2026-10-08. Status: proposed for architecture review before code changes.
PLAN.md remains authoritative. This proposal covers P7-AC-01 through P7-AC-17 and
Root ADRs 0010/0011; no acceptance gate is claimed as passed.
The implementation decision is recorded in [Root ADR 0012](adr/0012-planned-phase-seven-implementation.md).

## 1. Desired behavior and phase boundary

Phase 7 delivers three related capabilities:

1. Accept qualifying native conversation candidates through an engine-owned policy;
   consolidate repeated support and preserve corrections, conflicts and history.
2. Expose bounded typed relationships and temporal reads needed by Phase 8.
3. Include a small source-backed user profile in existing terminal/LiveKit text,
   independently of the current query, with safe invalidation and manual controls.

For a sanitized scenario, repeatedly saying "Milo is my shark plushie" must not
create another equivalent active belief on each processing run. Every distinct
supporting source remains inspectable. An explicit preferred-name correction
changes current profile context while preserving earlier belief/history. An unrelated
NAS question receives eligible name/pronoun/style pins alongside ordinary recall.

Phase 7 supplies native acceptance and maintenance entry points for already archived
sources. Phase 8 owns continuous capture/extraction queues, browser/audio controls,
source-selection UI and DeepRecallService. The Phase 6 gateway still prohibits P3/P4
while a realtime session is connected; apparent idle time is not safe admission.
No new scheduler, preemption mode, model planner or memory daemon is proposed.

## 2. Ownership and coupling

| Owner | Phase 7 responsibility | Boundary |
|---|---|---|
| memory-abi | Optional typed related lookup, temporal additions, pin/profile metadata and capability negotiation | No SQLite, model, LiveKit or agent imports |
| memory-engine | Acceptance, consolidation, support/origin metadata, conflict/redirect rules, profile selection, current access and time validation | Existing character store and bounded workers |
| memory-importers | Preserve existing source normalization/identity; compatibility fixtures if required | Imports remain proposals subject to operator review |
| memory CLI | Trusted maintenance activation, profile inspection/settings and correction/review controls | Exclusive existing root owner; no competing writer during chat |
| agent composition/backend | Bind native dialogue attribution and inject capability-aware clients | Only this layer constructs concrete engine services |
| character core | Profile/pin formatting, budgets, dependency validation and retained-history handling | Public injected contracts only; no inference during recall |
| gateway | Existing pinned P0/P3/P4 admission and transport | No direct Ollama fallback or new GPU concurrency |

The necessary coupling is between profile/prompt dependencies and canonical memory
policy, plus bundle/index projections and the new canonical metadata. Model output
never supplies the character binding, policy authority, pin controls or source grants.

## 3. Findings from the current implementation

| Finding | Consequence for implementation |
|---|---|
| Generic mutations accept only confirmed_user authority | Introduce a narrow engine policy operation; do not impersonate human confirmation |
| Policy currently checks duplicate/conflict processing candidates | Compare against accepted state and durable manual decisions inside the acceptance transaction |
| Native delivery archives Messages; source_kind=native currently selects Documents | Version source selection and identify actual first-party delivery messages before acceptance |
| Dialogue actors currently carry role only; branch selection is unavailable | Bind native speaker/origin through trusted composition; never approve a message merely because role is user |
| ProcessingService/GatewayExtractionModel check the current extractor/prompt/schema | Preserve immutable legacy processing profiles so new Phase 7 work does not invalidate existing 0.1.2 jobs |
| Supersession, evidence, claim suppression, source grants and immutable revisions already exist | Extend their rules and projections rather than replace the mutation/storage system |
| Pin storage has one row per memory and no automatic/manual ownership distinction | Add minimal binding/management metadata; automatic profile selection cannot overwrite manual pins |
| CharacterCore disables pins and rechecks memory IDs serially during preparation/streaming | Wire profile context and use bounded batched validation when supported |
| Selective bundles explicitly enumerate canonical tables | Extend selected closure, policy/exclusions and support/origin metadata in the same change |
| Derived refresh copies the previous search DB | Reuse/coalesce existing refresh; do not cause a rebuild or copy for every repeated confirmation |

Existing Phase 5 performance evidence is a warm duplicate-memory fixture, not
a diverse Phase 7 benchmark. Preserve its measurements and outstanding gates.

## 4. Reuse versus genuinely new primitives

Keep ProcessingService, activation evaluation, immutable sources/evidence, existing
writer transactions, mutation receipts/audit, supersession, suppression dependencies,
source grants, index outbox, pin/retrieval models, async workers and the existing
dialogue/gateway adapters. Extend CLI groups and the current composer.

Limit new implementation units to cohesive engine modules for consolidation/support,
profile policy/selection and bounded related reads, plus a small fixed processing-profile
definition/dispatch module if required. A module is not a new service or framework.

The genuinely missing primitives are:

- A trusted native acceptance request/decision/receipt. It binds candidate revision,
  character, acceptance-policy specification and idempotency key. It can accept,
  attach equivalent support, preserve a conflict, defer or reject; it cannot grant
  access, seed an import or enable a broad model-owned mutation mode.
- An indexed claim projection for accepted memory identity, subject/predicate/value/
  polarity, lifecycle and observed/valid/recorded selectors. This is rebuildable
  metadata in existing storage, maintained with canonical changes.
- Append-only support/relationship metadata and conflict membership where existing
  supersession/decision tables are insufficient. Support records retain exact
  evidence, processing specification, source and seed-origin references.
- Typed profile policy, durable field exclusions and automatic pin bindings. Manual
  pins retain their existing storage/semantics; management metadata prevents one
  purpose from deleting another. Supporting Memory objects remain canonical.
- The PLAN's optional RelatedLookupClient lookup_related intents resolve/expand/validate,
  with bounded typed responses and a read manifest. Profile data remains part of
  retrieve_context; it does not acquire a parallel retrieval API.

Proposed starting ABI limits: 32 relationship anchors, at most 100 returned records
and 256 dependency references for validation, plus explicit row/call/deadline limits.
Source/neighbor reads enforce their separate grants before limits. An oversized
manifest is rejected/trimmed through whole-turn context policy, not split into an
unbounded loop. These engine ceilings can be narrowed by Phase 8 request budgets.

Do not add a general graph query language, universal workflow registry, another
canonical database or abstraction wrappers around unchanged functions.

### Ordinary-chat complexity budget and measured value

Start with the simplest indexed profile selection and bounded validation that meets
acceptance. Persistent user policy/exclusions and pin-purpose semantics are necessary;
a separate selection cache, rebuild queue or dependency subscription system is an
optimization that must earn its complexity through paired measurements.

- One existing retrieve_context call includes profile pins plus ordinary recall.
  Keep one hybrid query; no related resolve/expand calls, graph walks, source hydration
  or full relationship read manifest construction occurs on an ordinary turn.
- Fast validation carries a small set of selected memory/source references and the
  necessary policy/selection/time stamps. Deduplicate; cap the combined set at 256
  references and 64 KiB metadata. Trim whole dependent history turns with reasons if
  needed, preserving current input and never dropping required access/expiry checks.
- Where supported, batch the dependency union at prompt preparation, before
  generation and before visible delivery/final completion. Metadata-only progress
  triggers no relationship checks. Return validity/status without hydration; replace
  serial checks rather than adding a parallel validator or per-field lookup layer.
- Record benefit on a fixed sanitized suite of unrelated questions and profile facts,
  with corrections, forbidden fields and prompt inspection. Compare identical enabled/
  disabled inputs; report response behavior without forcing a name into every reply.
- Compare equivalent serial/batched validation sets and whole streamed turns, recording
  calls, rows, bytes, latency and successful mid-stream invalidation. Ordinary traces
  must show zero relationship expansion. Scope correctness is not an optimization.

If a gate fails, simplify or optimize the responsible work and retain the unfulfilled
AC in the delivery record. Disabling an optional optimization is allowed; disabling a
required capability does not justify calling Phase 7 complete.

## 5. Contracts and invariants

### Acceptance and attribution

Reuse validated proposals and original source windows. Re-read candidate revision,
source availability/authority, manual decisions, suppression, compatible semantics
and accepted-state conflicts before the atomic writer commit. A model confidence
number is one input, not a grant or probability.

New first-party delivery records carry trusted native provenance/speaker binding
using existing source identity/attribution mechanisms. The versioned native selector
recognizes user delivery messages without treating an unavailable imported branch
as confirmed or promoting assistant speech. Legacy ambiguous native records stay
readable and require explicit confirmation if their identity cannot be established.
Imported/summary candidates retain the Phase 6 review/seed path.

Keep eligibility evaluation separate from the trusted acceptance operation. Reuse
the low-level canonical write helpers after their existing validation; record policy
authority distinctly from confirmed_user in decisions, receipts and audit. Mutation,
decision, support/relationship rows, dependency changes and outbox effects commit
together. Revision races re-evaluate or return conflict; no half-accepted memory.

### Duplicates, support, corrections and origins

Use registered normalization and indexed canonical claim keys for exact fact equivalence.
Overlapping windows or repeat extraction of the same span are the same support.
Separate sources may corroborate a fact, but generated summaries do not manufacture
independent support. Alias/entity matches must be unambiguous and store-bound.

New support attaches to the same logical accepted belief through append-only metadata.
Do not append an ever-growing copy of all evidence to a JSON revision on every mention:
that would make storage grow quadratically. Preserve accepted revision evidence anchors
and expose additional supports through bounded related/source inspection. Confidence
or importance is not silently increased by mention counts.

Episodes require source/event identity and compatible time/branch semantics; the same
wording on two dates is not automatically one event. Semantic similarity can nominate
bounded review candidates but cannot merge facts, resolve conflicts or invent an incident.

Preserve each support's original native/library origin and independent seed receipt.
Do not destructively coalesce assertions across incompatible source scopes. Keep their
origin-specific support/membership so later source selection can distinguish them;
deduplicate eligible retrieval only after scope checks. Added support never bypasses
claim/source suppression or turns a revoked origin into a valid one. Selected reads
validate the evidence actually supporting that assertion and all hard derivation/origin
dependencies; they do not walk every historical corroboration on every chat turn.

Reuse memory_supersessions and versioned transition rules. Explicit "changed now"
evidence can establish a transition only for a compatible registered predicate;
a purchase does not imply replacement, and intent does not imply residence. A
correction of a false belief is distinct from a world-state change. Preserve the
old recorded belief without inventing its valid interval. Ambiguous relationships
remain conflicts/pending decisions. Redirects are acyclic, bounded and scope-checked.

### Time and optional public reads

Preserve existing valid_at and recorded_before meanings. Add the missing observed
range/valid interval filters with explicit half-open bounds and unknown-date behavior.
Project observed time into indexed headers; do not parse every JSON memory at query time.
Historical selection happens before candidate limits and returns historical revisions,
while current suppression/grants always apply.

Related lookup returns supported canonical redirects, supersessions/conflicts, source
references and existing alias/conversation metadata, with reasons and coverage. Shared
user/topic/date alone never becomes an incident relationship. Evidence_read permits
cited evidence; archive_search is required for unquoted source neighbors.

Batch validate rechecks exact memory/source revisions, lifecycle, time and policy/selection
dependencies without similarity search. Reuse it for bounded prompt/history/stream
validation when supported; legacy fake/backends retain bounded equivalent checks.
Read manifests disclose logical generations/watermarks/epochs, not paths or authority.
No cross-store atomic-snapshot guarantee or arbitrary traversal is introduced.

### Profile and prompt

Enable the allowlisted profile by default for eligible accepted preferred-name,
explicit-pronoun, timezone and communication-preference facts for the bound user.
Register explicit predicate meanings; do not reinterpret vague episodes, unknown
predicates or plans into profile facts. Omit conflicting or uncertain current fields.

Profile policy/field exclusions persist with character identity, independently of
model tag. Select/reselect through indexed metadata and relevant mutation/expiry
invalidation; no full-corpus scan, extra embedding, source hydration or Qwen call
occurs on an ordinary turn. Derived bindings can rebuild without forgetting user choices.

Return revision-bound profile/manual pins with their supporting memories through
retrieve_context. The agent renders a separate compact untrusted-data contribution.
At most eight automatic entries share the existing 384-token Pins allocation with
manual pins; preserve six distinct ordinary recall slots and the 7,000 input ceiling.
Deduplicate across profile/ordinary/deep use and report every omission.

Use a verified Qwen tokenizer/template if available, otherwise the existing conservative
guard with counting-mode diagnostics. The BGE tokenizer is not a Qwen prompt tokenizer.
Do not move personal observations into trusted personality/security instructions.

Bind cache/history manifests to data/selection/policy revisions and known expiry/time
boundaries. Revalidate during delivery and drop or stop unsafe dependent output.
Bound retained-history dependencies as well as record counts; the current transitive
dependency accumulation must not turn streaming validation into unlimited ID reads.

### Migration and compatibility

Proposed versions for implementation: engine/schema 0.5.0 (user_version 5), ABI 0.4.0;
gateway wire 0.2.0 and normalized archive semantics remain unchanged unless an actual
wire change proves necessary. These are proposals, not edits made by this document.

Use one backed-up additive migration for projections and canonical support/relationship/
conflict/profile metadata. Preserve IDs, source bytes, immutable revisions, grants,
manual review history and suppressed keys. Interrupted migration leaves the prior
database recoverable. Historical JSON is not rewritten for a new predicate policy.

Keep the 0.1.2 extraction prompt/output schema/policy definitions byte/hash stable.
Dispatch legacy resumptions against their pinned profile; new predicates/transition
hints use a new immutable extraction/acceptance profile and new jobs. Old 0.1.0/0.1.1
unsupported jobs retain their explicit status; do not claim retroactive support.
No automatic rerun, reset or reapproval of the user's ongoing archive job.

Version schemas and compatibility matrices; capability-gate optional request/response
additions and test legacy strict shapes. Full and selective bundles preserve support,
origin/relationship closure, referenced policy specifications, pin ownership and
field exclusions. Partial bundles disclose unavailable relationships/evidence rather
than invent or leak omitted history.

## 6. Failure behavior

| Condition | Required outcome |
|---|---|
| Invalid evidence, unsupported semantics or unclear native identity | Persist specific deferred/rejected decision; no accepted belief |
| Unresolved duplicate/conflict/time ambiguity | Bounded inspectable result; no last-write-wins fact |
| Manual reject/forget or denied origin | Respect durable restriction, including new IDs/redirects/re-extraction |
| Interrupted acceptance or revision race | Atomic rollback or identical-key replay; re-evaluate changed inputs |
| Missing index/encoder | Canonical profile/structured reads remain available; ordinary recall reports degradation |
| Missing/stale/conflicting profile field | Omit with reason; healthy chat continues without that field |
| Mid-stream correction/revoke/expiry | Invalidate dependent delivery/history; do not serve cached private values |
| Bounded lookup saturation/deadline/cancellation | Typed partial/unavailable outcome; running native work remains owned until drain |
| Unsupported optional ABI capability | Explicit bounded fallback, without hidden backend calls |
| Source/bundle closure incomplete | Honest availability/coverage; no raw-file reconstruction or wider grant |
| Connected realtime session | P3/P4 pending/blocked until safe suspension/disconnection |

## 7. Acceptance and performance evidence

| PLAN criteria | Required evidence |
|---|---|
| P7-AC-01, P7-AC-02 | Synthetic native extraction/accept/defer/reject; exact authority/evidence/policy audit; import isolation |
| P7-AC-03 | Thousands of overlapping/repeated proposals yield stable logical beliefs and linear support storage; distinct episodes remain distinct |
| P7-AC-04, P7-AC-08 | Valid/observed/recorded current/history scenarios, explicit corrections/transitions and unknown times |
| P7-AC-05, P7-AC-06 | Manual rejection/forgetting/suppression across re-extraction/redirects, multiple origins, independent seeds and interruption |
| P7-AC-07, P7-AC-09 | Optional typed resolve/expand/validate with aliases, branch/grant/time isolation, bounded rows/cycles and legacy capability behavior |
| P7-AC-10, P7-AC-11 | Unrelated NAS prompt receives eligible profile; pending/import/other-person/hypothetical/conflicting facts cannot fill it |
| P7-AC-12, P7-AC-13 | One ordinary retrieval, no extra model/embedding/KNN/hydration, 384-token shared pin budget, six ordinary slots and deduplication |
| P7-AC-14, P7-AC-15 | Mutation/expiry/unavailability during prepare/stream; manual pin/exclusion/profile-disable across restart, model change and bundle restore |
| P7-AC-16 | Shared terminal/LiveKit text composer, authorized CLI inspector, content-free logs, index-independent fallback and injection resistance |
| P7-AC-17 | Fake/in-process and real pinned Qwen/LiveKit integration; measured profile benefit, zero ordinary relationship expansion, bounded metadata and paired validation/performance evidence; full locked quality gate |

Benchmark profile disabled/enabled on identical data/configuration, with cold/warm
caches, mutation/revocation and simultaneous index refresh. Record all samples,
per-stage latency, SQL/ABI counts, queue/admission, prompt cost and model prefill/TTFT.

Targets remain:

- Profile selection/validation/formatting warm p95 <=10 ms.
- Complete ordinary recall retains the 150 ms soft behavior and 50-100 ms warm aim.
- Existing ID/provenance <=10 ms, structured <=15 ms and small mutation <=20 ms
  excluding inference remain acceptance targets, not claimed observations.
- Related reads obey declared cardinality/time ceilings; no unbounded neighbor or
  dependency crawl is accepted as a fallback.

Use diverse canonical fixtures up to the PLAN's 100,000-memory scale; vary historical
revisions, duplicate supports, conflicts, profile candidates and denied scopes. Exercise
the 500,000-message/250,000-chunk scale where a Phase 7 claim depends on archive indexing.
Do not infer that full capacity passed from a duplicate fixture or a small real archive.

Expected scaling is indexed lookup plus bounded eligible candidates, rather than all-pairs
comparison. Let N be canonical beliefs, S distinct supports and E recorded relationships:
canonical additions grow with N + S + E, not with every pair of statements or repeated
copies of all prior evidence. Profile rendering is bounded by eight entries and its
token allocation; batch validation is bounded by its manifest cap. Query plans, rows,
bytes and measurements must demonstrate this expectation.

Existing index-copy refresh remains O(index bytes). Coalesce relevant changes, avoid
refresh on support-only metadata changes that do not affect searchable content, and
measure bursts. Do not add segment storage or a graph/vector service to hide this cost.

Run installed locked Ruff format/lint, strict Pyright, schema/compatibility checks,
lockfile checks and pytest, plus marked real-model/LiveKit tests. Map each AC to
passed/failed/blocked/not-run evidence. Private imports are not acceptance fixtures,
and test skips or missed performance targets prevent an unqualified completion claim.

## 8. Implementation sequence and review handoff

1. Add sanitized contract/attribution/migration fixtures and define the optional ABI,
   policy/specification identities and backwards compatibility.
2. Fix trusted native source selection/binding; implement indexed accepted-state
   comparison and atomic policy acceptance with durable receipts/decisions.
3. Add bounded support, conflict, redirect/transition and temporal projections;
   complete suppression/origin and bundle closure before enabling their read paths.
4. Implement default bounded profile policy/bindings and durable CLI controls.
5. Integrate compact pins and bounded dependency validation into the shared character
   core; preserve the gateway's conservative scheduling.
6. Run adversarial/restart/restore/real-service and scale/performance gates, update PLAN,
   root/component implementation ADRs, compatibility, schemas and the Phase 7 runbook,
   then stop before Phase 8 for architecture review.

At each step reuse an existing owner/helper where it enforces the same invariant.
Create a new primitive only for the missing authority, support, pin-management or
batched-read semantics above. Record evidence rather than expanding the scope to
browser/audio, continuous ingestion or inference preemption.

This document is the requested pre-code review checkpoint. No production/test code,
package versions, database, extraction job or private memory is changed by drafting it.
