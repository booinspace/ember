# Phase 5 retrieval and indexes

Run from the Ember workspace in Windows PowerShell. SQLite is an embedded Python
library, not a separately running database server. The runtime owns its connections
and bounded workers while it runs; data and search generations persist on disk.

## Setup

```powershell
uv sync --locked
uv sync --locked --project ember-memory
uv sync --locked --project ember-agent --extra local-deployment
.\.venv\Scripts\python.exe scripts\setup_embeddings.py
```

The final command explicitly downloads and verifies pinned CPU BGE assets into
`.local/models/bge-small-en-v1.5`. Startup and queries never download models.
Your Ollama Qwen GGUF tag/digest remain configured separately; embeddings use CPU.

## Build and inspect

Stop the runtime before maintenance commands: one owner holds the canonical root.
Replace the root and UUID below with the existing deployment data root and desired
character/library ID. A character ID is shown at chat startup and retained in the
runtime profile's `*-character.json`. Build each source library once and each
character separately. Building a library grants no character access or belief.

```powershell
$memoryRoot = ".local/data"
$storeId = "YOUR-CHARACTER-OR-LIBRARY-UUID"
$encoder = ".local/models/bge-small-en-v1.5"
.\.venv\Scripts\ember-memory.exe index rebuild --root $memoryRoot --store $storeId --encoder $encoder
.\.venv\Scripts\ember-memory.exe index status --root $memoryRoot --store $storeId
.\.venv\Scripts\ember-memory.exe index update --root $memoryRoot --store $storeId --encoder $encoder
```

`update` replays pending changes and reuses unchanged embeddings. It performs a
rebuild if the index is missing, incompatible or corrupt. Failure retains the active
generation; repeat safely. No canonical IDs, history or import receipts are reset.
Canonical databases are `<root>/libraries/<UUID>/memory.db` and
`<root>/characters/<UUID>/memory.db`; the root registry is `<root>/registry.db`.
Derived generations and their catalog are under each store's `indexes/` directory.

## Enable runtime recall

Add to your deployment TOML, with the path relative to that TOML:

```toml
[search]
embedding_directory = "../models/bge-small-en-v1.5"
```

For a TOML in `.local/config`, this selects the installed assets above. Then use
existing `ember start`, `ember chat` or `ember livekit-chat` commands with that config.
Check `ember --help` for their existing launcher/service options. The runtime refreshes
already-built compatible indexes after committed writes, using bounded background
work. Missing indexes permit canonical recall but require explicit initial rebuild.
A missing encoder permits lexical fallback with partial coverage.

In chat, `/remember KEY TEXT` creates an explicit human memory through the existing
source/evidence authoring path. Ordinary dialogue remains source history; it does
not automatically become an accepted belief. Imported archive access still requires
explicit source grants; indexed private export text is not automatically recallable.
Full model extraction is Phase 6; voice/STT/TTS/browser integration is Phase 8.

## Query diagnostics

```powershell
.\.venv\Scripts\ember-memory.exe index search --root $memoryRoot --store $storeId --encoder $encoder --query "preferred hue"
.\.venv\Scripts\ember-memory.exe index search --root $memoryRoot --store $storeId --query "favorite color" --mode lexical
.\.venv\Scripts\ember-memory.exe index archive --root $memoryRoot --store $storeId --query "exact wording" --mode literal
```

Search/archive commands require a character ID; they enforce its current grants,
suppression and temporal scope. CLI stdout contains coverage/counts, not private
result text. Add `--output .local/my-new-results.json` to explicitly save a private
result/provenance file; existing files are never overwritten. ABI clients additionally
support predicate/entity/kind/modality filters and source/date/role/conversation/
branch archive filters. Literal mode verifies original canonical text.

`status` validates the generation hash and SQLite integrity. Coverage reports encoder
loss, admission/deadline misses, pending outbox, corrupt indexes and partial sources.
The 150 ms recall budget is soft: a timed-out native job retains its slot until it
finishes; subsequent searches can degrade instead of spawning unbounded work.

## Validate

```powershell
.\.venv\Scripts\python.exe scripts\check.py
$env:EMBER_EMBEDDING_MODEL = (Resolve-Path $encoder).Path
.\.venv\Scripts\python.exe -m pytest ember-memory/packages/memory-engine/tests/test_phase_five_search.py -q
```

Optional real room/gateway tests additionally need `EMBER_LIVEKIT_EXE` pointing to
installed LiveKit and `EMBER_REAL_CONFIG` pointing to your validated model-pinned
TOML, with its authenticated gateway ready. Tests use temporary synthetic roots,
not the personal library. Missing prerequisites are explicitly skipped.

A reproducible aggregate benchmark against an offline-owned indexed character:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_search.py --root $memoryRoot --character $storeId --encoder $encoder --samples 40 --output .local/my-new-search-report.json
```

It measures startup, complete retrieval, ID/provenance and separate scoped native
FTS/vector stages. Generation must be current. Five warmups precede each measurement;
p95 uses nearest rank with no outlier trimming. Semantic facade timings include cached
query encoding; the native vector scan explicitly excludes encoding. Partial-response
counts include warmups. Reports contain aggregate counts/timings, not source content.
See [Root ADR 0008](adr/0008-phase-five-retrieval-and-indexes.md) for measured results
and unfulfilled capacity/platform gates before treating Phase 5 as fully accepted.


Current validation: 360 tests passed with actual BGE/LiveKit/Qwen enabled; formatting,
lint, strict typing, generated schemas and offline lockfiles passed. Warm hybrid
recall was 94.3 ms p95 on the duplicate 100,000-belief stress fixture. FTS (51.6 ms)
and native vector scan (86.2 ms) missed their 30/80 ms targets. Larger archive scale
remains unverified. Review the linked ADR before advancing to Phase 6.
