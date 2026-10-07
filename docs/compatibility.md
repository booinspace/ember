# Compatibility and release status

| Surface | Current version | Status |
|---|---|---|
| CLI/ABI/engine/importer/agent package artifacts | 0.0.0 | Local scaffold only |
| Preparation report/runtime Python models | Pydantic v2 | Typed internal preparation APIs; not Memory ABI |
| Reconnaissance report | ember-memory.reconnaissance/0.0.1 | Internal inspection format |
| Deployment TOML | config_version = 1 | Preparation format; no runtime guarantee |
| Memory ABI | Planned 0.1.x | Not implemented/frozen |
| Normalized Archive | Planned 0.1.x | Not implemented/frozen |
| Canonical SQLite schema | Pending phase 2 | No DB created |
| Bundle format | Planned 0.1.x | Not implemented/frozen |
| Gateway/runtime/provider contracts | Pending their phases | No supported transport yet |

Source-based local installs connect sibling project roots. uv lockfiles capture
dependency resolution for each root; they do not represent a published release.
Later releases version ABI/engine/importers independently and document migrations
and a compatibility matrix. Pre-1.0 changes do not authorize wiping history.

The ember rename changes component distributions/imports and console commands.
New inspection reports use `ember-memory.reconnaissance/0.0.1`; readers also accept
the earlier `character-memory.reconnaissance/0.0.1` identifier. Source hashes,
logical inventory hashes, configuration paths, and retained private sources are
unchanged by the rename.
