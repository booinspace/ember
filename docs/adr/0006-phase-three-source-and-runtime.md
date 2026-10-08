# Root ADR 0006: phase 3 source adapters, memory authoring and P0 runtime

Status: phase 3 implementation and private ZIP import verified; operator-reviewed
real-source memory adoption remains open.
Date: 2026-10-07. Extends root phase 2 and the component records linked below.

## 1. Desired behavior

Import real supplied sources deterministically with exact provenance and replay,
create new explicitly confirmed memories independently of provider/export format,
and stream a recalled-context conversational turn through one pinned local P0 gateway.
Prove restart and original-source evidence. Missing ChatGPT saved memories do not
block these paths. The official ZIP was inspected and its supported conversations
were imported privately. One real-source candidate/adoption path still needs
operator review. See section 11 of [PLAN.md](../../PLAN.md) for format findings,
implementation evidence and the current stopping point.

## 2. Ownership

Memory owns source adaptation, deterministic proposals and authoring/persistence.
Composition binds character identity, human authoring and lifecycle. Character and
LiveKit logic depend on the existing ABI client. Agent owns prompt representation,
P0 transport/admission, model pinning, visible streaming and runtime diagnostics.
Neither source files nor model output confer mutation or scheduling authority.

## 3. Existing abstractions

Reuse ArchiveWriter, MemoryStore, AsyncMemoryClient, SourceRef/Evidence, immutable
revisions, source keys/cursors, grants and idempotency. Reuse deployment config and
the same gateway roadmap. No second memory owner, scheduler or extraction LLM.

## 4. Necessary primitives

An injected importer maintenance port connects provider-private keys to the engine
without engine dependencies in source models. Generic authoring preserves an original
observation and creates an evidence-backed draft through existing mutation contracts.
A versioned P0 request/visible stream and authenticated local gateway process bridge
all generation clients to one upstream. A character turn core/prompt composer and
LiveKit adapter share that route. Source inspection/metadata are not generation.

The official ZIP uses a verified component reader, metadata-only replay checkpoint
and bounded bulk source-revision lookup on the maintenance port. Two offline
normalization passes keep one graph's bodies in memory: prepare stable keys,
revisions and parts; publish metadata; reconstruct/verify and commit batches of at
most 256 records. Replay metadata version 0.3.0 binds namespace, raw/logical source
identity, qualified components, key adapter and processing specification. It stores
identity indexes, revisions, part IDs, boundaries and hashes without repeated source
references or message content. No public ABI or canonical schema change was required.

## 5. Contracts and invariants

Retain originals verbatim in ignored local storage. Imports atomically commit keys,
records/cursors/receipts. Canonical IDs survive replay; changes append revisions.
The scraper supplied 219 conversations/5,585 messages and 989 failures, without graph
or channel metadata; preserve gaps, invent neither graph nor saved memories. Unknown
assistant/tool visibility stays excluded. New memories require trusted human entry;
archive extraction emits narrow explicit-fact proposals for confirmation. Memory
text is untrusted data, never instructions. Hidden reasoning never enters visible
streams/history/prompts. P0 alone, one allowlisted model/digest, explicit contexts,
one active upstream, bounded requests and conservative prompt/output budgets.

The official source has 13 nested conversation shards, 1,205 distinct conversations,
24,055 messages and 25,260 nodes, including a 1,695-node conversation. All shards
and account metadata belong to one observed container. Keep attachment ZIPs and
the earlier scraper scope separate. `thoughts` and `reasoning_recap` are excluded;
completed legacy text with the observed null-channel flags can be visible under a
versioned rule. No saved-memory collection has been established.

Replay metadata must preserve uncommitted referenced UUID4s, planned revisions and
part IDs across partial conversations. Key/record/cursor/receipt transactions remain
engine-owned. Source-record changes append history; do not silently reinterpret
fixed SourceRefs for smaller storage. Initial asset resolution remains explicitly
unavailable while raw files, hashes and original references are retained.

## 6. Failure behavior

