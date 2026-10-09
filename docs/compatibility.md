# Compatibility and release status

| Surface | Current version | Status |
|---|---|---|
| Memory ABI package | 0.4.0 | Experimental typed contracts; optional related validation and temporal lookup |
| Memory ABI / Normalized Archive | 0.4.0 / 0.1.0 | ABI adds optional related validation/temporal reads; archive format remains unchanged |
| Predicate definitions | 0.1.0 | Existing definitions plus explicit profile/ownership predicates; engine policy gates native activation |
| Canonical object JSON | ember.canonical-json@0.1.0 | Specified/tested Python 3.12 byte encoding |
| Canonical engine package | 0.5.0 in the isolated Phase 7 worktree | Native policy/consolidation, temporal relationships and bounded profile; ABI 0.4.0, archive/bundle 0.1.0 |
| CLI/importer/agent packages | 0.1.0 in the isolated Phase 7 worktree | Native maintenance/profile controls, Phase 6 extraction, conservative P3/P4 and shared terminal/LiveKit profile core |
| Reconnaissance report | ember-memory.reconnaissance/0.0.2 | Nested/streamed/directory preparation inspection |
| Deployment TOML | config_version = 1 | Validated preparation format; no runtime guarantee |
| Canonical SQLite schema | 0.5.0 / user_version 5 | Additive native policy, relationship, projection and profile metadata; backed-up schema-1/2/3/4 upgrades preserve IDs/history |
| Bundle format | 0.1.0 experimental | Scoped registry, source/linked character/selective evidence bundles; validated new-root restore |
| Gateway transport/health | Request 0.3.0 opt-in / health 0.2.0 | Optional upstream prefill duration; accepts legacy 0.1/0.2 requests and omits added usage field for them; health and background extraction wire remain 0.2.0 |
| Derived index layout | 0.5.0 / headers 0.2.0 | Disposable grant-expiry projection enforces source scope before lexical/vector top-k; older layout requires explicit update |
| Official ZIP replay metadata | 0.3.0, importer-private | Nested ZIP import, metadata-only recovery and no-op replay verified; not a Memory ABI/schema change or published release |
| Provider/extended scheduling contracts | Pending their phases | P1/P2 and provider integration remain at their planned phases |
| Saved-memory summary adapter | chatgpt_memory_summary@0.1.0 | Explicit UTF-8 text/Markdown or documented JSON entries; originals retained, no automatic approval |
| Extraction/review | Legacy `ember.atomic-claims@0.1.2`; native `@0.2.0`; review/policy 0.1.0 | Legacy import hashes remain exact; new native predicate/profile fields use explicitly versioned jobs and engine-owned policy |

These working-tree package versions are experimental implementation metadata, not
published releases. The phase 3 code passed its recorded local quality gate and the
official ZIP was imported privately. Operator-reviewed real-source adoption remains
open. See [the implementation record in PLAN](../PLAN.md) and
[Root ADR 0006](adr/0006-phase-three-source-and-runtime.md).

Agent and engine package dependencies pin `ember-memory-abi==0.4.0` in the Phase 7
worktree. The ABI depends only on Pydantic and Python's standard library. Sibling editable installs remain
local development wiring; lockfiles are not evidence of a published release.

There is no persisted ABI 0.0 archive requiring migration: the previous package was
an empty reservation. Existing private sources/reconnaissance reports remain intact.
The report reader accepts both `ember-memory.reconnaissance/0.0.1` and the earlier
`character-memory.reconnaissance/0.0.1` identifiers. New reports use 0.0.2.

Unknown namespaced extensions and wire kinds/modalities remain readable. Opaque
payload/source wrappers represent unsupported kinds without acquiring write
semantics. Unsupported writes fail explicitly. Schemas describe structure; runtime
validation handles evidence hashes, Unicode boundaries, relationships and policy.

Pre-1.0 contract changes require an explicit compatibility matrix and, once canonical
state exists, migrations/backups. They do not authorize wiping history. ABI 1.0
freezes only after the real-source/gateway/character/restore acceptance in PLAN.md.
See [phase 2 architecture](phase-2-canonical-engine.md) for storage and recovery limits.


Phase 4 acceptance and migration/bundle limits are recorded in
[Root ADR 0007](adr/0007-phase-four-libraries-characters-bundles.md) and
[Memory ADR 0005](../ember-memory/docs/adr/0005-libraries-seeds-and-bundles.md).
The first explicit open of a schema-1 root upgrades each store under ownership,
with a consistent pre-upgrade .backup.db retained beside its canonical database.
Restore never changes an existing root. It validates a supplied complete bundle
set and constructs a new root with the original character/library UUIDs and
providers disabled. Selective snapshots preserve explicit partial coverage; they
cannot serve as complete shared source libraries or resume imports into them.


Phase 5 adds query modes/filters and a default native-context option. Existing
canonical JSON and Normalized Archive models retain their original identity;
ABI-0.1 consumers with strict extra-field rejection must update before accepting
new query JSON. Generated schemas for 0.1.0 and 0.2.0 are retained explicitly.
The ABI remains SQLite/ONNX/LiveKit independent. Concrete search dependencies
are optional engine adapters installed by workspace/local-deployment extras.

Schema 3 extends phase 4 without rewriting canonical payloads. Schema-1/2 stores
receive consistent versioned `.backup.db` snapshots before migration. Bundle 0.1.0
accepts schema-2/3 databases, upgrades in staging and omits disposable indexes.
Derived layout 0.4.0 is independently versioned; missing/older model/layout indexes
require explicit maintenance and degrade safely. See
[Phase 5 architecture](adr/0008-phase-five-retrieval-and-indexes.md).


Phase 6 is recorded in [Root ADR 0009](adr/0009-phase-six-extraction-and-background-inference.md)
and its [operator runbook](phase-6-runbook.md). ABI 0.3.0 extends supported episode-write
modalities; fact predicate compatibility is retained. Old payloads/archive models remain
readable and schemas for ABI 0.1.0/0.2.0/0.3.0 are retained. Runtime health emits 0.2.0;
strict earlier consumers must update. Requests continue accepting 0.1.0/0.2.0. P1/P2
fail explicitly, and P3/P4 require the distinct background credential. Canonical engine
processing uses an injected model Protocol; only its optional HTTP adapter requires
`httpx` via the processing extra. It does not depend on the agent Python package.

Phase 7 is implemented experimentally in the isolated `ember-phase-seven` worktree;
see [Root ADR 0013](adr/0013-phase-seven-native-consolidation-and-profile.md) and the
[AC evidence](phase-7-validation.md). Schema 5 and disposable layout 0.5.0 require an
explicit backed-up migration/index update before production deployment. Legacy extractor
0.1.2 hashes and gateway health/request 0.1/0.2 behavior remain compatible; request 0.3
is opt-in for measured prefill. Archive and bundle remain 0.1.0. Do not infer that the
primary checkout or its data has been upgraded.

Schema 4 upgrades 1/2/3 additively with consistent pre-upgrade backups and transactional
rollback. Bundle 0.1.0 accepts schema 2/3/4. Full source snapshots retain jobs and review
history. Independent seeds/selected character snapshots retain referenced generic
processing and policy definitions without copying unrelated private jobs/review logs.
Partial evidence libraries retain their explicit coverage and cannot resume full imports.
Experimental working-tree versions are not published releases or ABI 1.0 acceptance.
