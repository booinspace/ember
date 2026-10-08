# Phase 6: saved memories and structured extraction

Phase 6 adds operator-run summary import, resumable Qwen extraction and exact-evidence
review/approval/seeding. Personal memories were **not** imported or extracted during
implementation. The original conversation ZIP and its existing private library are
unchanged. A source library, proposed candidates, approved library knowledge and a
character's accepted memories are different stages; starting Ember does not skip them.

Run PowerShell from `C:\Users\operator\repos\ember`. Stop `ember start`/`ember chat` before
maintenance: the canonical root has one process owner. Gateway-only operation can stay
running because it does not open the canonical root. Connected terminal/LiveKit runtime
sessions block background generation, including idle sessions. LiveKit startup reserves
realtime ownership and waits for a running background step to finish; no preemption is
claimed. Set a sufficient LiveKit startup timeout for that drain when needed (up to 120s).

## Setup and pinned settings

```powershell
uv sync --locked
uv sync --locked --project ember-memory --all-packages
uv sync --locked --project ember-agent --extra local-deployment
$env:PYTHONUTF8 = "1"
$memory = ".\ember-memory\.venv\Scripts\ember-memory.exe"
$configPath = ".local/config/deployment.non-mtp.toml"
$settings = & .\.venv\Scripts\python.exe -c "import json, sys; from pathlib import Path; from ember_agent.runtime.config import load_config; c=load_config(Path(sys.argv[1]), require_model_pin=True); print(json.dumps(dict(root=str(c.data_root), runtime=str(c.runtime_root), model=c.model_tag, digest=c.model_digest.removeprefix('sha256:'), gateway=c.services.gateway.endpoint, profile=c.active_profile, encoder=str(c.embedding_directory) if c.embedding_directory else None)))" $configPath | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Deployment settings are invalid." }
$memoryRoot = $settings.root
$backgroundTokenFile = Join-Path $settings.runtime "gateway-background.token"
```

These settings select the existing character/profile and model pin. Use the MTP config
instead only if that is the intended separate profile. Do not mix pins or data roots.
Setup installs dependencies; startup does not download anything. CPU BGE embeddings
must already be installed and configured; see the Phase 5 runbook if they are missing.

In a **separate terminal**, start only the existing gateway (Ollama must be running):

```powershell
.\ember-agent\.venv\Scripts\ember.exe gateway --config .\.local\config\deployment.non-mtp.toml
```

It creates scoped background credentials automatically. Do not print/copy the token.
The normal foreground token cannot submit extraction jobs. On a stale crash marker or
stale realtime reservation, check that the former runtime has stopped and upstream
work has drained before using the gateway's `--acknowledge-quiescence` recovery flag.
Ordinary idle gaps or client disconnection are not evidence of native cancellation.

## Import your saved-memory summary

Provide a UTF-8 text/Markdown file containing your actual saved memories. This adapter
preserves exact original bytes and text, including CRLF/BOM, as a private source.
Text/Markdown is limited to 8 MiB. It does not infer saved-memory inclusion from
conversations or extract them from the official ZIP.

```powershell
$summaryPath = "C:\path\to\saved-memories.md"
$import = & $memory summary import $summaryPath --source-namespace "chatgpt-saved-memories" --root $memoryRoot | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Summary import failed." }
$libraryId = $import.source_store_id
```

For JSON, the supported shapes are a top-level array of strings/objects or
`{"memories": [...]}`. Object entries need a text field (`text` by default) and may
have a stable nonempty string `id`. For example, **synthetic** data:

```json
{"memories": [{"id": "color", "text": "My favorite color is cobalt blue."}]}
```

Prefer one saved memory per JSON entry for explicit source boundaries. If your scraping
tool uses `content` instead of `text`, import it explicitly:

```powershell
$import = & $memory summary import "C:\path\to\memories.json" --format json --text-field content --source-namespace "chatgpt-saved-memories" --root $memoryRoot | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Summary import failed; check the JSON shape and text field." }
$libraryId = $import.source_store_id
```

JSON is limited to 64 MiB and 100,000 entries. Duplicate explicit IDs, empty entries,
unsupported shapes and reasoning-tagged text fail explicitly. Unsupported metadata
remains in the raw original; dates/authority are not guessed. Repeating an identical
import is a no-op. Changed entries with stable IDs append source revisions; id-less
entries use exact content plus position. Normalization commits batches of 128 and
resumes after interruption. Use a different namespace for another account.

