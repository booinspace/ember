# ADR 0003: phase 2 canonical SQLite engine and owned workers

Status: implemented and validated; awaiting user architecture review before phase 3.
Date: 2026-10-07. Scope: phase 2 only, on top of ABI/archive 0.1.0.

## 1. Desired behavior

Persist immutable normalized source/graph revisions and character beliefs, recover
identities after restart, resolve exact evidence, preserve valid/observed/recorded
history, perform idempotent compare-and-swap mutations, and start without derived
indexes. Provide the existing sync and async interfaces without realtime-loop I/O.
Actual-export acceptance, production normalization, model generation, indexing,
seeding/bundles and runtime startup stay in their designated phases.

## 2. Ownership

`ember-memory-engine` owns SQLite schema, store identity/ownership, source keys,
transactions, canonical revisions, provenance, mutation receipts, outbox/audits,
current access/suppression checks and worker lifecycle. Libraries and characters
use separate local databases under the planned layout; registry.db stores identities.
Composition creates/closes the engine and supplies trusted character bindings.
No runtime framework, provider or importer becomes the canonical memory owner.

## 3. Existing abstractions

Implement MemoryStore, AsyncMemoryClient and ArchiveWriter from memory-abi 0.1.
Reuse SourceRef, EvidenceSpan, source/quote validation, temporal models, predicates,
mutation/query/result contracts and typed failures. Keep physical paths and source
provider IDs in engine maintenance records rather than exposing them through ABI.

## 4. Necessary primitives

- A store/database owner with an OS-held lock and thread-owned SQLite connections:
  prevent simultaneous writers and release ownership safely after process death.
- Source-key assignments committed with the normalized batch and resume cursor:
  stable provider-to-UUID crosswalks without baking ChatGPT fields into storage.
- A source resolver boundary: allow character evidence to refer to library/native
  sources without assuming cross-database atomicity or copying archive bodies.
- Trusted mutation authority: default clients are read-only. Explicit confirmed-user
  bindings permit manual mutations; model-supplied authority/verdict strings do not.
- Owned bounded worker groups: one writer and two foreground readers per store,
  admission limits, explicit draining and receipt-based uncertain-write resolution.
- Injectable clock/token counter: deterministic recorded-time tests and bounded
  context without coupling the memory engine to Ollama or a tokenizer package.

These are engine/composition concerns, not additional product repositories or
alternate memory interfaces. Full grant/seeding/bundle and derived-worker features
remain phase 4/5; the access/suppression foundations are required now for safe reads.

## 5. Contracts and invariants

Canonical content/revisions are append-only. Exact repeated writes can replay;
changing an existing revision fails. Existing belief changes require expected
revision. Scoped idempotency keys bind an operation and semantic request hash;
changed requests under a used key fail. Record, key mapping, resume cursor, receipt
and import audit commit together; belief changes, receipt and outbox commit together.
Unknown source times stay unknown; recorded clocks are server-assigned UTC integer
microseconds. Source text is unchanged and reasoning has no normalized body.

Library access requires explicit evidence_read/archive_search grants for exact
existing source revisions; later imports do not expand a snapshot grant. Native
character sources belong to that character. Source claims are never automatically
activated. Manual mutations still validate predicate/modality, owned entities,
source evidence, current grants and suppression. Missing graph links and forward
references are retained; storage does not repair the source graph.

## 6. Failure behavior

Unsafe/nonlocal canonical roots, ownership conflicts, unknown schemas, malformed
canonical state, idempotency conflicts, missing sources, stale revisions, denied
access and expired deadlines yield typed content-free failures. Failed transactions
roll back. Missing indexes yield truthful degraded canonical retrieval/provenance.
Queued cancellation may avoid work; running native work remains owned/bounded.
Running-write timeout/cancellation never implies rollback: resolve using the same
idempotency request/receipt after worker completion, rather than a new mutation key.

Separate source/character database consistency relies on immutable source revisions
and final current-policy checks. It does not promise a cross-database transaction.
Forgetting suppresses source/claim/origin dependencies, invalidates local pins and
records a policy/outbox change. Later runtime consumers must purge copied context;
a database write cannot retract data already delivered to a caller.

## 7. Acceptance tests

