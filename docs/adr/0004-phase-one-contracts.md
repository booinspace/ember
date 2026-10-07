# Root ADR 0004: phase 1 experimental memory ABI and normalized archive

Status: generic phase 1 core implemented as 0.1.0; source-dependent and ABI 1.0
acceptance remain open.
Recorded: 2026-10-07, summarizing the implemented contract delivery.
Scope: the interface shared by memory, future agent composition and importers.

## 1. Desired behavior

Provide typed portable source, evidence, memory, temporal, predicate, mutation and
retrieval contracts before backend/runtime implementation. Permit engine development
without inventing personal facts or claiming actual-export compatibility.

## 2. Ownership

ember-memory-abi owns public contracts/validators. Importers own provider models
and eventual normalization; the engine owns persistence/policy enforcement.
ember-agent character logic consumes ABI clients; composition chooses a backend.
The root records integration; detailed decisions remain in
[Memory ADR 0002](../../ember-memory/docs/adr/0002-phase-one-before-export.md).

## 3. Existing abstraction

Use PLAN's SourceRef, exact EvidenceSpan, memory/predicate/temporal semantics,
ArchiveWriter, MemoryStore and AsyncMemoryClient. Phase 0a objects inform provisional
mappings but cannot become generic public contracts.

## 4. Necessary primitives

Strict Pydantic v2 models, UUID4 canonical identities, positive revisions, UUID5
derived chunks, versioned serialization/hashes, dual evidence coordinates, predicate
validation and typed content-free failures. Reproducible JSON schemas come from the
same models rather than a second manually maintained contract set.

## 5. Contracts and invariants

Paths/provider/vector keys stay outside public identity. Exact Unicode text binds
codepoint and UTF-8 byte ranges and source/quote hashes; browser UTF-16 conversions
reject split boundaries. Valid/observed/recorded time remain independent. Unknown
readable kinds/modalities/extensions do not acquire write or activation semantics.
Existing-belief mutations require expected revision; mutations carry idempotency
keys; source records and cursor share an archive batch contract. Schema validation
cannot approve claims, resolve original sources or enforce grants.

## 6. Failure behavior

Reject invalid evidence, identities/timestamps and unsupported writes with typed
validators/errors. Preserve unknown readable values for compatibility. Shallow model
freezing is not persistence immutability; the engine must snapshot nested data and
enforce transactions/ownership. Timeout/cancellation does not prove rollback or
permit changing a retry key.

## 7. Acceptance tests

Synthetic serialization/round-trip, Unicode/UTF-16 bounds, source/hash mismatch,
timestamp precision, unknown extensions, predicates/modalities and protocol contracts.
Committed schemas reproduce with `scripts/export_contract_schemas.py --check`.
[Phase 1 contracts](../phase-1-contracts.md) describes the surface and actual-source/
restore gates. This delivery preceded phase 2 storage tests; current test totals
belong to the latest validation snapshot.

## 8. Implementation and coupling

The ABI depends on Pydantic and the standard library. It imports no SQLite, Ollama,
LiveKit, MCP or harness package. Agent and engine independently pin ABI 0.1.0;
editable sibling paths are development installation wiring. Running clients/DBs
were not implied by this phase; [phase 2](0005-phase-two-canonical-engine.md) now
implements them behind these interfaces.

[Memory ownership](../../ember-memory/docs/adr/0001-ownership.md) and
[agent composition](../../ember-agent/docs/adr/0001-composition-and-gateway.md)
retain component boundaries. Actual-source reconciliation and complete character/
gateway/backup/restore acceptance precede ABI 1.0. Additive changes/migrations must
preserve history. [ADR index](README.md) tracks the phase review workflow.
