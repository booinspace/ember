# Phase 8 isolated development runbook

Phase 8 is in progress. This checkout must use its own data, runtime, configuration
and ports. Do not point it at the primary database or migrate that database while
ingest is running. Real model/audio tests remain deferred until the GPU is available.

## Setup and development checks

From the isolated repository, install the locked Python environment with uv sync
--locked. The root development environment includes the optional audio adapters.
For product installation, ember-agent[audio,local-deployment] supplies them.

Build the frontend explicitly before enabling the browser:

    cd ember-agent/frontend
    pnpm install --frozen-lockfile
    pnpm run check
    pnpm run test
    pnpm run build

Runtime startup does not run pnpm, download models, build indexes or repair state.
Install faster-whisper artifacts locally and configure audio.stt_model_directory.
Audio uses CPU int8, at most four configured threads, one owned native utterance
and a 30-second bound. Silero is CPU-only. Kokoro currently attaches to an installed
local OpenAI-compatible server with /v1/audio/voices and /v1/audio/speech.
For managed Kokoro, configure services.kokoro.mode = "managed", an installed
executable, its argument list and optional working_directory. Startup launches
that exact command without a shell after STT readiness, checks the configured
voice, and stops only its owned child. CUDA_VISIBLE_DEVICES=-1 and standard
Hugging Face offline guards apply to that child, not the gateway/ingest.
Configure the installed service itself for CPU operation. Attach mode verifies
an existing service and never stops it. These controls are not proof of actual
Kokoro synthesis/device use; the real audio acceptance gate remains open.

Run the Python gate with python scripts/check.py. Synthetic installed-server tests
use EMBER_LIVEKIT_EXE and temporary isolated roots/ports. Leave EMBER_REAL_CONFIG
unset while primary ingest owns the GPU.

## Browser and ordinary chat

Copy deployment.example.toml into a private isolated configuration. Supply actual
model pins, independent data/runtime roots and ports, installed LiveKit binary and
credentials. Enable browser.enabled. Enable audio only with installed providers.

After ingest finishes and GPU testing is permitted:

    .\.venv\Scripts\ember.exe start --config .local/config/deployment.phase-eight.toml

Open the URL in runtime/browser-activation.json. Its fragment contains activation
authority; keep that link private. The authenticated client receives a room
capability, not gateway/server credentials. Connect to chat. Recall and capture
controls are separate; changing sources approves no import and grants no browsing.

Disconnect to permit bounded native P3 capture. Connected silence never releases
realtime capacity. Reconnect reserves P0 and pauses new extraction steps while
the currently owned step drains. New jobs use dated native extraction; queued
legacy jobs keep their exact specifications. Runnable backlog continues in
bounded batches while disconnected. Failed checkpoints are shown in memory
status and skipped until an explicit retry, so later sources can still progress.
Missing background credentials block further idle attempts instead of spinning;
repair the isolated gateway/token configuration and reconnect or restart to resume.
Use Refresh memory status for aggregate capture/acceptance/index counts; it
does not start a per-turn status worker.

## Deep recall and calendar interpretation

Use the browser Recall deeply action, direct text /recall QUESTION, or the
spoken phrase recall deeply QUESTION after transcription. Inspection
separates bounded accepted memories from exact authorized observations. Answer
using this recall chooses qualified rows within existing prompt categories.
There is no additional planner/summary call or canonical belief write.
Inspection shows ordered event/state periods, modality and inclusion reasons.
Source links open already-authorized exact passages; another passage in the same
record is not treated as the cited evidence.

Deep reads currently defer to admitted foreground native jobs: an initial ungated
comparison failed strict ordinary regression limits. Deferred investigations
remain explicit, ephemeral outcomes and are never silently replayed.

Common requests such as memories from summer 2026 resolve locally on ordinary
recall. Strict event searches exclude undated records; no matches do not prove
that an event never occurred. Optional interpretation.enabled defaults off and
permits one bounded P0 call for unresolved ordinary calendar wording. It cannot
create event anchors or access, and does not affect the deep no-planner rule.

## Benchmark evidence

scripts/benchmark_phase_eight.py requires a new synthetic root and installed CPU
BGE assets. It measures at least 1,000 ordinary requests per disabled, idle and
attempted-active deep condition, plus parser timing. Aggregate reports retain
deferrals, failed gates and unmeasured conditions. They do not certify complete
Phase 8 performance or Qwen interpretation/voice latency.

See phase-8-validation.md and Root ADR 0015 for current evidence and open gates.

## Offline failed-checkpoint retry

Stop the isolated runtime before using offline memory maintenance: the runtime
owns the canonical writer. Do not use this development checkout against the
primary ingest database.

From the isolated repository in PowerShell, set the actual isolated character ID:

    $memory = ".\.venv\Scripts\ember-memory.exe"
    $root = ".local/data"
    $character = "YOUR-ISOLATED-CHARACTER-UUID"
    & $memory capture inspect --character $character --root $root

Inspect the reported checkpoint failure before retrying its original pinned job:

    & $memory capture retry --character $character --checkpoint "CHECKPOINT-KEY" --yes --root $root

Restart the isolated runtime; it drains runnable capture only after disconnection.
Retry does not approve imports, replace an extractor specification or erase failed
attempt history. Repeated invalid output can fail again and remains inspectable.

## Reviewed event-date enrichment

This workflow changes selected accepted character memories, not a library's
approved snapshot or an old extraction job. Stop the isolated runtime for offline
maintenance. Obtain exact memory/source IDs and revisions from authorized source
inspection; do not guess a date passage or substitute an import timestamp.

Create a private JSON input with an entries array, at most 64 items. Each entry
contains memory_id, expected_revision, source (the complete existing SourceRef),
support (expression, exact quote, completed/current_state status, and quote_start
when repeated), and timezone. An episode requires a supported completed event.
The passage must overlap accepted evidence and support its original source clock.
Unknown dates cannot be force-filled by this command.

    & $memory enrich propose-batch --character $character --input .local/date-input.json --output .local/date-review.json --root $root
    if ($LASTEXITCODE -ne 0) { throw "Date proposal creation failed." }
    notepad .local/date-review.json

The output contains version ember.temporal-batch-review@0.1.0 and reviews. Change each decision
from pending to approve or reject after checking its original passage, resolved
interval, precision, basis and expected_revision. Leave proposal fields unchanged.

    & $memory enrich apply-batch --character $character --input .local/date-review.json --yes --root $root

Each item reports applied, already_applied, pending, rejected or failed with a
private-safe code. Exit 2 means at least one failed; successful earlier items are
already durable. Re-run the same reviewed file to resume without duplicate
application. A concurrent revision reports revision_conflict and stays pending;
inspect the current memory and create a newly pinned proposal rather than editing
the old proposal to bypass CAS. Proposal output files are created exclusively;
use a new output filename for a new review.

Use the existing Phase 5 index-update workflow with the installed CPU encoder after
accepted metadata changes. Unchanged text vectors are reused. Startup does not
download embeddings or rebuild an index. A selected bundle retains enrichment
history only when its exact evidence closure is already included.
