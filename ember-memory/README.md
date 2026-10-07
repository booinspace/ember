# ember-memory

Owns durable source archives, canonical memory/provenance, grants/suppression,
retrieval, extraction, consolidation, seeds, and bundles.

The uv workspace contains separately packaged `memory-abi`, `memory-engine`, and
`memory-importers`; the workspace root packages the maintenance CLI from `cli/src`.
Phase 0a implements read-only nested-ZIP/directory reconnaissance, streamed
conversation statistics, and provisional typed source-adapter models. Public
Memory ABI/Normalized Archive 0.1.0 contracts are implemented experimentally;
SQLite storage and client implementations begin in phase 2. Empty planned
module folders mark ownership, without advertising an implementation.

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
