# Ember memory benchmark

Public benchmark results and synthetic diagnostics are separate. Synthetic retrieval scores are not
answer accuracy and do not go in leaderboard tables.

## Latest checkpoint: rank-first archive search on Miku Fedora

Paired serial replay, Fedora 45 under WSL2, Python 3.12.16, SQLite 3.53.1, one CPU BGE
thread. Both providers used `precision_v1`, identical frozen stores and requests, and the same
harness, encoder, limits, and 500 ms archive soft budget. Baseline is `d4ce01c` plus the precision
profile; candidate changes only `index_storage.py` and `indexed_retrieval.py` in that overlay.
Neither replay rebuilt the timed stores. Distinct and repeated passes use a warm process; the
repeated pass also benefits from the existing query-vector cache. These are native archive calls,
not ordinary recall, UI, or shared-runner latency.

| Replay order | Fixture / pass | Executed | Baseline p50 / p95 / p99 (ms) | Rank-first p50 / p95 / p99 (ms) |
| --- | --- | ---: | ---: | ---: |
| Baseline first | Synthetic / distinct | 121 | 181.21 / 192.64 / 196.49 | 147.87 / 152.45 / 154.83 |
| Baseline first | Synthetic / repeated | 121 | 173.58 / 184.34 / 192.71 | 140.41 / 143.74 / 147.70 |
| Baseline first | Public PMB / distinct | 69 | 12.06 / 30.60 / 43.88 | 12.06 / 24.68 / 35.69 |
| Baseline first | Public PMB / repeated | 71 | 6.62 / 19.40 / 23.42 | 6.68 / 13.47 / 14.78 |
| Candidate first | Synthetic / distinct | 121 | 183.51 / 195.94 / 203.98 | 146.28 / 149.86 / 153.06 |
| Candidate first | Synthetic / repeated | 121 | 175.35 / 185.06 / 187.68 | 140.23 / 143.50 / 144.70 |
| Candidate first | Public PMB / distinct | 69 | 11.89 / 29.90 / 41.41 | 12.06 / 24.36 / 35.58 |
| Candidate first | Public PMB / repeated | 71 | 6.58 / 18.21 / 21.96 | 6.77 / 14.26 / 15.07 |

PMB also executed two duplicate-text searches on each first pass (p95 7.13 vs. 7.40 ms
and 6.91 vs. 7.19 ms). Five empty/zero-budget cases and one case without a user store did
not execute on either pass. All 384 executed searches per variant completed per replay; no
partials, deadline misses, deferrals, or samples over 500 ms. Ordered IDs, exact provenance
(including chunk spans), and coverage matched per case in both directions and the profiled pair.
The synthetic ungranted canary remained isolated. This is speed parity on an unchanged result
set, not a new PMB quality score.

Separate profiled runs show where the gain came from:

| Fixture / pass | Projection payload rows | Canonical source payload rows | Evidence validations |
| --- | ---: | ---: | ---: |
| Synthetic / distinct | 10,570 → 600 | 10,570 → 600 | 4,656 → 516 |
| Synthetic / repeated | 10,570 → 600 | 10,570 → 600 | 4,656 → 516 |
| Public PMB / distinct | 554 → 473 | 554 → 473 | 293 → 243 |
| Public PMB / duplicate | 10 → 10 | 10 → 10 | 5 → 5 |
| Public PMB / repeated | 564 → 483 | 564 → 483 | 298 → 248 |

The synthetic gain came from eliminating 94.3% of each kind of payload read, not from faster
encoding or a different candidate window. Batched header/scope SQL cut distinct-pass calls
48,315 to 2,845, but its median per-query time rose from 1.58 to 14.14 ms. KNN still dominated
(114.26 vs. 116.73 ms median); FTS was 15.24 vs. 15.49 ms and encoding 6.93 vs. 6.79 ms.
If more archive speed is needed, profile pre-KNN scope at larger history/grant sizes before
changing rankers, candidate limits, or the encoder.

