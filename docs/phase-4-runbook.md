# Phase 4 Windows maintenance and architecture review

Run from `C:\Users\operator\repos\ember`. Stop `ember start`/direct chat with
Ctrl+C before database maintenance: the existing root ownership lock prevents
another process from opening the same canonical root. Each command runs locally;
SQLite needs no server. Phase 4 adds no voice/UI feature.

```powershell
$memory = ".\.venv\Scripts\ember-memory.exe"
$root = ".\.local\data"
& $memory --help
& $memory bundle --help
```

The first explicit open of an old root upgrades schema 1 to schema 2 and preserves
consistent `*.schema-1*.backup.db` recovery images beside the original databases.
The tests verify IDs, quotes and replay survive upgrade and failed-upgrade retry.
Your imported source is not automatically approved or turned into memories.

## Independent characters and reviewed library knowledge

A new character is fresh: no imported grants/beliefs. The `character` command emits
its character-bound human subject; its `store_id` is the character UUID.

```powershell
& $memory character --root $root
```

Existing Phase 3 `remember` creates character-owned native memory, and `confirm`
adopts a reviewed character-bound candidate directly. Phase 4 adds a shared
library approval path for independently seeding multiple characters. It accepts
library-owned candidates, not a candidate whose owner is another character.
Do not use `--yes` before reviewing exact original evidence; imported/model verdict
strings are not approval. The narrow production GPU detector still matched no
supported claims in the official archive.

Register explicit library entity context first, using a local `Entity` JSON file:
`reference.store_id` is the library UUID, `reference.entity_id` is a new UUID4,
`revision` is 1, and `name`/optional `aliases` are human-reviewed context. None
of these IDs are provider IDs. A sanitized shape is:

```json
{
  "reference": {
    "store_id": "LIBRARY_UUID",
    "entity_id": "NEW_ENTITY_UUID"
  },
  "revision": 1,
  "name": "User"
}
```

Then propose a supported explicit source message, review its `draft.content.evidence`
against the original immutable source, and approve the exact candidate. You can also
supply a reviewed generic `CandidateMemory` JSON through the same approval interface;
Phase 4 does not broaden automatic extraction.

```powershell
& $memory library-entity --root $root --library LIBRARY_UUID `
  --input .\.local\review\entity.json --yes

& $memory library-propose --root $root --library LIBRARY_UUID `
  --subject ENTITY_UUID --record MESSAGE_UUID `
  --output .\.local\review\candidate.json

& $memory library-approve --root $root --library LIBRARY_UUID `
  --input .\.local\review\candidate.json --key approval-001 --yes
```

Approval emits a knowledge UUID and revision. You can inspect it with `library-show`.
Seed explicit snapshots into each character; repeat `--knowledge UUID@REVISION`
to select more than one. Reusing the same key/input resolves the same receipt/IDs;
changing content under a used key fails. Different characters receive independent
belief/entity UUIDs and keep exact shared evidence references.

```powershell
& $memory library-show --root $root --library LIBRARY_UUID `
  --knowledge KNOWLEDGE_UUID --revision 1

& $memory seed --root $root --character CHARACTER_UUID --library LIBRARY_UUID `
  --knowledge KNOWLEDGE_UUID@1 --key seed-001 --yes

& $memory recall --root $root --character CHARACTER_UUID
& $memory sources --root $root --character CHARACTER_UUID --memory MEMORY_UUID
```

Seeded characters gain supporting evidence reads; they gain no archive search.
Selected-archive/primary behavior requires separately granted exact historical
snapshot scope. Imports never silently expand it or rewrite a character's beliefs.

## Grants, revocation and forgetting

`grant`/`revoke` take a local `SourceGrant` JSON. Each reference has
`source_store_id`, `record_kind`, `record_id`, `record_revision` and optional
`part_id`. Capabilities are `evidence_read` and/or `archive_search`; archive search
requires whole-record references. Optional `expires_at` is canonical UTC, for
example `2026-10-10T12:00:00.000000Z`. An omitted expiry is indefinite.

```powershell
& $memory grant --root $root --character CHARACTER_UUID `
  --input .\.local\review\grant.json --yes
