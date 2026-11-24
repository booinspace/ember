# Memory ADR 0009: Source selection, temporal selectors and native checkpoints

Status: partial Phase 8 implementation, experimental; main-device integration is deployed with broader acceptance still open.

## Decision

Reuse canonical schema/projections and existing owned workers. RecallSelection
is an optional capability: native inclusion, all/selected/no imports, library
overrides and CAS revision. Persist it in the character store. Changes increment
policy_epoch; they do not grant source access or rewrite accepted memories.

Compile origin selection before SQL candidate limits. Apply it to canonical and
indexed search, profile selection, pins, archive grants and delivery validation.
Default policy preserves the certified derived fast path. Bundles copy recall and
capture policy; selected bundles exclude unrelated pending source work.

TemporalSelection qualifies exact certificate references under a historical query.
Reuse memory_scope for selector validation. Do not hydrate memories or search vectors
during validation. Unqualified dependencies and current pins remain current.
Episode interval ends do not expire remembered events. Current source/grant expiry
still bounds certificates. Preserve old empty certificate serialization.

Native archive writes enqueue bound user messages atomically. CaptureService uses
exact source-reference jobs and existing processing/native acceptance; P3 admission
belongs to the gateway. Cursor-paged acceptance and deterministic receipt keys bound
memory and prevent duplicates. Settings are independent of recall. Model changes
cannot silently repin an interrupted processing job.

Temporal extractor 0.3.0 has its own schema/hash dispatch. Keep legacy ClaimBatch
unchanged. Resolve exact date expressions against source observation and configured
IANA zone, preserving precision/basis and a second exact supporting evidence span.
Unknown dates stay undated; completed-event classification rejects plans,
hypotheticals and assistant narratives.

## Compatibility, consequences and unfinished work

ABI 0.5.0, engine 0.6.0/schema 6 and disposable layout 0.6.0 are experimental.
Schema migration is backed up and additive; schema-5 upgrades skip old backfills.
An older vector layout cannot use the new episode current-clock shortcut.

Temporal groups add bounded header SQL reads only when historical references are
selected. Restrictive policies add canonical joins and require measured performance
acceptance. No new graph service, database, scheduler or global mutable cache exists.

Reviewed manual enrichment is implemented with bounded batch replay/CAS and
selected-bundle history. Complete library/seed isolation and deep-related
admission/quality acceptance remain unfinished. See [Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md)
and [validation](../../../docs/phase-8-validation.md). Existing
[Memory ADR 0008](0008-native-consolidation-and-profile.md) retains Phase 7 evidence.

## Opt-in canonical identity lookup

`ember.identity_recall_v1` is an optional `MemoryQuery.options` extension for structured
search. It matches bounded exact alias phrases against character-owned canonical entities.
`origin_store_ids`, when supplied, narrows eligible origins before the result limit; it cannot
grant access, select another character, or start archive search. Lookup retains current
lifecycle, temporal, suppression, selection, and grant checks. It checks source headers and
rechecks eligibility before returning. Unavailable proof is excluded and reported as partial.

