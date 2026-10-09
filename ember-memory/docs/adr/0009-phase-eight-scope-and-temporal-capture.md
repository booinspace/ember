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

The reviewed enrichment workflow and complete deep-related admission/quality
acceptance remain unfinished. See [Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md)
and [validation](../../../docs/phase-8-validation.md). Existing
[Memory ADR 0008](0008-native-consolidation-and-profile.md) retains Phase 7 evidence.
