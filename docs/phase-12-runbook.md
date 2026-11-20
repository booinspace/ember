# Shared harness skills

Ember owns the operator catalog, enablement, context limits and broker receipts.
The selected harness owns procedure loading and execution. Hermes Desktop is not
required. OpenClaw metadata survives import/export; an OpenClaw runtime adapter is
not implemented.

## Browse and use

Open **Skills** in Ember's dashboard. Search available packages, inspect their
original Markdown, requirements, resources and package hash, then explicitly enable
reviewed packages. **Use in conversation** inserts the exact package identity into
the composer. For example:

- `Use shared/text-brief to summarize: The release is Friday; testing is still in progress.`
- `Use shared/page-summary to summarize https://docs.searxng.org/dev/search_api.html`
- `Summarize https://docs.searxng.org/dev/search_api.html` uses the same bounded
  public reader before a foreground reply, without waiting for a P2 worker.

Plain URLs and Markdown links are accepted for literal page reads, including
`Summarize [paper](https://arxiv.org/abs/1706.03762)`. Link labels are display text;
only the literal destination is fetched. Multiple separate URLs remain ambiguous.
On failure, expand **Read public page** for a bounded diagnostic such as
`page_http_429`, `page_fetch_timeout` or `page_fetch_failed`; remote bodies and
exception text are not exposed.

Tool/skill activity appears with the conversation and in the dashboard. Delegated
work retains its task, receipts, verified sources and bounded inference inspection.
At most eight compatible packages can be enabled. Enabled metadata is available to
conversational delegation; the whole installed catalog never enters ordinary prompts.
The model still chooses whether to delegate an implicit request; use the exact
`Use shared/...` form when selecting a particular procedure matters.

## Repository and installation

The PC's shared portable repository is
`/home/nathalie/.local/share/ember-skills` in Ubuntu WSL2. `packages/<name>/SKILL.md`
and accompanying resources are portable package data; `enabled.json` records exact
approved package hashes. SDK bundled/optional skills are discovered in place.
Nothing is downloaded, installed or silently resealed at Ember startup.

The dashboard imports/exports bounded portable JSON bundles. Import creates a new
shared package and never replaces an existing identity or enables it automatically.
The operator can also use the running instance's authenticated API through:

```text
ember skills --config <deployment.toml> list
ember skills --config <deployment.toml> inspect shared/text-brief
ember skills --config <deployment.toml> import <package-directory-or-json>
ember skills --config <deployment.toml> export shared/text-brief --output <new-file.json>
```

For a new deployment, explicitly install/verify the pinned Hermes SDK, copy the six
worker bridge files listed in `scripts/hermes/README.md`, create a dedicated shared
root with `packages/`, then seal with `hermes_setup.py --skills-root <absolute-root>`
plus the existing `--source`, `--python` and new `--output` profile arguments.
Set `[harness] skills = true`, the qualified command and receipt digest, and grant
only required broker IDs (the sample page reader needs `web.read_page`). Restart
Ember after changing deployment/profile configuration. Package enablement can change
live through the dashboard without a model reload.

## Boundaries and unavailable outcomes

Requirements describe dependencies; they do not grant tools. Shells, installers,
credential collection, preprocessing, scheduler changes and canonical-memory writes
are unavailable. Skills needing unsupported capabilities show explicit reasons.
Mandatory procedures above 8 KiB and resources above 8 KiB are unavailable rather
than truncated. Actual serialized P2 input remains guarded at 16 KiB; ordinary Core
input stays at 24 KiB despite the model's 128K context.

Package changes, disablement or provider loss invalidate copied task outputs,
receipts, artifacts and inspection. Refresh discovery after editing package data;
review and re-enable changed hashes explicitly. Imports preserve original Markdown,
provider extensions and resource bytes; they do not claim identical semantics on a
future harness.

Public reads accept literal user-supplied public HTTP(S) URLs only. No local/NAS
addresses, credentials, cookies, login flows, PDFs, browser automation or cloud
extraction fallback. DNS targets and every redirect are validated and connections
pinned. Pages return at most 1,536 UTF-8 bytes of extracted text (marked `partial`),
not full-article guarantees.

P2 tasks still wait for connected realtime voice sessions. Phase 13 owns scheduling
and physical model cancellation; pressing Stop speaking does not prove GPU abort.
EOF, cancellation and `wsl.exe` failure must purge the isolated task worker. Failed
qualification leaves the previous sealed profile available for rollback; never edit
its receipt or automatically accept modified bridge code.

## Starting the desktop manually

Start the installed Ollama app before running `.\start.ps1`. The current desktop
configuration attaches to Ollama; the launcher starts Ember's gateway and LiveKit,
but does not start the independent model runner. Gateway/voice startup messages do
not guarantee that Ollama will remain available for a later reply.

If a reply is rejected, the console now prints its HTTP status and a known gateway
code, and the inference activity card retains that code after cleanup. Error bodies,
prompts and credentials are not logged. Unknown or oversized error responses retain
the generic `gateway_rejected` code. Do not clear recovery journals or change model
pins based on that generic message alone.
