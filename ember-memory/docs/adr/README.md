# Memory architecture decisions

These records own ember-memory implementation decisions. The
[root ADR index](../../../docs/adr/README.md) tracks application phases and integration;
[PLAN.md](../../../PLAN.md) remains authoritative. Root records link to these details
rather than duplicating schemas, evidence policy or worker implementation.

| Memory record | Scope/status | Root integration |
|---|---|---|
| [Memory ADR 0001](0001-ownership.md) | Ownership accepted; portions implemented in phases 1/2, later capabilities planned | [Phase -1](../../../docs/adr/0001-phase-minus-one-scope.md), [phase 1](../../../docs/adr/0004-phase-one-contracts.md), [phase 2](../../../docs/adr/0005-phase-two-canonical-engine.md) |
| [Memory ADR 0002](0002-phase-one-before-export.md) | Experimental ABI/archive 0.1.0 implemented before the personal export | [Root phase 1](../../../docs/adr/0004-phase-one-contracts.md) |
| [Memory ADR 0003](0003-canonical-engine-and-workers.md) | Engine 0.1.0 implemented/tested; review checkpoint before phase 3 | [Root phase 2](../../../docs/adr/0005-phase-two-canonical-engine.md) |
| [Memory ADR 0004](0004-source-import-and-authoring.md) | Official ZIP replay and native human entry implemented; private import verified, operator-reviewed claim adoption remains open | [Root phase 3](../../../docs/adr/0006-phase-three-source-and-runtime.md) |
| [Memory ADR 0005](0005-libraries-seeds-and-bundles.md) | Library approval/seeds, expiring grants, linked/selective bundles and schema 0.2.0 implemented; review checkpoint | [Root phase 4](../../../docs/adr/0007-phase-four-libraries-characters-bundles.md) |
| [Memory ADR 0006](0006-derived-retrieval-and-generations.md) | Graph-safe FTS/CPU BGE/sqlite-vec, hybrid scope, immutable generations/replay; 360-test gate passed, full performance/capacity acceptance and review pending | [Root phase 5](../../../docs/adr/0008-phase-five-retrieval-and-indexes.md) |
| [Memory ADR 0007](0007-extraction-jobs-and-summary-sources.md) | Structured exact-evidence proposals, extractor 0.1.2 claim schema, pinned resumable jobs and review; 408-test full gate passed; four private archive proposals await operator review | [Root phase 6](../../../docs/adr/0009-phase-six-extraction-and-background-inference.md) |
| [Memory ADR 0008](0008-native-consolidation-and-profile.md) | Native policy receipts/supports, typed transitions, bounded profile/source validation and backed-up schema 5 migration; implemented experimentally, review checkpoint | [Root phase 7](../../../docs/adr/0013-phase-seven-native-consolidation-and-profile.md) |


Phase 0a preparation is recorded in
[root reconnaissance](../../../docs/adr/0003-phase-zero-reconnaissance.md) and its
linked research/source-model/runbook. It does not imply real-export or production
normalizer acceptance.

Phase 6 now also includes interactive ingestion/review/seeding with durable human
decisions and restart-safe indexing; see Root ADR 0009 and Memory ADR 0007.

Phase 7 engine-owned native acceptance, exact support consolidation, temporal/related
contracts and bounded profile selection are recorded in Memory ADR 0008. Application
integration and measured acceptance evidence live in Root ADR 0013; this worktree has
not migrated primary production stores.

Numbering is local: Memory ADR 0003 differs from Root ADR 0003. Use a component
label and link. Keep memory decisions with this component if repositories are
separated, and update root phase status when a local decision changes application
behavior. Runtime/gateway decisions belong to
[agent ADRs](../../../ember-agent/docs/adr/README.md), not this backend.

The planned temporal extension in [Root ADR 0014](../../../docs/adr/0014-planned-conversational-temporal-recall.md)
and [Memory ADR 0008](0008-native-consolidation-and-profile.md#planned-phase-8-temporal-extension)
covers source-supported event dates, reviewed character-memory enrichment and
query-aware historical eligibility. This is accepted Phase 8 design, not a migrated
schema, implemented workflow or measured release.

[Memory ADR 0009](0009-phase-eight-scope-and-temporal-capture.md) records the in-progress Phase 8 source/temporal/capture implementation.

The 2026-10-16 review corrections are recorded in the component Phase 8 ADR and
[Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md).

The same records include the user-directed
[ordinary word-recall correction](../../../docs/phase-8-word-recall.md): extracted
value matches before limits and requested context limits within the existing ABI.