Canonical data is `<root>/libraries/<libraryId>/memory.db`; the retained original is
under that library's `rawsource/`. Import does not run Qwen or compute embeddings.

## Create, run, pause and resume extraction

```powershell
$job = & $memory extract create --store $libraryId --key "saved-memories-v1" --source-kind summary --workload P4 --model $settings.model --digest $settings.digest --root $memoryRoot | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Job creation failed." }
$jobId = $job.definition.job_id
& $memory extract run --store $libraryId --job $jobId --gateway $settings.gateway --token-file $backgroundTokenFile --max-windows 5 --root $memoryRoot
if ($LASTEXITCODE -ne 0) { throw "Extraction failed; inspect status before retrying." }
& $memory extract status --store $libraryId --job $jobId --root $memoryRoot
```

The first small run lets you inspect cost/quality before processing the rest. It
checkpoints validated complete windows and pauses after five. Run the same job without
`--max-windows` to finish; it skips completed windows:

```powershell
& $memory extract run --store $libraryId --job $jobId --gateway $settings.gateway --token-file $backgroundTokenFile --root $memoryRoot
```

Use `--retry-failed` only when retrying a failed step; completed steps remain intact.
Ctrl+C pauses an interrupted job at its durable boundary; native gateway work drains
before its runner slot is reusable. If an earlier native request is still draining,
wait for gateway health to become ready before resuming. `extract cancel --store ...
--job ...` permanently cancels a job after the CLI root is available; already committed
candidates are retained. A new processing version/model/policy/source snapshot needs a
new `--key`. Reusing a key with different settings fails.

Jobs pin canonical source revisions, model tag/digest, prompt/output-schema/policy
hashes, extractor version and window settings. Each bounded window has a separate model
context. Extractor 0.1.2 requires `quote_start` as an explicit integer or null, asks for
a unique exact quote when possible, and validates repeated-quote offsets against source
text. Its discriminated schema also requires complete fact fields and null fact-only fields
for episodes/goals/plans. If changing from an earlier extractor contract, use a new
idempotency key; do not rewrite or bypass a failed evidence check. P3/P4
queues have eight pending slots each and alternate classes while preserving FIFO
within each class; only one upstream generation runs. Queued requests expire after
180 seconds. Queue expiry, unsupported output, missing quotes or changed hashes leave
safe failure codes and no completed checkpoint for the failed step. Status includes up to sixteen
failed-window ordinals/codes; stdout progress contains counts, never source/model text.

`complete` means every selected window was processed successfully. It does not certify
that a model found every durable claim or interpreted it correctly. Default windows
are at most 1,800 UTF-8 bytes with 64 codepoints of overlap and at most four proposed
claims per output. Review completeness and meaning yourself. Similarity and repeated
model runs are not independent corroboration.

Status and other CLI commands require exclusive root ownership, so use printed
progress while a long run owns the root; inspect status after a pause or completion.

## Review exact evidence and apply explicit decisions

```powershell
$reviewPath = ".local/saved-memories-review-001.json"
& $memory extract reevaluate --store $libraryId --job $jobId --root $memoryRoot
& $memory extract review --store $libraryId --job $jobId --output $reviewPath --root $memoryRoot
notepad $reviewPath
```

Read each candidate's statement, modality, original `quotes` and policy reasons. Change
**only** its `decision` from `pending` to `approve` or `reject`; leave uncertain entries
pending. Do not edit candidate payloads, IDs, hashes or quotes. Human corrections use
the existing `remember`/update flow with new exact source evidence, rather than changing
an immutable proposal. No extraction output carries approval or grants.

Summary-only candidates are capped at confidence 0.79, below the policy's 0.90 threshold.
Manual confirmation is retained separately as an explicit override; it does not pretend
the model acquired stronger source evidence. All eleven modalities are preserved.
Unsupported fact predicates remain proposals and cannot be written until supported;
generic durable observations use episode payloads. Conflicts, duplicate proposals,
uncertain branches, speculation and prior rejections stay visible for review. This
phase does not merge/supersede accepted memories or resolve temporal contradictions.

Review files contain private original quotes; keep them under `.local/`. They are new
files and never overwrite an existing export. Each page holds up to 1,000 candidates.
If `next_offset` is present, export another new file using `--offset N`; review/apply/
seed each page using a distinct seed key.

