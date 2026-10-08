# ember-memory

Owns durable source archives, canonical memory/provenance, grants/suppression,
retrieval, extraction, consolidation, seeds, and bundles.

The uv workspace contains separately packaged `memory-abi`, `memory-engine`, and
`memory-importers`; the workspace root packages the maintenance CLI from `cli/src`.
Current experimental versions are ABI 0.3.0, engine/schema 0.4.0 (user_version 4),
and archive/bundle 0.1.0. Source inspection/import, canonical storage/provenance,
independent library approval/seeds/bundles and Phase 5 retrieval are implemented.
Phase 6 adds explicit saved-memory summary import, model-based exact-evidence proposals,
pinned resumable jobs, persistent policy/review decisions and operator seeding commands.
It uses the same agent-owned gateway through an injected model Protocol/optional HTTP
adapter. Source import and extraction never automatically activate personal beliefs.

Read [the Phase 6 operator runbook](../docs/phase-6-runbook.md),
[Memory ADR 0007](docs/adr/0007-extraction-jobs-and-summary-sources.md) and
[the current compatibility matrix](../docs/compatibility.md). Stop for architecture
review before Phase 7. Phase 5's performance/capacity gates remain explicit.

```powershell
uv sync --locked --all-packages --python 3.12
uv run --locked ember-memory --help
```

Use the outer workspace phase 0 runbook before processing the actual archive.
`../PLAN.md` is the current design and engineering standard. The shared Ruff,
strict Pyright, and pytest configuration lives in `../pyproject.toml`; run its
quality gate from the workspace root. Importer unit tests and synthetic fixtures
are colocated under `packages/memory-importers/tests/`.
Raw sources and real reports belong in ignored local storage. The nested `.gitignore`
also protects private files if this root becomes its own Git repository.

## Architecture documentation

[Memory ADRs](docs/adr/README.md) own backend/contract detail and link to
[root ADRs](../docs/adr/README.md), which record delivery and agent integration.
Numbering is independent; qualify by component/path. Shared runbooks and phase
walkthroughs remain in root docs. PLAN is authoritative; accepted planned
capabilities are not implementation claims.


## Phase 4 library and bundle maintenance

The Phase 4 baseline introduced engine 0.2.0 support for operator-reviewed immutable library approvals,
independent character seeds/entity snapshots, exact expiring grants/revocation,
suppression across reseeding, consistent linked backups and selective portable
exact-evidence export/restore. Public ABI/archive remain 0.1.0. Old canonical
schema 1 upgrades additively to schema 2 with recovery backups; no history is reset.

Use the [Windows runbook](../docs/phase-4-runbook.md),
[Memory ADR 0005](docs/adr/0005-libraries-seeds-and-bundles.md) and
[root phase review](../docs/adr/0007-phase-four-libraries-characters-bundles.md).
Maintenance requires an exclusive canonical owner; stop chat first. Restore takes
an explicit complete bundle set and publishes a new local root with providers
disabled. It preserves logical character/library IDs and exact provenance, while
selective exports retain truthful partial archive coverage. No automatic personal
memory approval, new indexing, background extraction or voice feature is implied.


Phase 5 adds structured/FTS/CPU BGE/sqlite-vec/hybrid recall and disposable derived
index generations. Phase 6 general extraction is separate maintenance: `summary import`
preserves originals, `extract create/run/status` processes immutable source windows,
`extract review/apply` records exact operator decisions, and `extract seed` adopts actual
approved library knowledge. `index update` builds/replays the character's embeddings.
Use [the complete commands](../docs/phase-6-runbook.md) with the runtime stopped and a
model-pinned gateway alone. Schema upgrades retain backups and full source bundles can
resume processing jobs. No personal import was run during Phase 6 implementation.
