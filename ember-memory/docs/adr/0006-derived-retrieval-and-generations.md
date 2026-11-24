# Memory ADR 0006: derived retrieval and generations

Date: 2026-10-08. Status: implementation verified; architecture review and full performance/capacity acceptance pending.
[Root ADR 0008](../../../docs/adr/0008-phase-five-retrieval-and-indexes.md) owns Phase 5
integration and acceptance. Canonical state never becomes owned by a search database.

## Projection and embedding contracts

Use pinned local CPU ONNX BAAI/bge-small-en-v1.5, revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, 384-dimensional float32 CLS pooling,
L2 normalization and a 512-token limit. Query instructions apply only to queries.
Verify model/tokenizer hashes before loading. Setup is explicit; startup never
acquires assets. No torch, CUDA, Qwen inference or external vector service is needed.
The encoder Protocol permits replacement without changing public memory contracts.
See [BGE's model card](https://huggingface.co/BAAI/bge-small-en-v1.5).

Resolved direct selected-path adjacency permits grouping within ten minutes, or
bounded topic continuation within twenty. Ambiguous and alternative paths remain
isolated. Split at paragraphs/sentences before exact token offsets: target 400,
maximum 480 content tokens, overlap 64; count the final attribution template and
special tokens. Every quote has original Unicode/UTF-8 coordinates and hashes;
role labels/separators are not evidence. Oversized beliefs are explicitly split
for vectors while their canonical payload and full-text projection stay intact.
Hidden reasoning and unavailable/non-text parts are excluded from projection.

Each generation records model/tokenizer revisions/hashes, prefixes, query/document
mode, pooling/dtype/normalization, preprocessing/template/encoder/chunker/ranker
versions, input hashes/counts, exact target revision, encoding time and watermark.
Vectors are stored once in vec0. L2 distance on normalized vectors preserves cosine
ordering while avoiding repeated norm calculation; metric is explicit in metadata.

## Storage and publication

Canonical schema 3 adds covering header/pointer indexes and an expression index for
conversation membership. Back up schema 1/2 consistently before additive DDL;
rollback failures and permit retry. Canonical object JSON, IDs/history and archive
0.1.0 are unchanged. Bundle 0.1 readers accept schema 2/3 and migrate restored stores
before publishing a new root. Public ABI 0.2.0 adds typed retrieval filters/modes.

Derived files live beside each store under `indexes/generations/<UUID>.db`;
`indexes/search.db` contains the atomic active pointer and generation history.
Build separately, quick-check, hash, then publish. A canonical watermark change
aborts publication. An explicit update replays at most 8192 outbox events, otherwise
performs a verified-vector-reusing rebuild. Affected memories, entities and whole
conversation neighborhoods are reprojected; FTS triggers update the clone atomically.
No embedding call executes within a database write transaction.

Updates clone the previous database: copy cost scales with total index bytes.
Vector inference and grouping operate on affected projections; compatible exact
input hashes reuse verified vectors. Two published generations are retained, with
native leases/open Windows files retained until actual completion and cleanup.
Derived indexes are omitted from portable bundles and can always be rebuilt.

## Scope, ranking and ownership

Foreground recall selects 40 lexical and 40 semantic candidates, fuses reciprocal
ranks, applies bounded importance/entity/recency contributions, and removes duplicate
canonical payloads. Context adds pins and up to six ordinary beliefs, plus permitted
recent native observations within the supplied archive budget. Import libraries
never enter that native path. Predicate/entity/lifecycle/time/scope constraints are
applied inside candidate selection, before KNN limits.

Hash-certified immutable current headers accelerate eligibility only while the
relevant canonical outbox remains current. Otherwise use canonical eligibility
joins before top-k. Always reread exact delivered revisions, canonical scope,
source dependencies, expiry and policy epoch. Certified payload fingerprints avoid
repeated reads of redundant candidates, and the first delivered payload must match.
Only query vectors are cached (bounded 128-entry LRU); policy changes invalidate them.
There is no cached evidence/authorization/result that can bypass current policy.

Deep archive uses a separate reader/native owner, source/date/role/conversation/
branch filters and all dependency grants before candidate limits. Literal mode
verifies matches against original canonical text; chunk spans are individually
validated against exact original evidence. Evidence-only library coverage, pending
outbox and unavailable indexes remain explicit. Context preserves both memory and
archive coverage. See [FTS5](https://www.sqlite.org/fts5.html) and
[sqlite-vec KNN](https://alexgarcia.xyz/sqlite-vec/features/knn.html).

Archive ranking batches eligible headers per store, sorts by score/store UUID/row ID, and
hydrates unique winners across certified generations. A chunk and a whole source revision
use separate deduplication keys. Rejected payloads backfill within the same bounded candidate
set; delivery rechecks grants/expiry and the policy epoch. Coverage checks remain independent
of hits. Select and lease the active generation under the publication/prune guard. Keep leases
through hydration and native completion; release the native lease immediately on rejected
submission. Check the caller's explicit deadline after indexed or fallback exact reads. Uncertified
reads inspect all candidate payloads; unmetered requests can hash at most 4 MiB to certify reopened
files. Skipped payloads are not individually diagnosed. No schema or default ranking change.
The `precision_v1` archive experiment did not ship in character chat and is retired in this
checkout. Hybrid archive requests use the existing default ranker; the old
`ember.archive_ranking` request key is rejected rather than silently changing results. No
migration or index rebuild. See [historical measurements](../../../docs/memory-benchmark.md).

One derived writer (two pending), one query encoder (two pending), and distinct
foreground/deep native owners (two pending each) protect the realtime loop.
150 ms foreground and 500 ms archive are soft caller budgets: native operations
may finish later, retaining admission and generation leases. SQLite progress checks
cannot preempt every extension call. Encoding/native failures are consumed and
counted; shutdown drains workers before canonical/root locks are released.

Runtime replay is opt-in at the composition root and coalesces committed writes;
missing/incompatible indexes require explicit setup. Admission failure leaves the
outbox pending for a later write/manual update. Missing encoder permits FTS;
missing/corrupt indexes permit canonical fallback and exact provenance. No startup
rebuild/download or automatic claim activation is allowed.

## Acceptance and remaining gates

`tests/test_phase_five_search.py` covers actual FTS/vector paths, encoder deadlines,
invalid outputs, pre-K scope with both certified and canonical paths, graph/Unicode,
expiry/revoke/forget, dirty revisions, corruption/publication rollback, profile
replacement, vector reuse, native failures/retention and independent async deep work.
Library/bundle tests cover schema-1/2 migration, rollback, seeds, portable closure and
restore. Agent room acceptance uses restored synthetic evidence, real CPU embeddings,
production gateway/Qwen and real LiveKit transport. Aggregate measurements and scale
limits are in the root ADR/runbook; private source text is never committed.

Full extraction, summary storage/import, consolidation and ABI 1.0 are later gates.
No full-scale 500,000-message / 250,000-chunk or Linux claim is made here.


Final local gate: 360 tests passed with real service/model prerequisites enabled;
Ruff, strict Pyright, schema generation and offline lockfile checks passed. The
private official archive remains unapproved. Its final 22,179 chunks were indexed;
100 sampled chunks/181 exact spans were checked without emitting personal content.
Two measured native latency targets and the larger capacity/platform gates remain
open as recorded in the root ADR. This is not a declaration of full spec completion.