```powershell
& $memory extract apply --store $libraryId --input $reviewPath --yes --root $memoryRoot
if ($LASTEXITCODE -ne 0) { throw "Review application failed." }
```

Only entries you marked `approve` become approved library knowledge. Rejects persist
across new jobs. Pending entries remain unapproved. All selections are validated before
writes, but acceptance/review receipts commit per candidate, not as one whole-file
transaction; safely retry the same file after interruption. Rejection of previously
approved knowledge does not itself forget an existing belief; use the existing forget
flow for that. `reevaluate` appends changed policy decisions, preserving earlier inputs
and verdicts and never rewriting accepted beliefs.

## Seed the intended character, index and start chat

Read the existing deployment binding rather than creating/guessing a character:

```powershell
$bindingPath = Join-Path $settings.runtime ($settings.profile + "-character.json")
$characterId = (Get-Content -Raw $bindingPath | ConvertFrom-Json).character_id
& $memory extract seed --store $libraryId --input $reviewPath --character $characterId --key "saved-memories-seed-001" --yes --root $memoryRoot
if ($LASTEXITCODE -ne 0) { throw "Seeding failed; verify reviewed approvals and the binding." }
& $memory index update --store $characterId --encoder $settings.encoder --root $memoryRoot
if ($LASTEXITCODE -ne 0) { throw "Index update failed; verify installed BGE assets." }
```

If this deployment has never started, first start/stop it once to establish its character
binding, or explicitly create a character and configure the intended binding using the
existing Phase 3 workflow. `extract stores --root $memoryRoot` lists stores/namespaces.
Seeding verifies actual approved knowledge; an edited file cannot manufacture approval.
Independent character copies retain exact provenance and evidence-only grants; private
archive browsing is not granted. Index update creates a missing compatible generation
or incrementally reuses embeddings. Import/extraction alone do not create embeddings.

Stop the gateway-only terminal with Ctrl+C after extraction has drained, then start the
same configured runtime and join it from another terminal:

```powershell
.\ember-agent\.venv\Scripts\ember.exe start --config .\.local\config\deployment.non-mtp.toml
.\ember-agent\.venv\Scripts\ember.exe livekit-chat --config .\.local\config\deployment.non-mtp.toml
```

Ask about a memory you approved. Stop/restart and ask again to distinguish durable
recall from recent dialogue. Only the seeded character's eligible memories enter its
prompt; Qwen's model weights are not trained on the import. LiveKit remains text-only
until the planned Phase 8 browser/audio integration.

## Extract from the existing official ZIP library

The ZIP is already source history in a separate library. You do not need to import it
again. List stores, select its existing namespace/library ID, and create
a separate job using the same settings above and `--source-kind archive`. Summary and
conversation sources retain distinct authority. Run/review/apply/seed using that job's
IDs and new filenames/keys. Partial evidence-only bundles cannot serve as full source
processing libraries. Generic processing does not establish saved-memory inclusion
inside the official ZIP. Direct user evidence outranks assistant text, but all personal
adoption remains explicitly reviewed in this implementation.

Full source bundles retain jobs, candidates, immutable policy/review history and raw
originals; they can resume processing after restore with the same pinned specification.
Selective evidence bundles retain referenced processing/policy definitions and omit bulk jobs/review logs and have explicit partial coverage.
The first maintenance open upgrades schema 1/2/3 additively to schema 4, retaining a
consistent pre-upgrade backup beside each database. No canonical history is wiped.

## Validation and review

The root quality gate is `python scripts/check.py`. Real tests use synthetic temporary
sources/roots and need an authenticated ready gateway plus `EMBER_REAL_CONFIG`,
`EMBER_EMBEDDING_MODEL` and installed LiveKit via `EMBER_LIVEKIT_EXE`. Missing real-service
prerequisites are explicitly skipped. No test processes your personal library.

See [Root ADR 0009](adr/0009-phase-six-extraction-and-background-inference.md),
[Memory ADR 0007](../ember-memory/docs/adr/0007-extraction-jobs-and-summary-sources.md) and
[Agent ADR 0004](../ember-agent/docs/adr/0004-conservative-background-gateway.md).
Stop for architecture review before Phase 7. Phase 5's open performance/capacity gates
remain recorded separately and are not closed by these extraction tests.