No index rebuild, default ranking change, fuzzy expansion, or canonical writes. See the
[public seeded-belief replay](../../../docs/memory-benchmark.md#approved-belief-replay-on-miku-fedora)
for scorer boundaries and measured results.

### Current-belief preview

`ember.current_belief_v1` on a structured `MemoryQuery` selects active accepted assertions,
preferences, and beliefs. It rejects historical selectors and applies known validity bounds
also to episodes in this route; ordinary episode and historical lookup keep their own semantics.
`origin_store_ids` narrows the same canonical scope before ranking and cannot grant access.

Exact alias matches win. If none are eligible, try one edit (including adjacent transposition)
on ASCII words of 4–64 characters with an unchanged two-letter prefix. Resolve against eligible
claims only, reject ambiguous typo matches, and match the resulting canonical IDs. Lookup scans
at most 256 query words, materializes at most 257 alias rows to detect cap overflow, and shares
identity lookup's 150 ms soft deadline. Blank, unmatched, or ambiguous input can return nothing;
no archive fallback. Evidence headers, current authorization, and delivery certificate expiry
still apply. No identity writes or storage migration.

See [paired seeded replay](../../../docs/memory-benchmark.md#current-belief-preview) for
acceptance evidence and remaining raw-ingest and answer-quality gates.

`current_conflict_status_v1` adds an opt-in status-only context read for selected current
revisions. Bounded inspection checks exact native conflict edges, review, suppression and
source headers. It never returns deferred claim text, source excerpts, or candidate IDs.
Both positive and negative statuses are rechecked on context validation. Exceeding the edge
budget cannot certify absence. [Typed chat routing](../../../docs/adr/0015-phase-eight-implementation.md#explicit-typed-recall)
uses this capability; default search, grants, and canonical records are unchanged.

## Bounded deep engine profile

Existing owned workers accept an explicitly read-only request profile. It copies
reader state, installs a request meter on its own native connections and restores
callbacks/cache settings on every exit, including failed setup. It counts actual
statements/materialized rows/cumulative bytes; SQLite value-size bounds prevent
unbounded payload hydration. This is not a claim that Python/native peak RSS has
been measured.

Existing related lookup now supports bounded exact source spans, alias/source
neighborhoods, typed temporal selection and raw archive dependencies. Final
certification can hydrate selected references without expanding relationships.
Source switches do not treat a hard proof as the origin of a memory; explicit raw
excerpts are independently selected and granted. Current revocation/expiry and
suppression apply under historical selectors.

The processing loop accepts a runtime continuation predicate between immutable
steps. It pauses without failing a window or changing pinned source/model/schema
specifications when realtime returns. Already started model work drains normally.
Legacy extraction and native acceptance contracts remain unchanged.

## Processing and enrichment update: 2026-10-13

Dated native extractor 0.3.1 reuses the archive 0.3.0 date contract with native
predicate/policy rules; legacy jobs and hashes remain immutable. Exact English
day expressions preserve day precision, including London DST; unsupported or
invalid event dates stay unknown rather than becoming month-wide dates.

The existing capture ledger records failure codes and separates runnable from
failed checkpoints. Idle continuation processes later work; an explicit
confirmed-user retry reuses the failed checkpoint/job. No automatic approval of
an import, retry scheduler or replacement writer is introduced.

Temporal enrichment pins character revision/source/date support, records explicit
decisions, and applies through existing CAS and mutation receipts. A 64-item batch
commits independently, reports per-item failures and resumes safely. Selected
bundles copy eligible review records with the selected exact evidence closure;
tests prove review/replay restoration and unchanged text-vector reuse. Prior
schema-4/5 bundle versions are recognized and migrated through existing backups.

Mixed deep source observations are constrained by selected source stores and
conversation IDs; archive grants are checked separately from evidence-read grants.
The combined dependency certificate retains both controls. Nonempty temporal
selector and historical validation measurements are recorded in the root
acceptance tracker; broad-scale/refresh/quality acceptance remains open.

Source-relative dates additionally check original source-clock precision. Unknown
or year-only observation clocks cannot manufacture day/month-relative event dates;
explicit supporting dates remain independently usable. This reads the already
resolved immutable source record, without changing WindowRef or repinning old jobs.
Focused enrichment isolation tests preserve immutable library approvals, a second
character's seed, seed-replay behavior and an existing legacy extraction job.


## Large-corpus branch validation: 2026-10-14

DeepSources implements the optional SourceBranchResolver backend capability. An exact
selected-path lookup replaces repeated connection/payload loads of every node preceding
each quoted message. The immutable conversation and matching node retain hash/identity
checks; the first missing earlier node still makes the branch ambiguous. Paths above
1,024 nodes are unavailable within this profile. Existing schema/source identities,
ordinary resolver semantics and the public memory ABI remain unchanged.

A per-investigation LRU retains at most 256 verified immutable source records and 2 MiB
serialized payload. It shares the existing budget and does not cache permissions or
source availability. Exact evidence validation is reused for every quote, and delivery
still checks current grant windows, suppression, selection and source headers. Current
memory revisions are loaded once when identical to the requested revision.

Budgets remain 4,096 statements, 2,048 materialized rows and 16 MiB cumulative reads.
The isolated 12,563-memory snapshot no longer exhausted these limits for the matched
42 investigations. See [Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md)
and its aggregate report. Python/native peak RSS, refreshed-index contention and broad
quality gates remain open; serialized cache bytes are not a peak-RSS measurement.
## Source context and selected certificates: 2026-10-16

Both native processing profiles validate the original source's reported,
hypothetical and fictional qualifications, independently of a model's selected
subspan or factual label. Unsupported proposals stay deferred; prior receipts and
accepted history are not rewritten. Synthetic capture/extraction/acceptance tests
reproduce qualifier stripping under both profiles and verify restart visibility.

The optional `selected_context_dependencies_v1` capability negotiates
`RelatedRequest.refresh_dependencies` on the existing validate operation. Canonical
header/evidence metadata reconstructs selected hard evidence, effective grant
windows and current-state expiry. Caller-supplied history/profile boundaries remain
conservative constraints; historical selectors, current policy/source controls and
suppression are still validated. Explicit raw excerpt/archive dependencies retain
their independent permissions. Oversized/invalid results return no certificate.
No source bodies, memories, vectors or relationships are hydrated. The existing
response dependency field carries the result; ordinary validation and default
request serialization remain compatible. This is an experimental ABI 0.5.0 additive
negotiated contract; engine/schema/index versions and migration requirements remain.

Regression tests cover omitted versus selected expiry, permanent grants after an
unused temporary grant expires, revocation, source exclusion, row limits and metadata-
only operation. Root ADR 0015 records integration and remaining release gates.

## Ordinary extracted-word selection: 2026-10-16

The user requested per-word recall with more relevant extracted values. Reuse the
existing FTS index and canonical revision join: tokenize Unicode words, remove
conversational filler from multiword lexical queries and prioritize matches in
extracted values over internal predicate/entity labels before top-k. Importance
orders equal word coverage; BM25 and the existing single semantic encoding remain.
The lexical candidate bound is 100 and semantic bound 40, under the unchanged
150 ms soft deadline. Literal value coverage contributes an explicit ranking reason;
runtime ranker `ember.rrf-extracted-words@0.2.0` is distinct from generation metadata.

Canonical fallback uses the same literal word scoring in SQL before its limit.
It no longer requires the whole conversational question as a substring or samples
only the first high-importance rows. It remains explicitly degraded without a
derived index. Current source selection, grants, suppression and temporal/revision
eligibility precede candidate limits and final canonical checks remain mandatory.

`retrieve_context` now honors its requested limit within the existing 100-row ABI
envelope shared with pins, instead of silently restricting ordinary rows to six.
Prompt packing is agent-owned. No source, predicate, identity, accepted claim or
index schema is rewritten; existing generations need no rebuild. See
[Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md) and the
[design](../../../docs/phase-8-word-recall.md). Historical performance reports need
new measurements for the expanded ordinary candidate bound.

## Memory graph browser

The engine reads each graph page from a single canonical snapshot on the bounded
deep worker. Current source selection, grants, temporal eligibility and suppression
apply before the page limit. An explicit relationship needs visible current
endpoints and permission to read its evidence. The operator sees extracted labels
and relationship types without source excerpts. The graph defers to foreground
recall and has no model or derived-index dependency.

The overview returns at most 200 memories per canonical snapshot and supplies
the eligible total with the first batch. Cross-batch relationships are checked
against the same current query scope for both endpoints before they are returned.
Neighborhoods keep their 36-memory limit.
