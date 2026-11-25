# Ember

Ember is a local-first character application with realtime text and voice, durable
memory backed by exact source evidence, and one gateway for local model inference.
Chat in the browser or terminal, choose what a character can remember, and inspect
the evidence behind its recall.

## What works today

- **Text and voice.** The browser supports LiveKit text and voice, with HTTP text
  and controls when LiveKit is unavailable. It shows reply text ahead of speech and
  lets you change installed voices and speaking speed. Terminal chat is available
  with or without LiveKit. [Browser setup](docs/phase-8-runbook.md#browser-and-ordinary-chat)
  and [speech providers and Kokoro blends](docs/tts-configuration.md)
- **Memory and recall.** Add or correct memories directly, or enable native capture
  with source controls and reviewable checkpoints. Characters recall accepted
  memories across restarts. Ordinary recall uses structured and lexical retrieval,
  with optional local semantic indexing. Deep recall can follow temporal and
  associative links and show exact evidence. Natural-language deep routing and
  model-assisted calendar interpretation are separate opt-ins.
  [Memory controls and deep recall](docs/phase-8-runbook.md)
- **Import and portability.** Import a ChatGPT export or a saved-memory summary,
  extract candidate memories locally, and review exact evidence before approving
  and seeding claims. Imports begin as source history. Characters have independent
  seeds and scoped library access. Linked backups and selective bundles support
  restoration to a new root. [Import and review](docs/phase-6-runbook.md)
  and [backups and bundles](docs/phase-4-runbook.md)
- **Character and tasks.** Edit shared instructions and character-specific
  personality in the Prompts panel. Native tasks support exact-action confirmation,
  durable receipts, validated artifacts and idle speech notifications. Configure
  direct MCP separately for scoped tools with explicit write approval.
  [Prompt editing](docs/personality-prompts-proposal.md#operator-use),
  [tasks](docs/phase-9-runbook.md), and [direct MCP](docs/phase-10-runbook.md).
- **Local inference.** Ollama is the default. vLLM and managed Strata are selectable
  through the same gateway. Conservative scheduling verifies runner release before
  admitting more work. Live preemption stays disabled. Active coding can delay
  voice. [vLLM](docs/vllm-implementation.md), [Strata](ember-agent/docs/strata-setup.md),
  and [scheduling validation](docs/phase-13-validation.md).

## Optional integrations and previews

- **Managed OpenCode 2.3.2** is off by default and intended for synthetic,
  non-sensitive workspaces. Inspect offers `read`, `glob` and `grep` without OS
  isolation. Separately enabled Build edits and runs commands in an offline Linux
  container with no host mounts. The tested tool loops used a deterministic
  upstream. Live Strata delegation and production rollout remain unqualified.
  [Inspect](docs/adr/0025-managed-opencode-preview.md)
  and [Build](docs/adr/0026-managed-opencode-build.md).
- **Public web retrieval** can use keyless Exa MCP, keyed Exa or Parallel for page
  excerpts. SearXNG returns snippets. Conversation can use configured retrieval
  directly. The separate Web Research worker is opt-in, and Inspect and Build
  have no web grant. Live provider qualification is still open.
  [Provider setup and limits](docs/adr/0027-public-web-retrieval.md)
- **Coding and memory clients** can use Strata's optional coding completions route
  or the independent memory MCP listener. Use the listener read-only with
  separately managed clients and verified local inference. Coding completions
  and managed OpenCode have no access to memory MCP. Live skill loading remains unavailable.
  [Coding screenshots](ember-agent/docs/strata-vision.md)
  and [independent memory MCP](docs/opencode-memory.md).

Ember is pre-1.0. The Memory ABI is experimental (`0.5.0`, engine `0.7.0`).
Reviewed real-export adoption through recall and restore, broader recall quality
and scale, and supported-platform release checks remain open. Browser media has
been exercised with synthetic audio. Physical and LAN playback still have open
checks. [Release criteria](PLAN.md#abi-freeze-and-v1-release-acceptance-criteria)
and [browser validation](docs/phase-8-validation.md).

## Get started on Windows

Install Python 3.12 and [uv](https://docs.astral.sh/uv/). For this setup, start
Ollama locally and install a model before launching Ember. Setup installs
dependencies explicitly. Ember startup uses installed services and models.

From the checkout in PowerShell, install the workspace and LiveKit Server, then
create a private profile. Run the copy step for a new profile only.

```powershell
uv sync --locked
.\scripts\setup_livekit.ps1
New-Item -ItemType Directory -Force .local\config | Out-Null
if (-not (Test-Path .local\config\deployment.local.toml)) {
    Copy-Item ember-agent\config\deployment.example.toml .local\config\deployment.local.toml
}
(Invoke-RestMethod 'http://127.0.0.1:11434/api/tags').models | Select-Object name,digest
```

In `.local/config/deployment.local.toml`, set `model.tag` and
`model.expected_digest` to the installed model's name and full digest. Set
`services.livekit.mode = "managed"`. Paths resolve relative to the TOML file.
Change the copied `../../.local/` prefixes to `../`. Keep machine settings,
memory, and credentials under ignored `.local/`.

```powershell
uv run --locked ember doctor --config .local/config/deployment.local.toml
.\start.bat
```

`doctor` checks configuration offline. Select the profile in `start.bat`, then
join it from a second terminal.

```powershell
uv run --locked ember livekit-chat --config .local/config/deployment.local.toml
```

Try `/remember favorite-color My favorite color is cobalt blue.` Restart the
runtime, reconnect and ask your favorite color to verify durable recall.

For the browser, install Node.js and pnpm, then build the frontend from the checkout.

```powershell
pnpm --dir ember-agent/frontend install --frozen-lockfile
pnpm --dir ember-agent/frontend build
```

Set `browser.enabled = true` and
`browser.assets = "../../ember-agent/frontend/dist"` in the local profile. After
stopping the previous launch, run:

```powershell
.\start.ps1 -Config .local/config/deployment.local.toml -OpenBrowser
```

Enable audio after installing speech providers. For LAN access, configure the
host's real LAN address and the intended trusted proxy in the private profile.
[Voice setup](docs/phase-8-runbook.md#local-voice-setup-and-tested-deployment)
and [LAN HTTPS](docs/lan-https-setup.md).

## Import a ChatGPT export

With the runtime stopped, import into a private canonical data root.

```powershell
uv run --locked ember-memory import chatgpt-zip 'C:\path\to\OpenAI-export.zip' `
  --source-namespace 'chatgpt-export' --root '.local\data'
```

The import preserves the original ZIP and reports normalized coverage.
Follow the [review workflow](docs/phase-6-runbook.md) to extract and seed selected
claims. Saved-memory coverage depends on the records actually present in the export.

## Repository and development

- [ember-memory](ember-memory/) owns the typed public ABI, canonical engine,
  importers and the `ember-memory` maintenance CLI.
- [ember-agent](ember-agent/) owns the character runtime, browser, model gateway
  and provider adapters. Its commands are `ember` and `ember-agent`.
- The root owns the shared uv workspace and quality gate. After `uv sync --locked`,
  run `uv run --locked python scripts/check.py` for Ruff, strict Pyright and pytest.
  Service-dependent checks use explicit local prerequisites.

[PLAN.md](PLAN.md) owns requirements and acceptance criteria. The
[architecture decisions](docs/adr/README.md) record design choices and evidence
for each phase.