Qualification: synthetic tests cover exact revision/tie ordering, deduplication, denied scopes,
expiry and suppression with backfill, selection changes at delivery, linked export and new-root
restore with approval/grant/evidence parity, missing-index fallback, consumed corrupt payloads,
and republishing a prior generation. Lease tests cover pruning during late hydration and retention
by native work. The full offline root gate passed on macOS: 1,513 tests passed, 18 skipped;
Ruff format/lint, strict Pyright, generated schemas and the root lock check passed.
Certification never validates payloads it skips individually. Large reopened generations use eager
validation unless already certified; metered reads do not start cold hashing. No layout change,
index rebuild requirement, private-store change, or deployment. P5-AC-07/08 capacity/contention
and ordinary recall latency remain open. Separate synthetic bundle tests restore schema 4/5/6
into schema 7, check exact quotes, and preserve schema-6 recall selection. No private migration
was tested. Belief ranking and matched answer judging are separate gates.

### Replay the archive speed checkpoint

Use pinned inputs in the setup below, and prepare each snapshot once with
`python -m scripts.benchmark_memory_archive_speed prepare --root PATH --encoder PATH`.
For public PMB add `--amb PATH --fixtures PATH`. Keep each `fixture.json` and `store/` together
unchanged; each timed run uses a disposable copy. Run serially on one host, baseline then
candidate, then reverse. Use the same benchmark scripts on both checkouts; verify the venv
imports the intended engine instead of the editable install from another checkout.

```bash
# Run from each benchmark checkout, holding the same Fedora timing lock for the pair.
python -X utf8 -m scripts.benchmark_memory_archive_speed run \
  --root "$FIXTURE" --encoder "$ENCODER" --ranking precision_v1 --output "$NEW_REPORT"
```

Repeat separately with `--profile` for row, stage, and call counts; do not use instrumented
latency as the speed claim. Compare per-case `ids_sha256`, `provenance_sha256`, `coverage_sha256`,
status, and reasons only after verifying matching fixture/store/input hashes, encoder, harness,
host, runtime, and ranking. Keep skipped and duplicate cases separate from distinct timings.

Raw reports: `~/ember-benchmarks/20261123-fedora/results/idle-capture-wrapup/` and
`~/ember-benchmarks/20261123-fedora/results/archive-rank-first-reverse.IzigbY/` on Miku. Frozen
inputs and baseline code hashes: `~/ember-benchmarks/20261123-fedora/archive-speed-01/`.
Timed engine tree SHA-256: baseline
`111c76fbfa9dc8c2cbe3aaf403bb38c6af59e570a4c4a57bcecc3304b573b06d`, rank-first
`1e13313c6fffa44d1c65e5909658514018a3a89a3ebf516fffd49a48c45414e8`. Both receipts retain
base Git HEAD because the experiment used hashed overlays, not distinct Git commits.

A clean `8e4fcdd` replay of the same frozen inputs reproduced every archive case's ordered
IDs, exact provenance, and coverage: 242 synthetic and 142 executed PMB searches, no partials
or deadline misses. This follow-up checks parity through the bundle and identity changes; it
is not a new paired timing result. Receipts: `results/beliefs-committed.5VbftR/archive-*.json`.

### Archive delivery hardening: paired Miku replay

Compared `8e4fcdd` with the generation pinning and delivery deadline patch only. Same frozen
stores, harness and CPU encoder; serial baseline-first and candidate-first runs. All 384 executed
searches per arm per order matched ordered IDs, exact provenance and coverage. No partials,
deadline misses, deferrals or searches over 500 ms. Changes are correctness fixes, not a speed win.

| Order | Fixture | Distinct p95, before / after | Repeat p95, before / after |
| --- | --- | ---: | ---: |
| Baseline first | Synthetic | 149.61 / 149.41 ms | 144.33 / 142.76 ms |
| Candidate first | Synthetic | 148.47 / 148.37 ms | 141.86 / 142.37 ms |
| Baseline first | PMB | 25.40 / 25.13 ms | 13.38 / 13.64 ms |
| Candidate first | PMB | 24.79 / 24.03 ms | 14.14 / 13.46 ms |

