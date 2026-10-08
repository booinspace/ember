# ember-memory

Owns durable source archives, canonical memory/provenance, grants/suppression,
retrieval, extraction, consolidation, seeds, and bundles.

The uv workspace contains separately packaged `memory-abi`, `memory-engine`, and
`memory-importers`; the workspace root packages the maintenance CLI from `cli/src`.
Phase 0a implements read-only nested-ZIP/directory reconnaissance, streamed
conversation statistics, and provisional typed source-adapter models. Public
Memory ABI/Normalized Archive 0.1.0 contracts are implemented experimentally;
Phase 2 implements engine 0.1.0: local canonical SQLite storage, immutable
provenance/revisions, temporal primitives, owned sync/async clients and degraded
retrieval. See [the review](../docs/phase-2-canonical-engine.md) and
[Memory ADR 0003](docs/adr/0003-canonical-engine-and-workers.md). Remaining empty folders
mark later ownership without advertising an implementation.

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

Engine 0.2.0 now supports operator-reviewed immutable library approvals,
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
