# Phase 8 application and isolated development runbook

Phase 8 is in progress. Isolated development uses separate data, runtime,
configuration and ports. Do not migrate a primary database while ingest is running.
Main-checkout launch is configured separately below; real generation/audio quality
acceptance remains distinct from startup verification.

## Main checkout: single-command launch

After installing the locked Python environment, building the frontend and configuring
browser.enabled = true with assets pointing to ember-agent/frontend/dist, run:

    .\start.ps1

The wrapper uses .local/config/deployment.non-mtp.toml and the root .venv executable.
It preserves that configuration's existing character binding, model pin and data root.
Paths passed to the wrapper resolve relative to the repository, including when the
calling shell is elsewhere. Use -Config .local/config/deployment.mtp.toml for a separately
configured profile, or -NoBrowser to suppress automatic desktop opening.

The wrapper calls ember start --open-browser. The Python runtime owns all service
startup/shutdown and opens the private activation URL after the browser server is ready.
A desktop launch failure leaves the runtime available with its private activation file;
the activation capability is never printed. Ctrl+C stops the runtime and its owned
children. Startup does not install packages, download speech models or build assets.
Voice requires explicit audio.enabled and installed Whisper/Kokoro providers.

A primary root opening can apply existing backed-up schema migrations; finish ingest
and stop other owners before using that root. Import extraction completion is separate
from review, approval and seeding; launch does not approve or seed candidates.

## Local voice setup and tested deployment

The primary non-MTP configuration on this device now enables browser and audio,
uses installed faster-whisper-small.en on CPU int8 with two threads, and starts
installed Kokoro-FastAPI on loopback as an owned CPU child with af_alloy. Run
.\start.ps1, click Connect, then Use microphone and allow your browser's microphone
permission. Text Send needs a connected room and a nonempty prompt.

For a fresh checkout, install/build the root/frontend first, then explicitly run:

    .\scripts\setup_speech.ps1 -Uv uv

The setup script pins Kokoro source at 61797914e5f8da0baf1d0b97d9d0b15c66625615
and Systran/faster-whisper-small.en at d1d751a5f8271d482d14ca55d9e2deeebbae577f.
It installs the provider into ignored .local/tools/kokoro-fastapi with its own CPU
uv environment, downloads verified model weights and the Whisper vocabulary,
and writes CPU/model/voice paths for that provider. It does not change a character,
approve candidates or configure a model pin. Configure the private deployment:

    [audio]
    enabled = true
    stt_model_directory = "../models/faster-whisper-small.en"
    stt_threads = 2
    language = "en"
    stt_device = "cpu"
    tts_voice = "af_alloy"

    [services.kokoro]
    endpoint = "http://127.0.0.1:8880"
    mode = "managed"
    executable = "../tools/kokoro-fastapi/.venv/Scripts/python.exe"
    arguments = ["-m", "uvicorn", "api.src.main:app", "--host", "127.0.0.1", "--port", "8880", "--workers", "1", "--log-level", "warning", "--no-access-log"]
    working_directory = "../tools/kokoro-fastapi"
    startup_timeout_seconds = 60.0

These relative paths assume the deployment file lives under .local/config.
The runtime child has CPU-only/offline guards, bounded OMP/MKL threads, UTF-8 I/O
and warning-only provider logs. Startup never invokes upstream start scripts, which
would install/download, and never rebuilds the production frontend.

Ember's explicitly hosted participant keeps the room alive across delayed joins and
browser disconnection. Signalling and control APIs stay on loopback. LiveKit selects
usable host-local media addresses; forcing its node IP to loopback failed browser ICE
on this Windows device while native clients passed. Authenticated encrypted media
listeners can use local network interfaces; control/credential access is not broadened.
Provider health accepts legacy voice strings and current voice objects by exact ID.
Doctor reports configuration only, separately from actual runtime readiness.

## Optional browser speech acceptance

Frontend unit tests remain pnpm run test. Optional pnpm run test:browser uses
installed Edge on Windows and installed Chromium elsewhere; setup installs a browser
explicitly if needed. It skips when EMBER_VOICE_FIXTURE_DIR is unset. The fixture
folder must contain a matching synthetic-character fixture.json, question.wav and
runtime/browser-activation.json from a running isolated deployment. Never select a
private character: the test checks the exact fixture ID and zero libraries before
sending input. It uses a synthetic microphone file and checks final user transcription,
Qwen's source-backed answer and increasing inbound audio RTP bytes. It records only
aggregate receipts. Physical microphone/headset testing remains separate.

The first real runs are recorded in docs/reports/phase-8-speech-browser-20261014.json.
They used one accepted synthetic color memory, the installed CPU providers and the
shared primary Qwen gateway. This is functional evidence and two latency samples,
not a distribution benchmark, hardware speech-quality gate or broad recall evaluation.

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


## Deep recall on a large memory set

Restart the installed editable runtime after pulling/applying Python changes:

```powershell
.\start.ps1
```

Stop the previous launch with Ctrl+C first, and refresh the browser after rebuilding its
assets. An investigation can remain partial while returning useful accepted memories and
original observations; partial is bounded coverage, not necessarily failure. The inspector
shows the specific exhausted resource limit if applicable. Answer selection is disabled
when no retrieved material fits the prompt. Strict event-time searches still exclude
undated memories, and cold uncached semantic vectors may use lexical retrieval.