PMB also executed two duplicate queries per first pass: p95 7.31 / 8.07 ms and 6.80 / 7.31 ms.
Receipts and patch hashes: `results/archive-hardening.jWeRQb/` on Miku. Engine tree SHA-256:
`6ad5088ecf12059525a12099bc3489eb9d3e26536ca6a83aa44920cb9c01df16` before,
`ddfe0212f7daefc2dc7c3dfee33086a4eb73478793b867ce63bf5adb521c4f23` after.
Local regression tests cover deadline overruns during exact reads on both delivery paths,
rejected native submission, and logically corrupt certified payloads above and below the delivery
limit. Offline gate: 1,525 passed, 18 skipped; Ruff, strict Pyright, schemas and root lock passed.
Same-path replacement during native reads and full hard read-budget accounting remain unqualified.

## Reproduce the archive ranking comparison

From the Ember repo root with Python 3.12 and uv, stage public inputs and the pinned CPU BGE
encoder once. These commands use Bash; on Windows you can reuse the staged paths with `uv`
from PowerShell. No deployment config, live service, private store, or model API key is needed.
The AMB checkout contains large public datasets; leave time and disk for setup.

```bash
uv sync --locked
uv run --locked python scripts/setup_embeddings.py
mkdir -p .local/benchmarks/memory/{pmb-input,results}
git init .local/benchmarks/memory/amb
git -C .local/benchmarks/memory/amb fetch --depth 1 https://github.com/vectorize-io/agent-memory-benchmark.git 01bd83f3fa5245d6be3986cfab81fdd828f26acd
git -C .local/benchmarks/memory/amb checkout --detach FETCH_HEAD
for fixture in beliefs.seed.json retrieval.cases.json; do
  curl -fsSL "https://raw.githubusercontent.com/tenurehq/precisionMemBench/b95d6abb471c0d591c440172283dd74e7af000df/fixtures/$fixture" \
    -o ".local/benchmarks/memory/pmb-input/$fixture"
done
uv run --locked --with tiktoken==0.12.0 --with google-genai==1.66.0 --with scipy==1.17.1 \
  python -X utf8 -m scripts.benchmark_memory_amb --help
```

The final setup command warms uv's separate scorer overlay; Rich is already in Ember's root
lock. Do not install AMB or change Ember's lockfiles. The runner checks the pinned AMB revision,
its scorer source, and both fixture hashes. Unset `AMB_PMB_RETURN_CAP` if set.

Run these commands sequentially on the same checkout and host; change output names for every
rerun. With no `--query-limit`, both use all 77 cases. `default` omits the extension; `precision_v1`
opts into engine ranking for that request only and requires `--mode hybrid`. Neither command
changes runtime defaults. Each JSON manifest records `mode` and `archive_ranking`.

```bash
uv run --locked --offline --with tiktoken==0.12.0 --with google-genai==1.66.0 --with scipy==1.17.1 \
  python -X utf8 -m scripts.benchmark_memory_amb --amb .local/benchmarks/memory/amb \
  --fixtures .local/benchmarks/memory/pmb-input --encoder .local/models/bge-small-en-v1.5 \
  --dataset precisionmembench --mode hybrid --archive-ranking default \
  --output .local/benchmarks/memory/results/pmb-baseline-001.json
uv run --locked --offline --with tiktoken==0.12.0 --with google-genai==1.66.0 --with scipy==1.17.1 \
  python -X utf8 -m scripts.benchmark_memory_amb --amb .local/benchmarks/memory/amb \
  --fixtures .local/benchmarks/memory/pmb-input --encoder .local/models/bge-small-en-v1.5 \
  --dataset precisionmembench --mode hybrid --archive-ranking precision_v1 \
  --output .local/benchmarks/memory/results/pmb-precision-v1-001.json
```

To reproduce the lexical control, repeat the baseline command with `--mode lexical` and
`--output .local/benchmarks/memory/results/pmb-lexical-001.json`.

