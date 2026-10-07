# Root ADR 0005: phase 2 embedded canonical engine behind the memory ABI

Status: implemented and validated; phase 2 architecture review remains the
checkpoint before phase 3.
Recorded: 2026-10-07. Engine 0.1.0; initial SQLite schema 0.1.0 / user_version 1.
ABI/archive remain unchanged at 0.1.0.
Scope: durable memory and integration/lifetime responsibilities.

## 1. Desired behavior

Persist generic source graphs and independent character beliefs; preserve revisions,
exact provenance and temporal history; restart with stable identity; perform
atomic/CAS/idempotent writes; support canonical retrieval without derived indexes
or an available shared library.

## 2. Ownership

ember-memory-engine owns SQLite, source keys, transactions, workers, access/
suppression, receipts/audits and transactional invalidation outbox. Libraries own
original sources/evidence; characters own beliefs/grants/suppression. Composition
owns explicit engine creation/closure. Business logic uses injected ABI clients.

[Memory ADR 0003](../../ember-memory/docs/adr/0003-canonical-engine-and-workers.md)
owns backend detail; [phase 2 walkthrough](../phase-2-canonical-engine.md) shows the
coupling map. This root record explains application consequences rather than
providing a duplicate SQL schema.

## 3. Existing abstraction

Implement ArchiveWriter, MemoryStore and AsyncMemoryClient from phase 1. Reuse
SourceRef/Evidence, predicates, temporal models, queries, receipts and typed failures.
ChatGPT models remain importer-owned. Add no backend fields or model/framework
dependencies to ABI.

## 4. Necessary primitives

Engine-private OS locks, atomic source-key assignments, source resolver, trusted
confirmed-user binding, owned bounded workers and injected clock/context cost.
Native evidence can reuse its writer transaction to prevent self-deadlock. No
separate memory server, transport or public client contract is needed for the
planned in-process deployment.

## 5. Contracts and invariants

Source/body revisions and evidence are immutable; replays verify stored bytes.
Keys, source batch/cursor and receipt commit together; belief revision, receipt,
audit and outbox commit together. Expected revisions protect updates; a changed
semantic request under a used key fails. Source times stay unchanged/null; engine
assigns recorded time. Snapshot grants do not expand on import. Composition, not
model verdicts, supplies manual authority. Forgetting previews/suppresses historical
dependencies and registered aliases without deleting shared sources or another
character's beliefs.

## 6. Failure behavior

Reject unsafe/nonlocal roots, duplicate ownership, unknown schemas, corruption,
stale revisions, denied access, invalid evidence and unsupported query cursors.
Never recreate a missing registered character DB. Missing libraries preserve
adopted beliefs with unavailable provenance; missing indexes produce partial/
degraded reads. Immutable revisions and final policy checks provide cross-store
consistency, without cross-database atomicity. Failed character transactions may
leave safe unreferenced immutable evidence in a library.

Queued cancellation may prevent execution. Running work retains ownership/admission;
write timeout/cancellation does not prove rollback. Resolve with the same request/
key after completion. Async clients drain before blocking engine closure off the
realtime loop. Process death releases OS locks; incomplete WAL transactions roll back.

## 7. Acceptance tests

The phase 2 checkpoint passed 242 pytest tests including 42 engine acceptance tests,
locked installs, Ruff format/lint, strict Pyright and schema reproduction on Windows/
Python 3.12.15. Tests cover actual temporary SQLite, injected transaction failure,
concurrent CAS, child-process crash/recovery, exact Unicode quotes, temporal
replacement, grants/suppression/isolation and async heartbeat/cancellation/timeout/
drain. This is a historical implementation checkpoint. Linux and actual-export/
runtime acceptance were not executed in that delivery.

## 8. Implementation, coupling and SQLite lifetime

SQLite runs inside the Python application process through the standard-library
sqlite3 module. It adds no separate Python SQLite package, server, port or daemon.
An explicitly opened CanonicalEngine owns connections and one writer/two reader
workers per store with bounded admission (default 16 jobs per group). Closing the
engine drains workers/closes connections; .db files retain data. Future sessions
reopen the same data root. No process needs to keep running for memory persistence.
Importing the package does not open databases or start services.

Registry/store databases have separate identities under a local root. Eager opening
introduces per-store thread/connection costs. Source resolution is engine-internal
SQLite coupling; ABI/business logic remain independent. Engine ownership prevents
duplicate instances for one root. The later agent launcher owns/drains this lifetime
through composition; phase 2 does not implement ember start or full application
shutdown.

The outbox commits now but index/context/job consumers do not exist yet. Archive
fallback is a bounded scan with partial coverage; default context cost is provisional
UTF-8 bytes. Full grants/seeds/bundles, indexes, extraction, consolidation and actual
model prompt accounting remain in their phases. No model generation or personal
source activation occurred. Phase 0b remains open; phase 3 has not started.

See [agent composition](../../ember-agent/docs/adr/0001-composition-and-gateway.md)
and [ADR index](README.md) for ownership and review policy.
