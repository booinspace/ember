# ember — local character stack

Ember is a local character application with durable memory and realtime interaction.
[PLAN.md](PLAN.md) is authoritative, including engineering standards and phase order.
Phases 0–4 are implemented, Phase 5 retrieval/indexing is implemented with documented
performance/capacity gates, and Phase 6 adds structured extraction, saved-memory summary
import and conservative P3/P4 inference admission. The final Phase 6 gate passed 406
tests with real Qwen/BGE/LiveKit enabled. Stop for architecture review before Phase 7.

The official ChatGPT ZIP is private source history; starting Ember does not activate
personal claims. Import/extract/review/approve/seed/index using the
[Phase 6 PowerShell runbook](docs/phase-6-runbook.md). Personal Phase 6 imports/extraction
are left to the operator. [Root ADR 0009](docs/adr/0009-phase-six-extraction-and-background-inference.md)
records ownership, coupling, tests and limits. LiveKit remains text-only; voice/browser
integration is Phase 8. Saved-memory inclusion in the ZIP remains unestablished.

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
- Experimental Memory ABI 0.3.0 / Normalized Archive 0.1.0 with exact Unicode evidence,
  temporal/predicate semantics, typed client protocols, and reproducible schemas.
- Engine 0.4.0: immutable SQLite sources/beliefs, exact provenance, temporal
  queries, grants/suppression, and bounded sync/async clients.
- Deployment TOML validation and truthful scaffold readiness reporting.
- Synthetic fixtures and offline tests for source safety, privacy, ambiguity,
  configuration, and architectural ownership.
- Planned package/module directories and independently isolated data/runtime roots.

The runtime recalls accepted character memories using structured, lexical/semantic/hybrid
retrieval with CPU BGE and sqlite-vec. Phase 6 extraction proposes exact-evidence memories
through the existing gateway; explicit operator approval and seeding make them character
beliefs. Maintenance commands and the runtime take exclusive canonical root ownership.
See [Phase 5 retrieval](docs/phase-5-runbook.md),
[Phase 6 processing](docs/phase-6-runbook.md) and the [ADR index](docs/adr/README.md).

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
is configured for Windows and Linux CI. Service-dependent integration tests use temporary synthetic roots and run when their
explicit local prerequisites are provided; otherwise they skip. Offline tests need no models.
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
`ember start` hosts the configured managed LiveKit text runtime; use the same config
for `ember livekit-chat`. Startup attaches to Ollama and never downloads dependencies.

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

## Windows launcher

Run `uv sync --locked` and `.\scripts\setup_livekit.ps1` from the checkout.
Double-click `start.bat` to select a deployment from `.local\config`. Only
`deployment*.toml` files are listed. Stop Ember with Ctrl+C before switching.
See [Windows Strata setup](ember-agent/docs/strata-setup.md) or the
[Ollama setup](ember-agent/README.md#setup-and-running-from-windows-powershell)
to create a local deployment on a fresh clone. Startup never downloads models.

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
claim approval remains an operator review step. Phase 5 retrieval and Phase 6 processing extend this baseline. Stop for architecture
review before Phase 7.


Phase 6 adds `ember-memory summary import` and `extract create/run/status/cancel/
reevaluate/review/apply/seed`. [The operator runbook](docs/phase-6-runbook.md) contains
complete PowerShell settings/commands for your saved-memory text or documented JSON
format and for the existing official-export library. It starts gateway-only inference
while the canonical runtime is stopped, then reviews/seeds/indexes your chosen character.
Import and extraction are local operations and do not use a hosted model API.

## Current voice controls

The browser supports immediate reply text ahead of speech and session controls for
installed CPU Phonon voices and speaking speed. This independent amendment fits
alongside Phase 9; the earlier sections retain historical phase records. See
[voice controls and setup](docs/voice-controls-and-read-ahead.md).

Conversation continuity uses a [bounded session transcript](docs/conversation-continuity-amendment.md):
user input survives interrupted speech, recent dialogue stays within fixed prompt limits,
and first-message/literal topic questions can retrieve earlier session passages. Restarting
Ember begins a new session. The full conversation is never appended to every prompt.


The **Prompts** browser panel edits shared system rules and character-specific personality
as Markdown, with an effective instruction preview. Saves apply to the next message.
See the [prompt editing runbook](docs/personality-prompts-proposal.md#operator-use) for
file locations, budgets, migration and recovery.

Speech service settings use `[services.tts]`. The Phonon example defaults to Juniper
at 1.1× through `[audio]` settings; Kokoro uses the same adapter with its own voice.
See [TTS configuration](docs/tts-configuration.md) for defaults and legacy compatibility.

Native Phase 9 task foundations now include exact-action confirmation, durable
effect/attempt recovery, scoped model-selected tasks, validated artifacts and
idle speech notifications. See the [runbook](docs/phase-9-runbook.md) and
[validation](docs/phase-9-validation.md), including the opt-in gateway inference
benchmark. The optional [vLLM adapter and qualification](docs/vllm-implementation.md) retain
serial admission and drain-on-cancellation; native preemption remains Phase 13 work.

Direct MCP tools are available through **External tools** and the existing **Tasks**
panel. No external service is enabled by default. The [runbook](docs/phase-10-runbook.md)
describes installed-service configuration, exact write approvals and supported
bounds; the [validation record](docs/phase-10-validation.md) includes acceptance
evidence and gateway performance measurements.

## Managed OpenCode preview and independent services

Managed OpenCode **2.3.2** delegation is default-off. The preview uses the existing
**Tasks** surface and Ember's P2 gateway for one-shot `read`, `glob`, and `grep` tasks
in a dedicated synthetic, non-sensitive workspace. Keep prompts and files free of
private data and credentials. There is no OS sandbox or network egress enforcement.
A real macOS tool loop was observed against a deterministic upstream, not live Strata.
Windows reuses Job Objects for process ownership but is unqualified; Linux is also
unqualified. See [setup and acceptance limits](docs/adr/0025-managed-opencode-preview.md).

Hermes delegation, Hermes Desktop integration, and gateway-hosted coding memory
have been removed. No private data is migrated. The separate Strata coding
completions route remains available without Ember memory tools.
[Independent memory MCP](docs/opencode-memory.md) is unchanged: use it read-only
from a separately managed client only with verified local inference. It is not wired
into managed OpenCode.

Ember's independently configured public SearXNG search returns search-result snippets,
not live page contents. It works without a harness. The web voice UI still supports
**Stop speaking**. The **Skills** panel shows no active provider: live skill loading
and page reading are unavailable. Earlier Hermes and Phase 12 runbooks are historical.