Hold the encoder, result cap, case IDs, and fixture revision fixed. Compare total and active
passes, mean precision and recall, case-level misses and unexpected IDs, coverage, and retrieval
latency. Repeat the pair in reverse order if measuring latency. These are archive retrieval
assertions, not answer or realtime recall scores. Other AMB datasets can run without the scorer
overlay; `--dataset all` includes PrecisionMemBench and requires it.

Benchmark regression tests need neither the overlay nor model files:

```bash
uv run --locked --offline python -m pytest \
  ember-memory/tests/test_public_memory_benchmark.py ember-memory/tests/test_synthetic_memory_benchmark.py
```

## Run synthetic retrieval on Miku

From `C:\Users\operator\repos\ember` in PowerShell, with locked root dependencies and the existing
local CPU BGE assets installed:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_memory_synthetic.py `
  --root .local\benchmarks\memory\synthetic-001 `
  --encoder .local\models\bge-small-en-v1.5 `
  --revision (git rev-parse HEAD)
```

Use a new run root every time. For a paired archive comparison, run once with
`--archive-ranking default` and again with `--archive-ranking precision_v1`; keep the seed, encoder,
and result cap fixed. This flag does not affect the belief lane.

The script creates two fictional source libraries and two independent
characters. It uses exact archive grants for the archive lane, and approves and seeds exact-evidence
fixture memories via `LibraryService` for the belief lane. Retrieval uses Ember's character-bound
hybrid search; the synthetic corpus does not use model extraction or answer generation. This does
not touch a running Ember service or read its memory root.

`cases.json` stores scoring labels separately from ingested history. `report.json` records fixture
and encoder hashes, per-case outcomes, support recall/precision at the
requested result limit, coverage, first/repeated-pass search latency, import/seed/index time, store
size and cross-character isolation checks. First pass uses distinct queries; neither pass starts
with a cold OS cache. Evidence recall uses labeled sources, not correctness of any generated answer.
Unanswerable queries measure whether search still returns context, not whether a model abstains.
Historical/correction and alias cases are retrieval diagnostics, not proof of temporal inference or
deep recall. Beliefs in this fixture are separate dated utterances represented as episodes, so a
stale source appearing in context is diagnostic, not a typed correction-policy violation.

### First Miku baseline

Windows 11, AMD Ryzen 9 9950X3D2, Python 3.12.15, Ember
`d4ce01c79a23843510f127b2993784c5398d5e01`, one CPU BGE thread. Seed `29045` generated 352
source records and 120 queries (96 with evidence labels).

| Native hybrid lane | Mean evidence recall@5 | Mean evidence precision@5 | All evidence found | First p50 / p95 | Repeated p50 / p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Archive search | 62.5% | 15.35% | 48/96 | 414.6 / 426.0 ms | 407.4 / 419.0 ms |
| Seeded belief search | 87.5% | 20.0% | 72/96 | 21.9 / 34.3 ms | 17.7 / 27.4 ms |

Archive import/grants: 0.38 s; archive index build: 5.19 s; fixture approval/seeding: 3.43 s;
belief index rebuild: 1.67 s. Store: 21.09 MB. Both isolation canaries passed. Neither lane found
both alias-hop sources in any of 24 cases. Archive retrieval missed original history in all 24
historical cases. Seeded search returned previously held values in 19/24 current-value cases.
Both returned context for all 24 unanswerable queries. No degraded coverage reported. Archive
search is a separate path from ordinary recall; these numbers do not establish a realtime SLO.

Miku reports: `.local/benchmarks/memory/20261123-synthetic-baseline/report.json` and
`.local/benchmarks/memory/20261123-synthetic-full/report.json`. The second run reproduced quality
and isolation outcomes; first-pass p95 was 422.4 ms (archive) and 34.2 ms (belief). Its
`cases.json` records scoring labels.

## Miku Fedora chroot (WSL2)

