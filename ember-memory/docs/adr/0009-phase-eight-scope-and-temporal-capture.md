# Memory ADR 0009: Source selection, temporal selectors and native checkpoints

Status: partial Phase 8 implementation, experimental and not deployed.

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
