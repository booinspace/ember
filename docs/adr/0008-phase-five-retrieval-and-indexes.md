# Root ADR 0008: Phase 5 retrieval and indexes

Date: 2026-10-08. Status: implementation verified; architecture review and full performance/capacity acceptance pending.

[PLAN](../../PLAN.md) governs the specification. [Memory ADR 0006](../../ember-memory/docs/adr/0006-derived-retrieval-and-generations.md)
owns backend details; [the runbook](../phase-5-runbook.md) describes setup and checks.

1. Desired behavior: graph-safe grouping, exact source chunks, full-text and CPU
   semantic recall, hybrid ranking, deep archive filters, diagnostics and rebuild.
2. Ownership: ember-memory owns all projections, policy checks and worker lifetimes.
   Each library has one shared archive index; each character has its own private
   belief/native-source index. Canonical history and character authority stay separate.
3. Existing abstractions: reuse ABI queries/results, graph/evidence, current-policy
   scope, grants/suppression, the atomic outbox, bounded workers and async facade.
4. Necessary primitives: an encoder/tokenizer Protocol, immutable generation catalog,
   builder/replayer and bounded CPU/native search pools. No vector service, new
   personality system or model-generated memory primitive is introduced.
5. Contracts: filters precede top-k; exact canonical revisions, dependencies and
   current policy are checked again before delivery. Never combine exclusive paths
   or truncate embeddings silently. A replacement generation must validate before
   publication. ABI 0.2.0 adds query modes/filters and native-context control; archive
   models remain 0.1.0. Engine/schema 0.3.0 add backed-up schema-1/2 upgrades containing
   canonical covering/expression indexes; canonical payloads and UUIDs are unchanged.
6. Failure: corrupt/missing/stale search data leaves canonical startup and provenance
   usable. Semantic timeouts return partial lexical/structured context while native
   work retains its bounded slot. Startup never downloads or rebuilds. Failed builds
   preserve the active generation; durable outbox events permit retry.
7. Acceptance: actual FTS5/sqlite-vec, pinned CPU BGE, scope before KNN, Unicode spans,
   graph separation, expiry/revocation/forgetting, stale/corrupt generations, failure
   recovery, vector reuse, background replay, worker isolation and schema upgrades.
   The existing restored synthetic seed also exercises a real LiveKit room and Qwen
   through the production P0 gateway, including indexing a newly entered memory.
8. Implementation/review: run the whole repository quality gate with the installed
   model and real local services. Record aggregate results below and in PLAN; stop
   before Phase 6 for operator architecture review.

## Coupling and tradeoffs

The application composition root optionally loads the CPU encoder and enables
coalesced replay of existing compatible indexes after committed writes. Character
logic continues to depend only on the public injected AsyncMemoryClient. Qwen,
Ollama scheduling and LiveKit do not own retrieval or embeddings. Runtime adds one
optional configuration path, not a new service or GPU consumer.

sqlite-vec and ONNX are concrete replaceable engine adapters. A separate derived
writer, one foreground KNN owner, one deep KNN owner and one query encoder have
bounded admission. Soft timeouts do not cancel extension code or detach jobs;
shutdown drains them before releasing store ownership.

Refresh copies the previous derived database before replaying affected records and
conversation neighborhoods. Unchanged vectors are reused. This preserves atomic
publication and concurrent readers, but copying remains O(index bytes), not
O(changed records). Keep two published generations plus leased/open native files;
prune again after final drain. Large libraries may need a measured future storage
change. No segment framework is added speculatively.

## Validation and limits

The final repository gate passed **360 tests** with actual BGE, LiveKit Server and
pinned Qwen through the authenticated production gateway. Ruff formatting/lint,
strict Pyright (zero errors), versioned schema checks and all three offline lockfile
checks passed. Windows test ports now probe TCP/UDP user ports outside the usual
sequential ephemeral range; synthetic listener cleanup covers setup failures. The aggregate-only
[benchmark report](../phase-5-benchmark.json) records 40 measured samples after five
warmups per operation, nearest-rank p95 without trimming, on Windows/Python 3.12.15
with the pinned CPU model. The fixture contains 100,000 accepted duplicate episode
beliefs and one native document. It stresses candidates/scope/redundancy but does
not represent 100,000 diverse personal memories. Query-vector caching is warm.

| Operation | Measured p95 | PLAN target | Disposition |
|---|---:|---:|---|
| Exact ID | 0.06 ms | 10 ms | Met on this fixture |
| Provenance | 2.56 ms | 10 ms | Met on this fixture |
| Structured | 1.41 ms | 15 ms | Met on this fixture |
| Scoped FTS candidates | 51.64 ms | 30 ms | **Not met** |
| Scoped vector scan, excluding encoding | 86.25 ms | 80 ms | **Not met** |
| Complete hybrid recall | 94.30 ms | Aim 50–100 ms; soft 150 ms | Met warm on this fixture |
| Common deep archive | 99.26 ms | 500 ms | Only one-document archive measured |
| Canonical startup, excluding model load | 502.89 ms | 2 seconds | Met on this fixture |

One partial response occurred across warmups/measurements; the first uncached
query can exceed its soft budget. Facade lexical/semantic timings are separate
from native-stage timings in the report. Larger 500,000-message / 250,000-chunk
capacity, small-mutation timings and Linux validation are not established.
Phase 5 is implemented and functionally tested, but **full performance/capacity
acceptance is open**. Do not call the entire phase/spec complete based on this
fixture or a smaller actual export. Performance targets have not been relaxed.

The official export has 18,761 visible source-part projections and 22,179 exact
chunks, with 50,520 canonical source records retained separately. The final derived file
is 245,608,448 bytes, and two generations are retained. One hundred sampled chunks
(181 source spans) were validated against original Unicode/UTF-8 evidence; maximum
stored embedding input length is 480 actual tokens. Indexing grants no access
and activates no personal claims. Phase 3 operator-reviewed real-claim adoption,
Phase 6 extraction/summary import, Phase 8 voice/browser integration, cross-platform
acceptance and ABI 1.0 remain their own gates.