Use Miku's `sudo -n /usr/local/sbin/enter-fedora` from the Ubuntu WSL2 distribution.
Keep the benchmark checkout, model assets, Python environment, stores, and results on the Fedora
filesystem, not `/mnt/c`. The isolated run lives under `~/ember-benchmarks/20261123-fedora`.
Use Python 3.12 and Ember's locked dependencies. Copy only the benchmark scripts, public inputs,
and CPU encoder assets from Windows. Do not reuse deployment configuration or private stores.

```bash
cd ~/ember-benchmarks/20261123-fedora/ember
.venv/bin/python scripts/benchmark_memory_synthetic.py \
  --root ../results/synthetic-rerun --encoder ../encoder --revision "$(git rev-parse HEAD)"
.venv/bin/python -X utf8 -m scripts.benchmark_memory_amb \
  --amb ../amb --encoder ../encoder --dataset longmemeval --mode hybrid \
  --query-limit 1 --output ../results/longmemeval-smoke-rerun.json

# PrecisionMemBench: add pinned scorer dependencies in a separate uv overlay.
UV_CACHE_DIR=../uv-cache ../tools/uv run --locked --offline \
  --with 'tiktoken==0.12.0' --with 'google-genai==1.66.0' \
  --with 'scipy==1.17.1' \
  python -X utf8 -m scripts.benchmark_memory_amb \
  --amb ../amb --fixtures ../pmb --encoder ../encoder --dataset precisionmembench \
  --mode hybrid --output ../results/precisionmembench-hybrid-rerun.json
```

Drop `--offline` only when first staging dependencies into the isolated uv cache.

Keep Windows and Fedora latency measurements separate. Running inside WSL2 does not qualify
bare-metal Fedora or production latency.

### Fedora results

Fedora 45, Python 3.12.16, same Ember revision and CPU encoder assets. Synthetic retrieval
reproduced Windows recall/precision and both isolation checks across 120 queries per lane.

| Synthetic hybrid lane | Evidence recall@5 | Evidence precision@5 | First p50 / p95 | Repeated p50 / p95 |
| --- | ---: | ---: | ---: | ---: |
| Archive | 62.5% | 15.35% | 301.2 / 311.0 ms | 311.1 / 327.6 ms |
| Seeded belief | 87.5% | 20.0% | 18.5 / 31.9 ms | 14.6 / 28.3 ms |

Archive import/grants and index: 0.25 s and 2.94 s. Belief approval/seeding and index: 1.72 s
and 1.54 s. Store: 21.12 MB. All 120 first-pass archive searches exceeded 150 ms; no belief
search did. Archive search is not ordinary foreground recall.

| PrecisionMemBench `single-turn` | Case passes | Mean precision | Mean recall | p50 / p95 |
| --- | ---: | ---: | ---: | ---: |
| Ember archive, hybrid | 9/77 (11.7%) | 6.00% | 100% | 31.6 / 42.0 ms |
| Ember archive, lexical | 14/77 (18.2%) | 18.80% | 95.35% | 11.9 / 23.1 ms |
| Published Hindsight | 66/77 (85.7%) | 84.81% | 100% | — |

All 77 case verdicts matched Windows for each Ember mode; ranked ID lists did not. Latency
covers 71 executed searches per mode; each ingested and indexed 35 documents in about 0.65 s.
The scorer flagged 1,040 unexpected-ID assertions in hybrid versus 373 in lexical. These counts
are individual assertions, not failed queries. Prioritize filtering irrelevant and superseded
beliefs before trying to increase recall.
Reports: `results/synthetic/report.json` and `results/precisionmembench-{hybrid,lexical}.json`
under the Fedora benchmark root.

One-query Fedora smokes index the full history for each selected scope, not the full split:

| Split | Source rows | Import + index | Retrieval | Result |
| --- | ---: | ---: | ---: | --- |
| LongMemEval-S | 53 | 24.9 s | 87.5 ms | Found labeled evidence |
| LoCoMo | 19 | 7.1 s | 58.4 ms | Found labeled evidence |
| PersonaMem | 5 | 5.5 s | 58.0 ms | Found labeled evidence |
| LifeBench | 363 | 295.5 s | 567.4 ms | Returned context with partial coverage (`semantic_deadline_or_admission_miss`) |
| BEAM 100K | 6 | 26.7 s | 75.3 ms | Returned context with complete coverage |

