# Phase -1 — preparation while the export is pending

Status: scaffold implemented; actual export reconnaissance is blocked on the input.
No actual-export capability claim has been made.

Validated on 6 October 2026 with Python 3.12.15 and uv 0.12.23: both project
environments installed successfully; all 20 offline tests passed; the installed
configuration doctor and synthetic ZIP inspection ran successfully. The resulting
report excluded content sentinels and raw paths, matched the exact source hash,
and created no canonical or operational databases. The preserved design's SHA-256
matches the supplied attachment exactly.

Engineering standards were updated on 7 October 2026 to follow the current
`../PLAN.md`. Shared Ruff/strict Pyright tooling, Pydantic v2 preparation models,
pytest tests, and a prepared Windows/Linux CI gate now replace the original
untyped reports and unittest runner. See `engineering-standards.md`.

The updated local gate passes: all three environments synchronize with `--locked`,
44 Python files pass Ruff formatting, lint passes, strict Pyright reports zero errors
or warnings, and 61 pytest cases pass. These include explicitly marked synthetic
CLI end-to-end checks and regressions for nonfinite JSON numbers, oversized integer
timestamps, boolean timestamp handling, and safe Pydantic validation diagnostics.
Hosted CI has not run; no Git repository/remote was configured by this work.

The project was renamed to ember on 7 October 2026. Renamed distributions/imports,
both `ember` and `ember-agent` commands, and legacy inspection-report reads pass
the quality gate with 64 pytest cases. The code is prepared for the existing Git
origin; retained private sources and the original design attachment are unchanged.

## Delivered

- Two product roots following design section 28, with Python 3.12 packaging and
  public ABI / engine / importer / application ownership boundaries.
- Empty planned module directories, schemas, tests, benchmarks, and ADR locations.
- Original revised implementation plan preserved in `design-plan.md`.
- Offline ZIP inspection and safe report-writing CLI, with no models/services.
- Source namespace requirement, immutable read behavior, raw and logical inventory
  hashes, per-file content hashes, and no automatic account equivalence.
- Structural counts for graph nodes/branches/edge anomalies/current-path markers,
  content/roles/channels, metadata shapes, timestamp representations, attachment
  candidates, and archive/deletion marker types.
- Fixed vocabulary plus hashed unknown labels; no raw message content, external
  IDs, titles, reasoning, or paths in default reports.
- Explicit parser/resource limits, malformed/duplicate JSON detection, and coverage
  warnings for omitted/oversized/unknown source components.
- TOML checks for profile, configured roots, loopback origins, model identity pairing,
  initial prompt budget, optional-provider disablement, and disabled preemption.
- CLI readiness that distinguishes valid configuration from a working application,
  character, voice, verified model pin, or probed services.
- Synthetic fixture and meaningful safety/privacy/configuration/boundary tests.
- Isolated virtual environments and setup commands using uv; no new global runtime.
- Ignored local staging directories for incoming archives, reports, fixtures,
  libraries, characters, operational state, artifacts, and backups.

## Deferred with intent

| Phase | Work that must wait |
|---|---|
| 0 | Actual component inventory/capabilities, real-derived sanitized fixtures, concrete ambiguities |
| 1 | Pydantic contract models, serialization/IDs/evidence, normalized archive writer contracts |
| 2 | Canonical SQLite schemas/migrations, graph/revisions, bounded async facade |
| 3 | Actual importer, deterministic extraction, seed/restart/provenance, LiveKit slice, P0 gateway, model pin/baseline |
| 4+ | Libraries/grants/bundles, retrieval, general extraction, consolidation |
| 8+ | Full single-command composition/voice/browser lifecycle and optional delegation |

Package version `0.0.0` denotes this scaffold. Report format
`ember-memory.reconnaissance/0.0.1` is internal preparation tooling; it is not
Memory ABI 0.1, Normalized Archive 0.1, or Bundle 0.1. Their schemas are not frozen.

## Known limitations

- ZIP input only at present; deterministic directory inspection remains future work.
- Bounded JSON parsing loads at most one allowed JSON member at a time. The default
  64 MiB byte bound is not an exact Python-memory bound. Oversized JSON is still
  fully streamed/hashed, but structural parsing is deferred and reported.
- The inspector recognizes candidate component names/field shapes from synthetic
  cases. Real export formats may differ; this is why phase 0 is still required.
- Counts are observations. They do not identify actual edits/regenerations, resolve
  timestamps/timezones, normalize selected branches, grant access, or prove saved
  memories are included. No canonical IDs or databases have been allocated.
- Default reports omit content, but hashes/metadata can still be sensitive. Keep
  real reports local; optional raw member paths are explicitly sensitive.
- No Ollama/gateway/LiveKit/Kokoro/MCP/provider health was probed, and no model call
  was made. No tool capability is claimed just because a directory exists.

## Phase 0 entry conditions

1. Obtain the actual export archive and retain it verbatim.
2. Choose an explicit source/account namespace.
3. Run the inspector into ignored local storage and review its coverage warnings.
4. Inspect actual structural variants and create content-free/sanitized fixtures.
5. Record capabilities, ambiguity, identity/source-time assumptions, and unresolved
   attachments before designing the phase 1 archive contract.

The pending export blocks these empirical steps; it does not block this scaffold.
