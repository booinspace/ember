# Compatibility and release status

| Surface | Current version | Status |
|---|---|---|
| Memory ABI package | 0.2.0 | Experimental typed contracts; canonical engine implements them |
| Memory ABI / Normalized Archive | 0.2.0 / 0.1.0 | Generic core implemented; source-dependent and integration acceptance open |
| Predicate definitions | 0.1.0 | Six conservative initial definitions; no automatic activation |
| Canonical object JSON | ember.canonical-json@0.1.0 | Specified/tested Python 3.12 byte encoding |
| Canonical engine package | 0.3.0 in the working tree | Phase 5 derived retrieval/indexes; ABI 0.2.0, archive 0.1.0 |
| CLI/importer/agent packages | 0.1.0 in the working tree | Phase 3 components implemented; operator-reviewed real-source memory adoption remains open |
| Reconnaissance report | ember-memory.reconnaissance/0.0.2 | Nested/streamed/directory preparation inspection |
| Deployment TOML | config_version = 1 | Validated preparation format; no runtime guarantee |
| Canonical SQLite schema | 0.3.0 / user_version 3 | Additive covering/expression indexes; backed-up schema-1/2 upgrades preserve canonical IDs/history |
| Bundle format | 0.1.0 experimental | Scoped registry, source/linked character/selective evidence bundles; validated new-root restore |
| P0 gateway/runtime contracts | 0.1.0 draft | Synthetic and short real-model probes passed earlier; sustained GPU and full voice-room delivery remain unverified |
| Official ZIP replay metadata | 0.3.0, importer-private | Nested ZIP import, metadata-only recovery and no-op replay verified; not a Memory ABI/schema change or published release |
| Provider/extended scheduling contracts | Pending their phases | P3/P4, P1/P2 and provider integration remain at their planned phases |

These working-tree package versions are experimental implementation metadata, not
published releases. The phase 3 code passed the current local quality gate and the
official ZIP was imported privately. Operator-reviewed real-source adoption remains
open. See [the implementation record in PLAN](../PLAN.md) and
[Root ADR 0006](adr/0006-phase-three-source-and-runtime.md).

Agent and engine package dependencies pin `ember-memory-abi==0.2.0`. The ABI depends
only on Pydantic and Python's standard library. Sibling editable installs remain
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