These smokes do not measure answer accuracy. LifeBench and BEAM have no usable document-level
relevance labels in the pinned exports.

### Fedora paired retest: archive precision profile

Same 77 public cases, pinned scorer, CPU BGE, result cap, and checkout across Ember modes.
`precision_v1` drops question filler from FTS and applies a 0.75 cosine-similarity floor to
vector candidates (converted to L2 distance for current indexes). Strong semantic-only matches
still qualify. The profile is request-scoped; no reindex, migration, or runtime default change.

| PrecisionMemBench `single-turn` | Passes | Active passes | Mean precision | Mean recall | Retrieval p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ember archive, default hybrid | 9/77 (11.7%) | 0/43 | 6.00% | 100% | 43.5 ms |
| Ember archive, default lexical | 14/77 (18.2%) | 3/43 | 18.80% | 95.35% | 25.0 ms |
| Ember archive, `precision_v1` | 21/77 (27.3%) | 6/43 | 30.49% | 95.35% | 30.0 ms |
| Hindsight, published | 66/77 (85.7%) | 33/43 | 84.81% | 100% | — |

`precision_v1` gained 12 passes over hybrid and seven over lexical. Unexpected-ID assertions fell
from 1,040 and 373 respectively to 174; forbidden-ID assertions stayed at two. The new profile is
still 45 passes behind Hindsight. Its latency is from one sequential run, not a production SLO.
Raw reports and code hashes: `~/ember-benchmarks/20261123-fedora/results/precision-v1-paired/`.
A clean `8e4fcdd` replay reproduced 21/77 (6/43 active), 30.49% precision and 95.35% recall.
All 71 executed requests completed. Receipt: `results/beliefs-committed.5VbftR/archive-precision-quality.json`.
This independently rebuilt replay checks score, not paired speed.

The paired synthetic rerun used the same fixture hash as the original Fedora baseline (120
queries per lane, 96 with evidence labels). Both isolation checks passed in both runs.

| Archive profile | Evidence recall@5 | Evidence precision@5 | First / repeated p95 | History hits | Stale evidence on current-value queries |
| --- | ---: | ---: | ---: | ---: | ---: |
| Default | 62.50% | 15.35% | 311.0 / 302.8 ms | 0/24 | 0/24 |
| `precision_v1` | 87.50% | 23.54% | 301.1 / 293.2 ms | 24/24 | 24/24 |

Direct and current-value recall stayed at 24/24 each. Neither profile found both alias sources
in any of 24 cases; both returned context on all 24 unanswerable queries. Seeded-belief quality
was unchanged (87.50% recall@5, 20.00% precision@5). Keep the profile opt-in: recovering history
also surfaced stale evidence on current-value queries. Test explicit temporal selection and typed
corrections next; these fixtures use dated episodes, not canonical state transitions.

## Approved-belief replay on Miku Fedora

Same pinned 77 PMB cases, AMB scorer and request limits. No encoder, extraction, or answer model.
The fixture adapter approved 35 public statements with exact evidence and seeded 29 independently
into character stores; six remained history-only. It excludes pins, relations, importance hints,
and scorer expectations. `seeded-belief` retains character and policy boundaries; `explicit-scope`
also narrows to approved origins corresponding to caller-supplied scope tags.

| Lane | Passes | Active passes | Mean precision | Mean recall | Retrieval p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ember seeded beliefs | 63/77 | 30/43 | 76.89% | 86.05% | 1.64 ms |
| Ember explicit scope | 71/77 | 37/43 | 86.05% | 86.05% | 1.63 ms |
| Hindsight, published | 66/77 | 33/43 | 84.81% | 100% | — |

