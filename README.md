# ember — local character stack

ember is a local character application with durable memory and realtime interaction.
[PLAN.md](PLAN.md) is authoritative for all future tasks, including its engineering
language and code-quality standards. The official ChatGPT export has been inspected
and imported into a private library. Phase 0a preparation, supported phase 0b export
handling, phase 1 contracts and phase 2 canonical engine are implemented. Phase 3
importer and P0/runtime work have passed the repository quality gate; operator-reviewed
personal claim adoption stays open. [PLAN section 11](PLAN.md) records source findings,
implementation, measured costs and open gates. Read
[Root ADR 0006](docs/adr/0006-phase-three-source-and-runtime.md) before continuing
phase 3 or starting phase 4.

Two independently packaged project roots are staged here:

```text
ember-memory/       public ABI, engine, importers, maintenance CLI
ember-agent/        realtime application, gateway, tools, optional providers
docs/               supplied design, decisions, phase 0 runbook
scripts/            setup, offline checks, empty local staging preparation
.local/             ignored private sources, reports, state, and backups
```

The Git workspace contains both independently packaged component roots, ready
to become the two product repositories. Shared development tooling and docs live
at the workspace root.

The main command is `ember`; memory maintenance uses `ember-memory`.
`ember-agent` is also available as a component command alias. From the workspace
root after setup, run `uv run --locked ember --help` or
`uv run --locked ember-memory --help`.

Import a ChatGPT ZIP as a private source library with an explicit account scope:

```powershell
uv run --locked ember-memory import chatgpt-zip 'C:\path\to\OpenAI-export.zip' `
  --source-namespace 'chatgpt-export' --root '.local\data'
