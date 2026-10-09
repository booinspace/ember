# Root ADR 0015: Phase 8 implementation and acceptance boundaries

Status: implementation in progress in the independent ember-phase-eight clone.
Base: origin/master 640888d59ffaa0f501b7453e161fd62c9e945ab6.
Date: 2026-10-09. Phase 8 is not complete or deployed.

## Decision and ownership

The approved [implementation proposal](../phase-8-implementation-proposal.md) remains
the scope. Extend the existing canonical owner, processing jobs, ABI, character core
and local composition. Add neither a database nor an inference scheduler.

The engine owns persisted recall selection, current authorization, SQL eligibility,
native checkpoints and exact date evidence. The agent owns conversational calendar
interpretation, prompt/history selection, delivery and service lifecycle.

Recall selection is independent of capture, import approval, grants and model identity.
Origin selection narrows eligibility before candidate limits, including profiles,
pins and archive search. Settings and policy epochs commit atomically under confirmed
user authority with revision checks; bundle restore preserves the controls.

Ended episodes describe occurrence intervals and remain recallable. Current-state
facts retain validity checks. A historical dependency carries its original selector.
Validation reuses canonical eligibility without hydration or similarity search;
current suppression, forgetting, grant expiry and origin controls still apply.
Current pins retain current eligibility. Dependency limits remain 256 references
combined and 64 KiB, with at most 32 temporal groups of 100 references each.

Native delivery and its capture checkpoint share the archive transaction. Bounded
incremental jobs select exact owned user messages. Old JobRequest serialization and
extractor 0.1.2/0.2.0 hashes remain unchanged. Capture reuses native acceptance receipts,
and repeated acceptance cannot duplicate memories. The runtime releases its realtime
reservation before bounded P3 consolidation; the gateway remains final GPU admission
authority. Realtime connected-session extraction is not enabled.

Extractor 0.3.0 has a separate dated schema and prompt. It accepts exact supporting
calendar expressions, not model-generated timestamps. Source-relative dates use the
original observation clock. Unknown dates stay unknown; hypothetical/planned content
cannot become a completed episode. Existing jobs are never repinned.

## Versions and scalability

Experimental ABI 0.5.0, engine 0.6.0 and schema 6 introduce these contracts. A backed-up
additive schema-5 migration skips prior projection backfills. Index layout 0.6.0
distinguishes episode occurrence from state expiry; old indexes require canonical
eligibility or explicit rebuild rather than silently changing their vector filters.

Default current recall retains the existing certified projection path. Restrictive
origin policies use canonical eligibility. Temporal parsing is local and bounded by
the existing 8192-character query limit. Date-only recall is structured; topical
calendar queries reuse the existing lexical/vector search. Measure restrictive and
historical paths separately; no constant-time claim is made for corpus-sized vectors.

## Evidence and open gates

[Validation status](../phase-8-validation.md) records only executed tests.
Root ADRs [0010](0010-planned-deep-associative-recall-and-phase-acceptance.md) and
[0014](0014-planned-conversational-temporal-recall.md) retain planned acceptance.
[Memory ADR 0009](../../ember-memory/docs/adr/0009-phase-eight-scope-and-temporal-capture.md)
and [Agent ADR 0006](../../ember-agent/docs/adr/0006-phase-eight-calendar-and-capture.md)
own component decisions.

Browser/voice composition, delivery acknowledgements, bounded deep orchestration,
reviewed enrichment, optional interpretation, full contention/quality measurements
and requested code-review remain open. No real Qwen/audio acceptance has run while
the primary ingest owns the GPU. Synthetic tests cannot replace those measurements.