Both Ember lanes completed all 71 executed requests; five skipped on empty/zero budget and one
had no user store. A clean replay of `8e4fcdd` reproduced all 77 ID sets, verdicts, and coverage
in each lane. Compared with the wrap-up overlay, order changed on seven seeded and five scoped
cases. Independently rebuilt stores do not share recording times or UUIDs used to break ties.
Scoped misses: five fuzzy/prefix cases and one negative-guidance case. Published Hindsight is a
same-case retrieval reference, not a matched ingestion, answer, or latency result. The scoped
lane received extra filters. Raw archive quality remains the separate 21/77 result above.

Run once per lane on the same host, without `--encoder`:

```bash
uv run --locked --offline --with tiktoken==0.12.0 --with google-genai==1.66.0 --with scipy==1.17.1 \
  python -X utf8 -m scripts.benchmark_memory_amb --dataset precisionmembench \
  --amb "$AMB" --fixtures "$PMB" --lane seeded-belief --output "$NEW_REPORT"
# Repeat with --lane explicit-scope and a new output path.
```

Receipts on Miku: `~/ember-benchmarks/20261123-fedora/results/beliefs-committed.5VbftR/`
(clean `8e4fcdd`) and `~/ember-benchmarks/20261123-fedora/results/beliefs-wrapup.UN9F16/`
(overlay; its checkout field retains base HEAD). Verified SHA-256 for replay engine files:

| Engine file | SHA-256 |
| --- | --- |
| `identity_recall.py` | `25b1a85e1b22932628a2d331e6f7bb2d4ccae4c4258bd2f6d4f95c38d02b7254` |
| `retrieval.py` | `04ef1ceb5cafb8746588c33b836be0e71897077d6da1256a104215be6e0e3f7d` |
| `character.py` | `62790a24584eeee2e92c6b898e04eb80e2513531ea8c224eeb18deaf05172a48` |