```

The import preserves the original ZIP and reports normalized coverage. It does not
create or activate character memories; saved-memory inclusion is reported only when
the archive establishes it.

## Working behavior now

- Python 3.12 packaging, uv workspaces/local dependencies, and CLI entry points.
- Read-only ZIP/directory inventory, bounded nested containers and numbered shards,
  raw/member/logical SHA-256 fingerprints, and streamed conversation observations.
- Offline `ember-memory import chatgpt-zip` with exact raw preservation, metadata-only
  replay checkpoints, bounded SQLite batches, graph/branch preservation and explicit
  hidden-reasoning/attachment coverage. Imported dialogue does not activate beliefs.
- Provisional typed ChatGPT source models preserving all graph branches, unknown
  metadata and absent/null distinctions; separate sanitized report models.
- Default reports contain counts/shapes/hashes, without message text, titles,
  external IDs, raw reasoning, or raw member filenames.
- Experimental Memory ABI / Normalized Archive 0.1.0 with exact Unicode evidence,
  temporal/predicate semantics, typed client protocols, and reproducible schemas.
- Engine 0.1.0: immutable SQLite sources/beliefs, exact provenance, temporal
  queries, grants/suppression, and bounded sync/async clients.
- Deployment TOML validation and truthful scaffold readiness reporting.
- Synthetic fixtures and offline tests for source safety, privacy, ambiguity,
  configuration, and architectural ownership.
- Planned package/module directories and independently isolated data/runtime roots.

The completed phase 2 baseline has canonical fallback retrieval with truthful
degraded coverage. Phase 3 adds JSON/manual-memory, P0/text and LiveKit work plus
the verified official ZIP importer. One operator-reviewed real-source memory
adoption check remains open. Indexed retrieval, general extraction and complete
voice/UI/startup await their planned phases. Review
[phase 2 architecture](docs/phase-2-canonical-engine.md) and
[Memory ADR 0003](ember-memory/docs/adr/0003-canonical-engine-and-workers.md).
Stop for phase 3 architecture review before phase 4.

## Setup (PowerShell)

Use installed Python 3.12 and uv. Setup installs packaging dependencies into local
virtual environments; it does not download models or start runtime services.

```powershell
.\scripts\bootstrap.ps1
```

When uv or Python 3.12 are not on PATH, supply their existing executable paths:

```powershell
.\scripts\bootstrap.ps1 -Uv 'C:\path\to\uv.exe' -Python 'C:\path\to\python.exe'
```

The bootstrap uses `uv sync --locked` to create a root development environment and
separate product `.venv` environments, installs all memory workspace packages and
the agent's `local-deployment` extra, and runs the complete quality gate. The root
`pyproject.toml` contains shared development tooling, not a third product package.
Each environment has its own `uv.lock`. The agent's editable sibling paths are local
development wiring; published pinned package installation comes in later phases.

Run installed quality tools without downloading packages, and prepare local staging:

```powershell
.\.venv\Scripts\python.exe .\scripts\check.py
.\.venv\Scripts\python.exe .\scripts\prepare_local.py
```

The quality gate checks Ruff formatting/lint, strict Pyright production typing, and
pytest (including explicitly marked synthetic CLI end-to-end tests). The same gate
is configured for Windows and Linux CI. Service-dependent integration tests will be
added when those components exist; the current suite requires no models or services.
See [engineering standards](docs/engineering-standards.md) for exact commands.
Local preparation creates empty staging directories without databases, character
identities, grants, or seeds.

## Check configuration

```powershell
.\ember-agent\.venv\Scripts\ember.exe doctor --config .\ember-agent\config\deployment.example.toml
```

Copy the example to `deployment.local.toml` before adding private machine settings.
Both supplied Q4_K_M tags and full digests have local private configs in
`.local/config/deployment.non-mtp.toml` and `.local/config/deployment.mtp.toml`.
Use either with `--require-model-pin`. See [model profiles](docs/model-profiles.md).
A stored inventory/configured pin still needs a fresh phase 3 runtime verification.
`ember start` currently fails explicitly because the runtime is not built.

## When the export arrives

Keep the original ZIP unchanged in `.local/incoming/` or supply its existing path.
Then run:

```powershell
.\ember-memory\.venv\Scripts\ember-memory.exe inspect chatgpt .\.local\incoming\chatgpt-export.zip --source-namespace personal-chatgpt --output .\.local\reports\chatgpt-inspection.json
```

Inspection writes a **new** local report, refusing to overwrite the source or a
previous report. It does not import sources into canonical memory. Conversation
JSON streams one bounded graph at a time; other JSON over 64 MiB is hashed and
deferred. Directories are accepted too, with report output outside the source tree.
See [public-format research](docs/phase-0-research.md) and the
[provisional source/archive model](docs/phase-0-source-model.md).

See [phase 0 runbook](docs/phase-0-runbook.md), [phase -1 status](docs/phase-minus-one.md),
and the [current plan](PLAN.md). The [original supplied design](docs/design-plan.md)
is retained as a historical snapshot and must not override PLAN.md.

## Architecture decisions

Start with [the root ADR index](docs/adr/README.md) for implemented phases and
integration. [Memory ADRs](ember-memory/docs/adr/README.md) own memory detail;
[agent ADRs](ember-agent/docs/adr/README.md) own runtime/gateway decisions, including
those still planned. Numbers are local to each tree. PLAN remains authoritative;
indexes link decisions to actual phase status and acceptance evidence.

## Phase 3 LiveKit text runtime

Run the explicit dependency setup once: `./scripts/setup_livekit.ps1`. Existing
private machine configs now enable the installed managed LiveKit Server. Startup
does not download dependencies. From the workspace root, start the text runtime:

```powershell
.\ember-agent\.venv\Scripts\ember.exe start --config .\.local\config\deployment.non-mtp.toml
```

In another PowerShell window, join the same configured character:

```powershell
.\ember-agent\.venv\Scripts\ember.exe livekit-chat --config .\.local\config\deployment.non-mtp.toml
```

Use `/remember KEY TEXT`, ask about the memory, then stop and restart the runtime
and reconnect before asking again to verify persistence beyond conversation history.
The client reports recalled-memory counts. Ctrl+C in the runtime drains the room
and stops only launcher-owned services. Ollama and authenticated attached services
remain running. MTP/non-MTP are alternative profiles with independent current
character bindings. Voice/browser remain future integration; real imported claim
review/adoption remains the open Phase 3 gate. See
[the agent runbook](ember-agent/README.md) and
[Agent ADR 0003](ember-agent/docs/adr/0003-managed-livekit-text-runtime.md).


Phase 4 adds reviewed library snapshots, independent character seeds, current
source policy, linked backups and selective exact-evidence portability. See the
[Windows maintenance runbook](docs/phase-4-runbook.md) and
[architecture review](docs/adr/0007-phase-four-libraries-characters-bundles.md).
Bundle/restore operations are exclusive offline maintenance; restored providers
remain disabled until explicitly configured and started. Phase 3's real archive
claim approval remains an operator review step. Stop for review before Phase 5.
