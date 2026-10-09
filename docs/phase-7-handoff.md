# Phase 7 implementation handoff — paused 2026-10-09

This is a historical pause checkpoint. The user resumed the work; the implementation
and final validation are recorded in [Root ADR 0013](adr/0013-phase-seven-native-consolidation-and-profile.md)
and [Phase 7 validation](phase-7-validation.md). Process IDs and “still in progress”
statements below describe the checkpoint only; verify live runtime state before using them.

The user asked to pause quickly so a smaller coding model can continue. Phase 7 is
**implemented in progress, not declared complete**. Do not start Phase 8.

## Workspace and runtime

- Work in `C:/Users/operator/repos/ember-phase-seven`, a detached Git worktree at
  `9e205d0fc33e19bb0f3f13b7f9dff8930dd6d886`. The original checkout is
  `C:/Users/operator/repos/ember`; source and its production `.local/data` have not been
  changed/migrated. Do not install into its environments while ingestion is running.
- Use the worktree's own `.venv/Scripts/python.exe`, Ruff, Pyright, pytest and uv.
  Its editable installs resolve to this worktree. Environment cwd may still incorrectly
  point to `ai-runner`; set workdir explicitly. Workspace writes require tool escalation.
- Git changes may be staged automatically by the app. Inspect both staged and unstaged
  changes; new untracked files are also important. No commit or push was made.
- The original ingest was initially PID 33260 and was protected. The user subsequently
  stopped it and the gateway, then explicitly authorized restarting the gateway.
- The gateway now runs worktree code with the original non-MTP config/credentials at
  `http://127.0.0.1:11435`. Launcher PID 64364 (verify actual listener owner).
  Logs: worktree `.local/gateway.stdout.log` and `.local/gateway.stderr.log`.
  After unclean shutdown, the pinned model was unloaded with `ollama stop`, the newly
  started uncertain gateway was stopped, and startup used `--acknowledge-quiescence`.
  The gateway does not open the memory databases. Keep it running for the user.
- Only our remaining pytest/benchmark Python clients were stopped at this checkpoint.
  A currently admitted synthetic inference may still drain inside the gateway. Do not
  kill the gateway, delete locks/journals, or assume cancellation proves quiescence.

## Architecture already implemented

Read PLAN.md engineering standards, AGENTS.md, approved
`docs/phase-7-implementation-proposal.md` and Root ADR 0012 before continuing.

Memory ABI 0.4.0 / engine 0.5.0 / schema user_version 5 add:

- Optional bounded resolve/expand/validate contracts and temporal intervals, compact
  exact dependency certificates, profile pin purpose and capabilities.
- First-party native dialogue attribution (bound actor and stable conversation identity).
  New extractor 0.2.0 is separate from immutable legacy 0.1.2. Golden regression tests
  assert old prompt/schema/policy hashes, preserving ongoing imports.
- Conservative engine-owned native acceptance with audited accept/support/supersede/
  defer/reject receipts. Exact scoped equivalence, append-only support ledger, explicit
  corrections/world transitions, conflicts, confirmed consolidation and redirects.
  Imports remain explicit operator review and independent seeding.
- Durable suppression, exclusions, user binding and profile controls. Default selection
  covers accepted preferred name, pronouns, timezone and communication style. Imported
  profile facts require explicit binding to the seeded intended user entity.
- Existing one context/hybrid lookup includes canonical profile/manual pins. Core shares
  384 conservative serialized tokens, manual priority, six ordinary slots and 7000 input
  ceiling. At most 256 distinct memory/source refs / 64 KiB metadata across history;
  trim whole history turns. Batch checks at prepare/generation/visible text/tools/end,
  never metadata-only progress. No ordinary expansion/full manifest/source hydration.
- Backed-up additive migration and paged projection/supersession backfill; full/selective
  bundle metadata and native-policy closure; CLI profile/native/related/consolidate.
- Support-only acceptance avoids embedding/outbox rebuild. Repeat seeding now avoids
  outbox events when searchable content and grants did not change.

Relevant new engine files: `consolidation.py`, `claim_projection.py`, `profile.py`,
`related.py`; ABI `related.py`; CLI `phase_seven.py`. Existing ownership/workers remain.

## Performance findings and latest changes

The fixture is entirely synthetic: 100000 diverse accepted episodes + four profile
facts + a few deliberately reviewed foreign-source refresh beliefs. Real CPU BGE
assets are read-only at primary `.local/models/bge-small-en-v1.5`. Fixture root is
worktree `.local/phase-seven-benchmark`, marked `synthetic-benchmark.json`; reuse it.
Do not repeat the 12-minute embedding build unnecessarily or open production data.

- Shared stateless Pydantic canonical/timestamp adapters removed schema compilation on
  every validation. Empty manual pin lookup now starts from pins, not 100000 memories.
- Initial native-only targets passed, but adding one foreign-source belief exposed a
  1.69-second grant-scoped retrieval regression. It is fixed by a minimal disposable
  index projection: layout 0.5.0 / canonical headers 0.2.0 add `scope_until_us` and
  project existing grant eligibility during maintenance. Existing vec0 active/valid_until
  metadata enforces current grant scope before KNN top-k. Canonical selected-result
  checks still apply; no profile cache, graph service or per-turn corpus grant scan.
- Expiry/restoration/revocation regression tests cover semantic/lexical/hybrid modes.
  Index header refresh now also runs for grant_changed/grant_revoked/seed_grants.