Malformed/changed/unsafe sources fail with safe diagnostics and resumable committed
state. Unsupported facts stay unapproved. Changed idempotency content fails. Denied
or forgotten evidence stays denied. Wrong/missing model, unsupported workloads,
invalid auth, overflow and occupied capacity fail before generation. Cancellation
must not imply runner release; keep owned draining or fail closed if release is
uncertain. No direct Ollama fallback or cloud/provider bypass. Optional LiveKit
transport availability does not disable the local text/memory path.

## 7. Acceptance tests

Synthetic tests cover nested shards, graph branches, visibility, hidden-text
exclusion, collision-safe IDs, Unicode, changed revisions, exact replay, checkpoint
binding and interruption after a 256-record batch in a 1,201-record conversation.
Existing tests cover human authoring, provenance, gateway lifecycle, fake-memory
injection and LiveKit boundaries. The full local gate passed: 294 passed and one
opt-in real-model test skipped. The private archive import wrote 50,520 records
across 13 completed cursors; a repeat wrote zero. Measured import/storage costs are
recorded in PLAN section 11. Operator-reviewed exact-claim adoption/restart/recall
through the P0 path remains open; the import alone does not activate beliefs.

## 8. Implementation and review

The implementation includes scraper and official ZIP normalization, generic human
authoring, operator proposal/confirmation, P0 service/client, shared turn core and
LiveKit adapter work. The official ZIP is stored in a separate private library;
50,520 source records and all 13 component cursors were verified. The exact archive
is 881,600,298 bytes; the private plan is 16,947,692 bytes and SQLite database is
170,311,680 bytes. The initial import took 138.6 seconds. A temporary measured
re-import took 134.07 seconds and sampled 268,574,720 bytes process RSS; a complete
no-op replay took 7.0 seconds. The temporary measurement database was removed.

No claim has been activated. The existing whole-message primary-GPU detector matched
zero of the archive's 9,778 user messages, so no candidate was created. The remaining
phase 3 acceptance requires either a narrow source-backed rule with sanitized tests
or a specific supported source claim for operator review, followed by confirmation/
adoption and restart/recall/evidence checks through the injected client/P0 path.
Saved-memory inclusion remains unestablished. No phase 4 implementation has begun;
stop here for the user's architecture review.

[Memory ADR 0004](../../ember-memory/docs/adr/0004-source-import-and-authoring.md)
owns replay/authoring details;
[Agent ADR 0002](../../ember-agent/docs/adr/0002-phase-three-p0-turn-slice.md)
owns the existing P0/turn boundary and runtime limits. Finish only phase 3, update
actual validation/status and stop for the user's architecture review before phase 4.

## LiveKit text-runtime extension — 2026-10-08

Managed server/gateway startup, one explicitly dispatched SDK text room, private
credentials/tokens, terminal participant and owned shutdown are now implemented.
[Agent ADR 0003](../../ember-agent/docs/adr/0003-managed-livekit-text-runtime.md)
owns the new lifecycle/protocol decisions. Actual room tests include restarted
synthetic canonical memory and actual non-MTP Qwen via P0. Root/agent locked setup
succeeded; current test counts are recorded in PLAN's 2026-10-08 runtime record.
One canonical owner supplies the room; no per-job engine or ABI/schema change.
This completes the runnable text transport slice, while exact real-export claim
review/adoption acceptance remains open. Browser/audio and full Phase 8 deployment
composition remain future work. Stop for architecture review before Phase 4.


## Phase 4 transition — 2026-10-08

The user authorized Phase 4 implementation. [Root ADR 0007](0007-phase-four-libraries-characters-bundles.md)
now owns that review checkpoint, extending the existing seed/grant primitives with
approved library snapshots, independent adoption and portable bundles. This does
not close Phase 3's operator-reviewed real-export claim gate or add voice support.

## Participant startup guidance (2026-10-08)

livekit-chat joins the room published by ember start; a gateway-only process does not
host that room. Missing discovery previously appeared as generic invalid local state.
Participant discovery now produces actionable, private-safe errors without opening
memory or starting providers. Agent ADR 0003 owns the diagnostic detail and regression
coverage. Runtime/service/room ownership remains unchanged.