& $memory revoke --root $root --character CHARACTER_UUID `
  --input .\.local\review\grant.json --yes

& $memory forget-preview --root $root --character CHARACTER_UUID --memory MEMORY_UUID
& $memory forget --root $root --character CHARACTER_UUID --memory MEMORY_UUID `
  --revision 1 --reason "Explicit operator forgetting" --key forget-001 --yes
```

Revocation/expiry leave adopted beliefs intact and deny corresponding source access.
Forgetting previews affected dependencies and persists conservative suppression;
it can hide other beliefs using the same source part. It prevents reseeding the
forgotten origin under a new key and leaves other characters/library data unchanged.

## Linked backup and restore

Outputs are new directories outside the live data root. Source backup is an explicit
full library backup, including retained raw exports/assets. Back up each library
once; character bundles reference it. Canonical state, receipts, revisions,
suppression, grants, pins and character configuration are retained. Indexes and
runtime/provider credentials are omitted.

```powershell
& $memory bundle source --root $root --library LIBRARY_UUID `
  --output .\.local\backups\source-001
& $memory bundle character --root $root --character CHARACTER_UUID `
  --output .\.local\backups\character-001

& $memory bundle restore --root .\.local\restored-linked `
  --bundle .\.local\backups\source-001 `
  --bundle .\.local\backups\character-001
```

Repeat `--bundle` for other character/source bundles. A library is restored once.
Missing/stale library snapshots, contradictory duplicate stores, changed hashes or
invalid coverage fail before publication. Restore never merges into an existing
root; use a new root. It validates the original approved origins and exact currently
permitted evidence as well as file/SQLite integrity. Providers remain disabled.
Character/library IDs survive; the new root's internal registry ID may differ.

## Selective self-contained export

Choose the memory IDs to carry, or omit `--memory` to include all owned beliefs.
All selected belief revisions and relevant receipts/pins/suppression are retained.
The character's stable human subject/personality configuration remains usable for
new explicit memory entries after restore.

```powershell
& $memory bundle character --root $root --character CHARACTER_UUID `
  --self-contained --memory MEMORY_UUID `
  --output .\.local\backups\portable-001

& $memory bundle restore --root .\.local\restored-portable `
  --bundle .\.local\backups\portable-001

& $memory sources --root .\.local\restored-portable `
  --character CHARACTER_UUID --memory MEMORY_UUID
```

Evidence closure copies only authorized exact spans. It excludes unrelated messages,
unquoted text in the same message, native history outside the selection, raw ZIPs,
assets and indexes. Suppressed/revoked source bytes are omitted. Granted but missing
evidence blocks export. Sparse library stores explicitly report `evidence_only`:
quotes work, full original-source/archive search and source imports do not.

Partial exports are bounded; large selections fail explicitly rather than silently
truncating. Linked backups remain the full-state path. See Memory ADR 0005 for limits.
To chat with restored state, deliberately create a deployment configuration with its
`data_root` and retained `character_id`, and start the existing runtime yourself.
Restore never launches services or rewrites external deployment settings.

## Validation and architecture review

```powershell
$python = ".\.venv\Scripts\python.exe"
& $python -m pytest ember-memory/packages/memory-engine/tests/test_libraries_bundles.py -q

$env:EMBER_LIVEKIT_EXE = (Resolve-Path .\.local\tools\livekit\1.13.9\livekit-server.exe).Path
$env:EMBER_REAL_CONFIG = (Resolve-Path .\.local\config\deployment.non-mtp.toml).Path
& $python scripts/check.py
& $python scripts/export_contract_schemas.py --check
```

For the full opt-in suite, start the matching gateway in another terminal first:

```powershell
.\ember-agent\.venv\Scripts\ember.exe gateway `
  --config .\.local\config\deployment.non-mtp.toml
```

The earlier real-model adapter test requires that running gateway; actual-room tests
attach to it and leave it running. Stop that terminal with Ctrl+C afterward.
Only set the opt-in variables when the explicit setup/model configuration exists.
The integration tests use isolated synthetic libraries/characters and exercise
actual room recall after portable seed restore, exact provenance and further native
memory entry. They do not approve your private archive. They stop owned services
and leave independently managed Ollama running.

[Root ADR 0007](adr/0007-phase-four-libraries-characters-bundles.md) records the eight
checks and coupling. [Memory ADR 0005](../ember-memory/docs/adr/0005-libraries-seeds-and-bundles.md)
owns the format/schema/transactions. Review this architecture before Phase 5.
