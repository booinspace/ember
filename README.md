# Local character stack — phase -1

Preparation for the Character Memory Platform and Local Character Stack design.
[PLAN.md](PLAN.md) is authoritative for all future tasks, including its engineering
language and code-quality standards. The actual ChatGPT export is pending.
Phase 0 has **not** been completed.

Two independently packaged project roots are staged here:

```text
character-memory/   public ABI, engine, importers, maintenance CLI
character-agent/    realtime application, gateway, tools, optional providers
docs/               supplied design, decisions, phase 0 runbook
scripts/            setup, offline checks, empty local staging preparation
.local/             ignored private sources, reports, state, and backups
```

The workspace initially had no Git repository. These are separate project roots
ready to become the two product repositories; no third product repository or
external worker checkout has been created.

## Working behavior now

- Python 3.12 packaging, uv workspaces/local dependencies, and CLI entry points.
- Read-only ZIP inventory, raw/member/logical SHA-256 fingerprints, bounded JSON
  shape inspection, conversation-graph observations, and explicit coverage gaps.
- Default reports contain counts/shapes/hashes, without message text, titles,
  external IDs, raw reasoning, or raw member filenames.
- Deployment TOML validation and truthful scaffold readiness reporting.
- Synthetic fixtures and offline tests for source safety, privacy, ambiguity,
  configuration, and architectural ownership.
- Planned package/module directories and independently isolated data/runtime roots.

Canonical memory models/storage, normalized import, retrieval, extraction,
generation, voice, UI, and service/process startup await their planned phases.

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
.\character-agent\.venv\Scripts\character-agent.exe doctor --config .\character-agent\config\deployment.example.toml
```

Copy the example to `deployment.local.toml` before adding private machine settings.
Model tag/digest remain pending. `--require-model-pin` makes a missing pin an error.
A configured pin is still unverified until phase 3 checks actual Ollama metadata.
`character-agent start` currently fails explicitly because the runtime is not built.

## When the export arrives

Keep the original ZIP unchanged in `.local/incoming/` or supply its existing path.
Then run:

```powershell
.\character-memory\.venv\Scripts\character-memory.exe inspect chatgpt .\.local\incoming\chatgpt-export.zip --source-namespace personal-chatgpt --output .\.local\reports\chatgpt-inspection.json
```

Inspection writes a **new** local report, refusing to overwrite the source or a
previous report. It does not import sources into canonical memory. Large JSON over
64 MiB is hashed and reported as deferred; completing phase 0 for such an export
requires a bounded streaming parser or an explicitly reviewed limit adjustment.

See [phase 0 runbook](docs/phase-0-runbook.md), [phase -1 status](docs/phase-minus-one.md),
and the [current plan](PLAN.md). The [original supplied design](docs/design-plan.md)
is retained as a historical snapshot and must not override PLAN.md.