Use synthetic sources only: reopen/restart identity; graph/missing-link fidelity;
immutable revision and key/cursor atomicity; Unicode evidence/source hash rejection;
manual authority/character isolation; no implicit grant expansion; CAS and changed-
key conflicts; temporal/recorded-history queries; explicit supersession; suppression
and pin invalidation; outbox/receipt durability; missing-index startup; ownership
conflicts/crash release; worker admission, heartbeat responsiveness, cancellation,
uncertain writes and draining. Preserve ABI conformance and dependency boundaries.
Run locked setup, Ruff, strict Pyright and the complete pytest suite.

## 8. Implementation and review

Implement the synchronous core first, then the bounded async facade, then acceptance
coverage and an architecture walkthrough. Update this ADR with actual decisions,
limitations and validation. Stop for the user's architecture review before phase 3.


## Implemented decisions and consequences

- Engine 0.1.0, initial SQLite schema 0.1.0 / user_version 1; ABI/archive unchanged
  at 0.1.0. Foreign/unknown versions are rejected without reset. Future migrations
  require PLAN backup, ownership, validation and restoration.
- Registry ownership serializes engines per data root. Each store owns one writer
  and two readers, 16 accepted jobs per group by default. Available registered
  stores open eagerly: simple lifetime ownership, with per-store thread/connection
  costs to revisit after scale profiling.
- Engine-private resolver reuses the native writer transaction and resolves shared
  sources read-only on the already owned worker. SQLite coupling stays inside
  the engine. Shared immutable evidence may survive a failed character transaction
  without creating a belief; no cross-database atomicity is promised.
- Missing character stores fail without recreation. Missing libraries retain
  unavailable catalog identity while adopted beliefs survive; evidence reports
  unavailable. Restart reopens restored libraries. Corrupt/unknown schemas fail.
- Default bindings are read-only. Trusted confirmed-user bindings allow validated
  manual mutations; model verdicts are not approval. Meaningful predicate
  replacement retains distinct transition evidence in provenance/dependencies.
- Original source time remains unchanged/null; recorded time is assigned by the
  engine. Existing source replay compares content independently of recorded time,
  while the batch receipt still binds the exact submitted batch/assignments.
- Snapshot grants distinguish evidence from archive access and do not expand on
  import. Part evidence grants are supported; archive grants cover whole records.
  Forgetting previews and suppresses dependencies across every revision, including
  registered aliases. Suppressed parts conservatively hide archive records.
  Source revocation preserves adopted beliefs. Full seeds/scopes/bundles remain
  phase 4, automatic extraction authority phase 6 and consolidation phase 7.
- Archive fallback scans at most 4,096 candidates with truthful coverage and rejects
  query cursors. Context tracks archive/evidence dependencies, but defaults to a
  provisional byte cost. Actual model prompt budgets and copied-context purging
  remain phase 3/8 work. Future/expired pins cannot bypass current time visibility.
- Running cancelled/timed-out work retains admission until completion; writes use
  the original idempotency key for reconciliation. Async drain retains the last
  128 safe failures and signals truncation. Engine close is blocking composition
  off the realtime loop, not the later application's complete shutdown controller.
- Outbox rows commit now; there are no index/context/job consumers yet. No model
  generation, automatic approvals, production import, bundles or services started.

## Validation and review checkpoint

Locked installation, Ruff format/lint, strict Pyright, reproducible ABI schemas and
242 pytest tests pass on Windows/Python 3.12.15, including 42 new engine tests and
child-process crash/WAL recovery. All committed sources are synthetic. Linux CI
is configured but not executed locally. Actual-export and ABI 1.0 acceptance remain
open. See [phase 2 review](../../../docs/phase-2-canonical-engine.md) for the coupling
map, behavior and limits. Stop for user review before phase 3.

## Root integration and embedded SQLite lifecycle

[Root ADR 0005](../../../docs/adr/0005-phase-two-canonical-engine.md) records the phase
outcome and application integration; this memory record owns backend details.
[The ADR index](README.md) uses component-local numbering: this is Memory ADR 0003,
distinct from the root phase 0a record.

SQLite runs inside Ember's Python process through standard-library sqlite3. It needs
no separate Python SQLite package, server, listener or daemon. An explicitly opened
CanonicalEngine owns connections/workers until close; close drains them while .db
files retain data. A future instance reopens the same root. Keeping a background
process alive is unnecessary for persistence. Package import has no database/startup
side effects. Agent composition will own this lifetime when implemented; the
phase 2 library does not implement the launcher/runtime.
