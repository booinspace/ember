# Compatibility and release status

| Surface | Current version | Status |
|---|---|---|
| Memory ABI package | 0.1.0 | Experimental typed contracts; canonical engine implements them |
| Memory ABI / Normalized Archive | 0.1.0 / 0.1.0 | Generic core implemented; source-dependent and integration acceptance open |
| Predicate definitions | 0.1.0 | Six conservative initial definitions; no automatic activation |
| Canonical object JSON | ember.canonical-json@0.1.0 | Specified/tested Python 3.12 byte encoding |
| Canonical engine package | 0.1.0 | Experimental SQLite core, owned workers and sync/async clients |
| CLI/importer/agent packages | 0.0.0 | Preparation/scaffold; agent local extra pins engine 0.1.0 |
| Reconnaissance report | ember-memory.reconnaissance/0.0.2 | Nested/streamed/directory preparation inspection |
| Deployment TOML | config_version = 1 | Validated preparation format; no runtime guarantee |
| Canonical SQLite schema | 0.1.0 / user_version 1 | Initial owned schema; created by explicit engine composition |
| Bundle format | Planned 0.1.x, phase 4 | Not implemented/frozen |
| Gateway/runtime/provider contracts | Pending their phases | No supported generation transport yet |

Agent and engine package dependencies pin `ember-memory-abi==0.1.0`. The ABI depends
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