[Hindsight](https://arxiv.org/abs/2512.12818) separates facts, experiences, summaries, and evolving
beliefs. Its [`search/tag_resolution.py` at `065b164`](https://github.com/vectorize-io/hindsight/blob/065b1642dce1d8ffb5d3e87d5092ac173038b191/hindsight-api-slim/hindsight_api/engine/search/tag_resolution.py)
resolves fuzzy names to exact vocabulary entries before filtering. That could address the five
name misses without relaxing canonical identity matching or granting source access.
[LongMemEval](https://arxiv.org/abs/2410.10813) evaluates fact-enriched index keys and time-aware
query expansion. Test those on versioned derived data only, with original quotes and canonical
scope unchanged. Neither technique was added in this pass; the negative-guidance miss is separate.

## Public reference protocol

Use the [Agent Memory Benchmark](https://github.com/vectorize-io/agent-memory-benchmark) split and
scoring mode from each pinned reference artifact. Do not compare public retrieval diagnostics to
answer scores. PrecisionMemBench's single-turn split is the exception: its score is the share of
cases passing required/forbidden retrieval-ID assertions. The upstream session benchmark is a
separate result.

| Dataset | Split | Reference scoring | Published Hindsight |
| --- | --- | --- | ---: |
| LongMemEval | `s` | RAG answer score | 94.6% (473/500) |
| LoCoMo | `locomo10` | RAG answer score | 92.0% (1,417/1,540) |
| PersonaMem | `32k` | RAG answer score | 86.6% (510/589) |
| PrecisionMemBench | `single-turn` | Retrieval assertion pass rate | 85.7% (66/77) |
| LifeBench | `en` | RAG answer score | 71.5% (1,433/2,003) |
| BEAM | `100k`, `500k`, `1m`, `10m` | Single-query mean rubric score | 73.37%, 71.1%, 73.9%, 64.1% |

Source: [pinned AMB results manifest](https://github.com/vectorize-io/agent-memory-benchmark/blob/01bd83f3fa5245d6be3986cfab81fdd828f26acd/results-manifest.json).
BEAM denominators in tier order: 400, 700, 700, 200.

Ember has no answer scores for these splits yet. Published answer artifacts name
`gemini:gemini-3.1-pro-preview` and `gemini:gemini-2.5-flash-lite`. Before matched runs, verify
model access, prompts, score aggregation, and context budget against each artifact. No hosted
inference ran. A local-model run needs its own label and same-model no-memory and BM25 controls.

### PrecisionMemBench: Miku Windows, native archive

Same 77 case IDs and pinned AMB scorer as Hindsight. Different retrieval paths: Ember uses scoped
archive search; Hindsight uses model-extracted identity/currency labels and server-side tag filters.

| System | Case passes | Active passes | Mean precision | Mean recall |
| --- | ---: | ---: | ---: | ---: |
| Ember archive, hybrid | 9/77 (11.7%) | 0/43 | 5.96% | 100% |
| Ember archive, lexical | 14/77 (18.2%) | 3/43 | 18.73% | 95.35% |
| Hindsight published | 66/77 (85.7%) | 33/43 | 84.81% | 100% |

Ember gaps: −74.0 percentage points (hybrid), −67.5 (lexical). High recall did not translate to
passes: extraneous IDs count against precision. Hybrid archive ingestion/indexing took 1.12 s
for 35 documents. Retrieval p50/p95: 55.5/71.1 ms hybrid and 29.1/52.6 ms lexical for 71 executed
searches per run. Five cases skipped search on empty/zero-budget input; one had no stored user
unit. These are archive-provider baselines, not accepted-belief or deep investigation scores.

Miku reports: `.local/benchmarks/memory/20261123-precisionmembench-hybrid.json` and
`.local/benchmarks/memory/20261123-precisionmembench-lexical.json`.

Run from repo root. Use detached AMB commit `01bd83f3fa5245d6be3986cfab81fdd828f26acd`.
For PrecisionMemBench, stage `fixtures/beliefs.seed.json` and `fixtures/retrieval.cases.json` from
[Tenure commit `b95d6abb`](https://github.com/tenurehq/precisionMemBench/tree/b95d6abb471c0d591c440172283dd74e7af000df/fixtures)
in a separate directory. The runner verifies both hashes. AMB scorer imports require AMB runner
dependencies; on Miku the missing packages were installed in a disposable benchmark venv, not
Ember's `.venv`. No Gemini calls or keys required for this retrieval path.

```powershell
uv run --locked --offline --with tiktoken==0.12.0 --with google-genai==1.66.0 --with scipy==1.17.1 `
  python -X utf8 -m scripts.benchmark_memory_amb `
  --amb .local\benchmarks\memory\amb --fixtures .local\benchmarks\memory\pmb-input `
  --encoder .local\models\bge-small-en-v1.5 --mode hybrid `
  --dataset precisionmembench --output .local\benchmarks\memory\precision-001.json
```

Use `--dataset all --query-limit 2` for a retrieval smoke run. This still indexes full histories
for selected scopes; allow extra time for LongMemEval-S and LifeBench. Use `--dataset beam --split`
with `100k`, `500k`, `1m`, or `10m` to select a scale tier. Keep results separate from answer
benchmarks: this path imports public AMB documents, not reconstructed turns; preserves timestamps
but does not enforce query-time cutoffs; and runs no answer model or judge.

First Miku Windows hybrid smoke results: LoCoMo document recall 60% on 5 queries, PersonaMem
100% on 5, and LongMemEval-S 100% on 1 (53 documents indexed). BEAM 100K completed 1 retrieval
(149.7 ms), with no graded answer or usable evidence label. These small samples verify execution,
not quality across the splits.

Pinned artifacts report 11,303 ingested LongMemEval-S documents versus 23,867 export rows,
and 2,877 LifeBench documents versus 3,605 export rows. Reconcile actual history coverage before
calling answer runs matched.

Record case IDs, usable history, model IDs, prompt versions and context budgets before comparing
answer results. For BEAM, use the pinned rubric score, not the fraction of binary `correct` rows.
The 100K site headline (75.0%) differs from the public raw result (73.37% mean rubric score at AMB
commit `01bd83f3fa5245d6be3986cfab81fdd828f26acd`). Publish artifact-based numbers with source links.
