# e-means review and AMB baseline at `6db7289`

## Where e-means could help

[Bessa et al., arXiv v1](https://arxiv.org/html/2611.09501v1#S3) use mini-batch exponential
moving averages of cluster sums and counts, cosine step decay, dead-cluster revival, and a
last-pass mean. Scaling laws set batch size and initial step from dataset size, cluster count,
and epoch budget. In the paper's GPU experiments, e-means matched the loss of 25 epochs of
Lloyd-style clustering in 1.4–2.6 epochs, about 10–45× faster in training. This is not end-to-end
index build speed, query latency, or answer accuracy. [Appendix L](https://arxiv.org/html/2611.09501v1#A12)
reports that e-means trained IVF indexes to similar or better nearest-neighbor recall on most
VIBE datasets at fixed scan selectivity, relative to Flash-KMeans. Vector-neighbor agreement is
not evidence relevance.

Ember uses scoped, exact sqlite-vec KNN on normalized BGE vectors and fuses up to 40 semantic
and 40 lexical candidates. There is no Lloyd training to replace. With the same vectors, scoring,
eligibility and candidate cap, IVF can match exact vector neighbors or miss them; better
centroids cannot improve on the exact neighbors. Clustering could improve *evidence* recall if
cheaper scans avoid semantic deadlines or buy enough time to fetch more distinct sources and
rerank them under the same context budget. It won't reduce BGE encoding cost, resolve aliases,
identify a superseded belief, or supply a second hop. The paper's training scale and GPU timings
do not qualify Ember's small per-user scopes or Apple Silicon.

| Benchmark | Higher-priority accuracy lever | Role for e-means |
| --- | --- | --- |
| LongMemEval-S | Time-aware queries, state updates, and evidence from multiple sessions | Test only if profiling finds a scan bottleneck; separate history indexing from query time |
| LoCoMo | Evidence-linked multi-hop retrieval; avoid spending context on near-duplicate chunks | Low priority at current per-conversation scale |
| PersonaMem `32k` | Resolve latest contextually relevant preferences and fit supporting evidence into the prompt | Low priority at this scope size |
| PrecisionMemBench | Filter by canonical identity, scope, state, and negative guidance; resolve fuzzy names to exact IDs | Behavior control, not a reason to deploy clustering |
| LifeBench and BEAM | Measure support/answer quality, time cutoff, and usable history before tuning context packing | Test larger scopes if scans, not BM25/FTS, encoding, or scope checks, dominate |

## Worktree baseline

- Worktree `/Users/operator/Documents/repos/ember-emeans`, HEAD
  `6db728969b8aa6b6246f4e24067683afd0dad7c4`. Locked Python 3.12.16 environment; verified that
  `ember_memory_engine` imports from this worktree. Existing `scripts.benchmark_memory_amb` ran
  unchanged. No Faiss candidate or production store was used.
- AMB `01bd83f3fa5245d6be3986cfab81fdd828f26acd`; PMB fixtures
  `b95d6abb471c0d591c440172283dd74e7af000df`. Apple M4, macOS 26.1 arm64, SQLite 3.53.1,
  sqlite-vec 0.1.9, ONNX Runtime 1.30.0. Archive uses pinned CPU BGE-small-en-v1.5 (`5c38ec7`),
  one encoder thread, default hybrid ranking unless noted, and up to 20 distinct public IDs
  subject to any lower PMB caller budget. No query-time cutoff; source timestamps preserved.
- Raw incremental per-split receipts: `.local/benchmarks/memory/head-6db7289-emeans/` (ignored).
  No hosted answer model or judge ran. Neither `GEMINI_API_KEY` nor `GOOGLE_API_KEY` was in the
  process environment. Ember's runner does not implement AMB's RAG or BEAM rubric stages.

### PrecisionMemBench `single-turn`

| Lane | Passes | Active passes | Mean precision | Mean recall | Retrieval p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Archive, default hybrid | 9/77 (11.7%) | 0/43 | 6.00% | 100% | 146.53 ms |
| Archive, `precision_v1` | 21/77 (27.3%) | 6/43 | 30.49% | 95.35% | 96.67 ms |
| Approved seeded beliefs | 63/77 (81.8%) | 30/43 | 76.89% | 86.05% | 3.28 ms |
| Approved beliefs + explicit scope | 71/77 (92.2%) | 37/43 | 86.05% | 86.05% | 4.06 ms |

Each arm had 71 complete requests, five skipped requests and one with no user store. Default
archive already found all required IDs in aggregate; extraneous IDs drive failures. One process
aborted with `recursive_mutex lock failed: Invalid argument` (exit 134) after both archive
receipts were written, before belief receipts. Both belief lanes completed on rerun. Belief
lanes get preapproved inputs, and explicit scope gets caller-supplied tags. Not matched ingestion
or timing comparisons; background load was not controlled. All four lanes reproduced every
case verdict and returned ID set from the separate same-commit run in the original checkout.

#### Why the default archive scores 9/77

The [published Hindsight artifact](https://github.com/vectorize-io/agent-memory-benchmark/blob/01bd83f3fa5245d6be3986cfab81fdd828f26acd/outputs/precisionmembench/hindsight/retrieval/single-turn.json.gz)
reports 66/77, 33/43 active, 84.81% mean precision and 100% mean recall. Same cases and AMB
scorer, different inputs to retrieval. Hindsight 0.10.0 used model-extracted identity and state
labels, caller scope and server-side exact/fuzzy tag filters. Ember's default lane indexed the
beliefs as source documents and received user isolation and a result cap, not belief-domain scope.
The scorer supplies persona, pins and open questions independently. Empty retrieval passes 34/77
but 0/43 active; total passes alone do not measure useful retrieval.

Default archive returned a median of 17 distinct IDs across 71 complete searches. All 68 failed
cases had unexpected IDs (1,040 unexpected-ID assertions total); two also surfaced forbidden
beliefs. No expected-ID misses. Default fuses an OR-based FTS search and fixed-width BGE KNN
without a semantic relevance floor. `precision_v1` removes query filler and adds a cosine floor;
unexpected-ID assertions dropped to 174, with two expected-ID misses. Offline rescoring of the
frozen default lists using only their first ID yielded 42/77 (31/43 active); this diagnoses noisy
result tails and is not a candidate run or a recommendation to force top-1.

Adding explicit scope to seeded beliefs fixed eight failures without losing a pass. The scoped
lane passed 10 cases Hindsight missed, missed five Hindsight passed, and shared one miss.
Scoped misses: five fuzzy/prefix cases and one case asking for Mongoose where the expected belief
favors the raw MongoDB driver. Resolve names within eligible scope and represent negative guidance
without treating a mere mention as identity. The seeded lane bypasses extraction and approval
coverage, so it does not show Ember can reproduce these scores from raw history.

### Other splits

The native archive adapter checks distinct labeled document IDs for LongMemEval-S, LoCoMo, and
PersonaMem, not question answers. LifeBench has no document relevance labels; BEAM IDs do not
identify relevant documents. Those two runs can report execution, coverage, and latency only.
A one-query smoke uses the full history of the selected isolation unit. BEAM defaults to `100k`;
`500k`, `1m`, and `10m` are separate scale tiers.

| Split | Smoke queries / split | Source rows / vectors | Evidence hit / recall / MRR | Coverage | Ingest + index | Retrieval |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| LongMemEval-S `s` | 1/500 | 53 / 433 | 100% / 100% / 0.143 | Complete | 74.97 s | 168.99 ms |
| LoCoMo `locomo10` | 1/1,540 | 19 / 119 | 100% / 100% / 1.000 | Complete | 21.93 s | 84.17 ms |
| PersonaMem `32k` | 1/589 | 5 / 92 | 100% / 100% / 1.000 | Complete | 18.80 s | 166.59 ms |
| BEAM `100k` | 1/400 | 6 / 480 | Not scored | Complete | 93.64 s | 260.33 ms |
| LifeBench `en` | 1/2,003 | 363 / 5,473 | Not scored | Partial | 419.75 s | 584.24 ms |

LifeBench smoke returned context but reported `semantic_deadline_or_admission_miss`.

| Full split | Queries (labeled) | Complete | Evidence hit / recall / MRR | Retrieval p95 |
| --- | ---: | ---: | ---: | ---: |
| LoCoMo `locomo10` | 1,540 (1,531) | 1,540 | 97.52% / 94.19% / 0.753 | 38.27 ms |
| PersonaMem `32k` | 589 (589) | 589 | 100% / 94.80% / 1.000 | 54.27 ms |

The batch was stopped during full BEAM `100k` before making retrieval code changes. Full LifeBench
and LongMemEval-S did not start. No full receipts for those three splits.

## Smallest useful follow-up

1. **Measure routing separately from ranking.** On frozen public or synthetic projections, compare
   native exact KNN, Lloyd-trained IVF, and e-means-trained IVF at matched thread, candidate and
   scan budgets. Pin the
   [Faiss implementation](https://github.com/facebookresearch/faiss/tree/dc4a261ff23ddf467dd94e7dd698af4d3ee9ebef/contrib/e_means)
   and seeds. Start with CPU `NumpyEmeans`, `spherical=True` and normalized FP32 vectors. Reassign
   vectors against final centroids; last-pass labels need not match them. Tune probes on held-out
   units. Record training, reassignment and publication separately from recall and query time.
2. **Test the quality mechanism.** Hold FTS, fusion, policies, result/context cap and queries
   fixed first. Record exact-neighbor agreement, document evidence hit/recall/MRR, per-case
   deltas, vectors scanned, coverage and deadline misses, and cold/warm p50/p95/p99. If scans
   get cheaper without hurting evidence, spend saved time on broader distinct-document candidates
   or reranking in a separate arm. For answer scores, reconcile history coverage and time cutoffs
   with pinned AMB runs, then hold case IDs, prompts, answer model, judge and token budget fixed.
3. **Keep scope and fallback exact.** Routing is disposable, per-generation derived data. Apply
   existing scope before top-k, lease the frozen generation, and recheck exact revisions, grants,
   and evidence at delivery. Do not share trained artifacts across unauthorized scopes. Keep exact
   KNN for small eligible sets and fallback, report partial results on deadline, and leave ordinary
   recall and canonical storage unchanged. Roll forward only if a larger public workload gains
   p95 headroom without lost evidence, leaked IDs, or more degraded requests. Phase 5
   capacity/contention gates remain open.

## Reproduce

Use the [pinned inputs and scorer overlay](memory-benchmark.md#reproduce-archive-ranking-comparison).
From the worktree root, with `AMB`, `ENCODER` and a fresh `RUN` directory set:

```bash
for dataset in longmemeval locomo personamem lifebench beam; do
  uv run --locked --offline python -X utf8 -m scripts.benchmark_memory_amb \
    --amb "$AMB" --encoder "$ENCODER" --dataset "$dataset" --mode hybrid \
    --query-limit 1 --output "$RUN/$dataset-smoke-1.json"
done
```

Omit `--query-limit 1` and use a new output filename for a full split. Use the runbook's PMB
commands for default hybrid and the two belief lanes. Use the pinned baseline commit above and its
runbook for the retired `precision_v1` experiment. Do not use single-run latency as a speed
comparison or use document ID metrics as a leaderboard answer score. The four offline
benchmark test files in `AGENTS.md` passed (13 tests).