- The scoped benchmark (`.local/phase-seven-performance-scoped.json`) measured profile
  warm p95 1.04 ms, enabled retrieval 120.57 ms and mixed foreign-source retrieval
  112.15 ms. Full synthetic prepare + 121-event delivery p95 was 155.06 ms; report
  this separately from recall. Every visible event validation is included.
- Concurrent index refresh still measured slow fallback (about 1.2–1.34 seconds).
  Latest unverified fix adds indexed recent ordering with forced point joins and permits
  certified old headers during monotonic new-memory/seed additions; restrictive changes
  still invalidate certification. Review this carefully for privacy and temporal safety.
  The final release benchmark was interrupted at pause; rerun before claiming a pass.
- Existing benchmark script records equivalent serial-vs-batch validation, public call/
  row/reference/byte counters, cold/warm/profile refresh and concurrent index copy.
  It now gates foreign-source warm performance too. No million-memory/platform
  certification is claimed. Cold encoder-load and index-copy costs must remain explicit.

Gateway requests optionally support version 0.3.0 to measure upstream prefill_seconds.
Health remains 0.2.0, legacy 0.1/0.2 requests omit this new field entirely, and legacy
extraction HTTP requests still use 0.2.0. This preserves old ingest compatibility.

## Verification and remaining work

Latest completed full suite (before the most recent index/prefill/origin changes):
**474 passed, 7 skipped** (107.62s), with explicitly installed real LiveKit binary.
Strict Pyright was clean and Ruff had one long SQL line, split at this checkpoint.

- Latest focused portability/grant-expiry + gateway prefill suite: **34 passed**.
- Phase 7 shared core tests cover unrelated profile + six slots, 100 progress frames
  without validations, mid-stream invalidation and bounded transitive history.
- Actual LiveKit room/profile-after-restart test passed (5.60s), using fake generation
  to avoid reserving the primary GPU. Real Qwen initial probe extracted and accepted
  four synthetic profile facts and streamed a reply; sanitized prior report is
  `docs/reports/phase-7-real-qwen.json` (ignored directory, not staged yet).
- The larger fixed three-query enabled/disabled Qwen test was running when paused.
  First attempt stopped on overly strict style wording (Qwen extracted exact "concise"
  instead of "concise replies"), corrected. Another failed because the user stopped
  the gateway; now restarted. Its latest run was interrupted, so rerun.
- Recent origin fix makes manual supersession retain the original seed origin plus
  additional origin dependency keys. Add explicit imported-supersession/suppression
  coverage; inspect selective bundle lineage. Newly added consolidated redirect /
  interrupted-transaction and recorded-history tests were interrupted before results.
- Need additional meaningful CLI, injection/budget, validity-boundary, migration
  old-supersession, relationship cycle/bounds and origin preservation checks as needed
  against all 17 Phase 7 AC. Avoid implementation-mirroring tests.
- Run uv sync --locked, Ruff format/check, strict Pyright, schema export --check and
  complete pytest after final changes. Seven skips are external integration gates,
  never equivalent to executed evidence.
- Run sanitized performance and Qwen evidence; fix remaining misses rather than
  publishing only native-only successes. Reports must not contain real user data.
- **ADRs/runbook/status are NOT yet updated**. Next root record is 0013; memory 0008;
  agent 0005. Link them through all three ADR indexes. Preserve historical proposal
  ADRs 0011/0012, add implementation links, update compatibility/AGENTS/current PLAN
  status and a Phase 7 runbook/evidence matrix. Preserve every AC, especially Phase 8.
- Existing reports under docs/reports are git-ignored. Explicitly stage only sanitized
  intended evidence. Scratch `.local-pyright.json`, `.local-ruff.txt`,
  `.local-bench-lint.txt` were removed; staged deletion/addition states need cleanup.
  TOML original newline styles were restored to keep dependency diffs small.
- Review all staged/unstaged/untracked changes, including new tests and benchmark script.
  Remain isolated until deployment is safe/reviewed; prepare an ignored Git patch or
  other concrete handoff after validation, rather than modifying a live editable install.

## Commands (worktree)

```powershell
Set-Location C:\Users\operator\repos\ember-phase-seven
.\.venv\Scripts\ruff.exe format .
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pyright.exe
.\.venv\Scripts\python.exe scripts/export_contract_schemas.py --check
$env:EMBER_LIVEKIT_EXE = "C:/Users/operator/repos/ember/.local/tools/livekit/1.13.9/livekit-server.exe"
.\.venv\Scripts\pytest.exe -q -o faulthandler_timeout=60
```

Benchmark (new output path each run):

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_phase_seven.py `
  --root .local/phase-seven-benchmark --records 100000 --samples 40 --reuse `
  --encoder C:/Users/operator/repos/ember/.local/models/bge-small-en-v1.5 `
  --output .local/phase-seven-performance-resumed.json
```

Real Qwen test requires background + foreground credentials. It never reserves a
realtime session and retries only known, unadmitted HTTP rejections:

```powershell
$env:EMBER_PHASE_SEVEN_GATEWAY = "http://127.0.0.1:11435"
$env:EMBER_PHASE_SEVEN_TOKEN_FILE = "C:/Users/operator/repos/ember/.local/runtime/non-mtp/gateway-background.token"
$env:EMBER_PHASE_SEVEN_FOREGROUND_TOKEN_FILE = "C:/Users/operator/repos/ember/.local/runtime/non-mtp/gateway.token"
$env:EMBER_PHASE_SEVEN_REAL_REPORT = "C:/Users/operator/repos/ember-phase-seven/.local/phase-seven-qwen-resumed.json"
.\.venv\Scripts\pytest.exe ember-agent/tests/test_phase_seven_real_model.py -q
```
