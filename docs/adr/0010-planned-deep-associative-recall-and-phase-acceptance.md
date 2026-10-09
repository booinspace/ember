# Root ADR 0010: planned Deep Associative Recall and explicit phase acceptance

Date: 2026-10-08. Status: historical planned architecture and stable AC record.
[PLAN section 13](../../PLAN.md#deep-associative-recall-planned-phase-8-architecture)
is authoritative for the algorithm/contracts/budgets, and
[section 34](../../PLAN.md#acceptance-evidence-and-phase-completion) owns stable phase AC IDs.
Phase 8 remains planned. Phase 7 behavior from this record's acceptance scope is now
implemented experimentally in [Root ADR 0013](0013-phase-seven-native-consolidation-and-profile.md);
this record preserves the stable AC IDs and Phase 8 design without rewriting historical delivery.

## 1. Desired behavior

Explicitly investigate related accepted memories and authorized original observations,
resolve supported temporal scope, retain ambiguity/contradictions and supply a compact
evidence-grounded context for a normal P0 answer. Ordinary retrieve_context remains
one bounded fast operation, approximately six memories and a 150 ms soft deadline.
Searching dozens of records does not mean inserting dozens into the model prompt.

## 2. Ownership and dependencies

ember-agent owns DeepRecallService, request/UI/cancellation, association selection,
time interpretation and prompt condensation. ember-memory owns indexed lookup,
canonical/evidence validation, filtering, scopes and resource/lease lifetime.
Composition injects a deep read profile against the running engine's sole owner.
No agent import of SQL/private engine code, separate maintenance process or new
memory daemon, database, vector service, GPU scheduler or persistent search-job
framework is authorized by this design.

## 3. Existing abstractions and evidence inspected

Reuse AsyncMemoryClient search/search_archive/get/get_sources and typed memory/source
references, archive graph/grouping, temporal primitives, source grants/suppression,
origin/evidence dependency indexes, immutable generation files and leases.

Reviewed root ADRs 0001–0009, Memory ADRs 0006/0007, Agent ADR 0004, the Phase 6
runbook, the ABI/facade, canonical workers, EncodingPool/BgeEncoder, VectorPool,
IndexedRetriever/retrieve_context, source access and CharacterCore. Current memory/
get/source reads use foreground admission, the shared encoder is FIFO/locked, and
deep native/read owners exist but do not alone establish CPU/IO isolation.
General relationship/conflict traversal and full prompt time/evidence integration
are not already implemented merely because PLAN describes them.

The Phase 5 94.30 ms warm hybrid p95 was measured on duplicate synthetic beliefs
with cached query vectors. Component target misses, full scale/platform gates and
the Phase 3 real-claim gate remain as recorded. No new benchmark was run here.

## 4. Minimum new primitives and justified coupling

Agent typed request/budget/temporal/association/cluster/result contracts carry bounded
read orchestration and dependency manifests, not mutation authority. Existing public
reads gain a composition-bound deep resource profile, including nested source reads.

One optional capability-gated lookup_related intent (resolve/expand/validate) supplies
bounded aliases/relationships/source neighborhoods and batched final checks that are
not efficiently available through current contracts. Optional temporal intervals and
read manifests extend existing query/coverage models; version and negotiate legacy
strict response compatibility before implementation. No arbitrary graph/SQL API.

Prefer supported indexed joins, with small rebuildable relationship projections in
the existing derived store only if measured necessary. Root ADR 0010 records coupling;
prospective Memory ADR 0008 and Agent ADR 0005 should own backend and runtime detail at
implementation, subject to numbering availability. Do not fabricate delivered records.

## 5. Contracts, invariants and data ownership

Keep every expansion relevant to the original question and bound to a defensible
anchor connection. Generic User, shared topic, similarity or date proximity alone
cannot assert one incident; weak hypotheses do not acquire transitive evidence.
Separate accepted beliefs, raw observations and derived associations; searching,
clustering and final generation cannot approve an imported claim or change grants.

Resolve relative calendar time from requesting-local date/timezone. Preserve
event-valid, source-observed and belief-recorded axes; missing/ambiguous times stay
unknown. Evidence excerpts retain exact original revisions/coordinates/hashes.
Final item/edge/excerpt/prompt/cache delivery rechecks current scopes, suppression,
selection, lifecycle and dependencies; denied adjacency is not implied evidence.

Native leases remain owned until actual drain. Per-operation read manifests disclose
generation/policy changes; no cross-store/multi-operation atomic snapshot is invented.
Detailed resource ceilings and coverage semantics live in PLAN, not duplicated here.

## 6. Failure, cancellation and resource exhaustion

Use one active deep request with bounded queued requests, stage/call/byte/depth/
embedding limits and an overall deadline. Cancellation stops future admission and
queued work; late native extension calls retain slots/leases and are consumed.
Ephemeral results do not resume or promote on crash.

Route all deep reads/encodes/KNN through foreground-aware engine admission and bound
derived encoder/copy stages. An extra executor or priority label does not reserve CPU
or the shared ONNX lock. If measured residual contention violates regression caps,
defer semantic/refresh or all deep work under realtime load and expose partial/deferred
coverage. Keep ordinary deadlines and reserved capacity unchanged.

Missing capability/index/encoder/source, revocation, saturation and stale generations
return explicit limited/partial/unavailable/deferred outcomes rather than unbounded
scans, false exhaustive answers or private logging. Separate logical cancellation
from physical drain and invalidate pending unsafe response/history context.

## 7. Acceptance and measurement

PLAN P7/P8 gates and sections 30–33 require source-backed networking reconstruction,
unrelated shared entities, ambiguous projects/dates, corrections/conflicts, archive-only
evidence, exact-quote follow-up and honest abstention. Include independent characters,
mid-search revoke/forget/source switch, hidden reasoning, stale/rebuilt generations,
cancellation, cycles/fan-out and queue saturation.

Compare paired p50/p95/p99 ordinary recall/timeout/fallback rates with deep disabled,
idle and active plus refresh/encoding contention. Freeze representative query sets,
retain all samples and measure cold/warm/diverse/scale conditions. Initial planned
regression caps and quality thresholds are in PLAN, not achieved results. Concurrent
deep semantic mode cannot be advertised if the contention gate fails.

This documentation update is validated for AC coverage/uniqueness, local links,
preservation of historical records/standards/benchmarks and Markdown/diff integrity.
No runtime tests or real personal search/import/approval are implied by those checks.

## 8. Delivery, ADR handoff and stopping point

Phase 7 prepares bounded relationship/temporal/correction prerequisites. Phase 8 makes
the feature usable through explicit browser/text/voice invocation, source inspection,
cancellation and bounded P0 context. MCP, Odysseus and Phase 9 tasks are not prerequisites.

Phase 9 may reuse this same service for events/delegated research and optional correctly
classified P1/P2 planning. Phase 13 alone may enable measured inference preemption;
connected sessions still prohibit P3/P4 generation. Search itself requires no Qwen call.

The PLAN now consolidates every phase's AC, including scaffold, 0a/0b and ABI/v1 release,
while retaining prior future Phase 7/8 criteria and all historical delivery records.
A phase completion claim must map AC to evidence and distinguish unrun service/platform
gates. Stop at documentation/architecture review; no Phase 7/8 runtime implementation
or change to existing schema/ABI/gateway versions is performed in this task.

## Planned Phase 7 profile extension

[Root ADR 0011](0011-planned-phase-seven-user-profile.md) adds bounded query-independent
profile pins to the ordinary context baseline in Phase 7. Deep recall reuses that
profile and its dependency/source-selection checks without duplicating its prompt
content or allocation. Existing deep-recall limits and historical evidence remain
unchanged; this extension is documentation only.

## Planned Phase 8 ordinary temporal extension

[Root ADR 0014](0014-planned-conversational-temporal-recall.md) makes calendar/time-axis
filtering available to ordinary conversation independently of deep investigation.
Reuse its local resolver in the deep path; P8-AC-13 still requires no extra planning
call. An independently opt-in ordinary-query interpreter defaults off and is measured
separately. Historical ordinary/deep delivery uses explicit selectors and current access
checks. PLAN adds P8-AC-23 through P8-AC-30 without changing existing AC IDs, resource
limits or historical evidence. Event-date enrichment remains separately reviewed;
this design extension does not implement Phase 8 or mutate production data.