For an explicitly authorized local performance check, make isolated SQLite backups of the
registered canonical stores and copies of their disposable indexes first. Do not point the
benchmark at the running live root. Store a JSON array of up to 32 queries in an ignored
private file; the aggregate output contains case numbers/counters rather than query text:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_deep_recall.py `
  --snapshot-root .local/deep-recall-investigation/data `
  --character $character `
  --encoder .local/models/bge-small-en-v1.5 `
  --queries .local/deep-recall-investigation/queries.json `
  --samples 3 --ordinary-samples 1000 `
  --output .local/deep-recall-investigation/new-report.json
```

Use the intended character's existing binding for $character. The benchmark requires a
new output path, normal exclusive snapshot ownership and installed CPU assets. It never
approves, seeds, extracts or generates an answer. Contention results distinguish deferred
deep requests from completed investigations; deferral is not a concurrent quality pass.
## Degraded browser text and controls

With the browser enabled, unavailable LiveKit during startup or initial room setup
selects browser text over the same character core and gateway. The UI reports voice
unavailability; Connect/Send use text and memory controls remain available. Replies
are buffered in this mode. If the pinned gateway cannot become ready, controls
remain available and Connect is disabled. Startup does not bypass the gateway or
install dependencies. The usual private activation link and origin/cookie controls
apply to both transports.

The text session renews a 45-second lease every 15 seconds. Closing/disconnecting or
letting a crashed/suspended browser expire cancels and drains its turn before P3
can resume. Only browser-acknowledged output enters retained history and the source
archive. Unacknowledged text is discarded; original input remains durable. Reconnect
after expiry rather than replaying an uncertain turn. An already established failed
room is not silently replayed into HTTP; a normal application restart can select
the degraded transport. No service restart was performed by the 2026-10-16 review.

## Ordinary word recall and configured context

Ordinary questions now search individual words in eligible accepted memories.
Matches in extracted values rank ahead of internal labels; relevant importance
then helps select values. The engine considers up to 100 lexical and 40 semantic
candidates within its existing soft deadline. The composer requests up to 100 rows
and fits whole values into a quarter of its configured input guard, capped at 6,144
conservative UTF-8 units. This is a bounded relevant subset, not a promise to include
every matching row. Exact quotes remain available through source inspection.

High importance does not make an imported episode an automatic profile field.
Profiles still require an eligible allowlisted fact attached to the explicitly
bound subject. Word retrieval does not rewrite identities, source approval or claims.

Check the chosen deployment file's `[model]` values when assessing context capacity.
The inspected current configuration still sets 8,192 context/7,000 input. For an
explicitly authorized 32k deployment, a compatible example is:

```toml
[model]
context_tokens = 32768
max_input_tokens = 28000
output_reserve_tokens = 4096
```

Keep the existing pinned model tag/digest and other deployment fields. Both runtime
and gateway must load the same settings through the normal owned restart. Changing
only a composer allowance cannot enlarge an already running model's context.
No live service was restarted by this correction. Existing FTS/vector generations
remain usable; no index rebuild or source re-extraction is required. See
[word-recall design](phase-8-word-recall.md) and [validation](phase-8-validation.md).

The operator subsequently authorized and activated these 32k settings on
2026-10-16. Normal LiveKit Connect, gateway/model context agreement and saved-name
text recall were verified; voice readiness reports ready. See the activation section
in validation for the exact scope. After a runtime restart, use its newly published
private activation link so the page refreshes its authentication and transport state.

## Reply details and composer controls

The requested UI build adds Thinking and Recalled memories expansions for the
current/latest reply. Thinking defaults off; the toggle affects the next observed
turn and confirms its setting before Send is enabled. Its output is ephemeral and
never saved into conversation or memory. The updated gateway and a sufficient
output reserve are required; the current 32k/4k-reserve config meets that bound.

The memory panel shows values actually selected for the reply, including selected
profile/pinned and deep values. It does not list all search candidates or raw source
quotes. Access changes invalidate it. The view resets for each new turn and does
not survive a runtime restart. Enter sends, Shift+Enter adds a newline, and IME/repeat
events do not submit. Sending scrolls down; reading older messages pauses following
of assistant updates.

The build passed synthetic backend/browser checks. Activation awaits new explicit
deployment approval after automatic review rejected reuse of the earlier 32k approval.
Reopen the new activation page after deployment.

### Experimental Phonon-2 STT attachment

Provision the pinned Fermion runtime and Phonon-2 weights separately. Start
`fermion serve phonon-2 --host 127.0.0.1 --port 8010 --threads 2` on the Windows CPU
backend. In the existing deployment's `[audio]` section set
`phonon_endpoint = "http://127.0.0.1:8010"` and `language = "en"`, then restart Ember.
The endpoint must report the Phonon-2 speech model. Ember sends only VAD-finalized
audio; LiveKit and Kokoro remain unchanged. Remove the endpoint setting and
restart to return to the configured Whisper model. Ember does not launch or
download the attached service. Keep it on loopback.
