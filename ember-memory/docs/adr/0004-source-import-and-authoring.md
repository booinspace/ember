# Memory ADR 0004: source replay and provider-independent human memories

Status: source import implementation and private archive acceptance verified;
operator-reviewed real-source memory adoption remains open.
Date: 2026-10-07. Extends Memory ADR 0003.

[PLAN section 11](../../../PLAN.md) is authoritative for source findings, design,
implementation and measurements. [Root ADR 0006](../../../docs/adr/0006-phase-three-source-and-runtime.md)
records cross-component delivery and the remaining review gate.

## Decision and ownership

Importers own provider layout, keys, visibility and reconstruction metadata. The
engine owns canonical source rows, identity mapping, transactions and policy.
Maintenance composition translates the injected ImportTarget port to engine APIs.
Source parsing does not import engine internals; character code does not import
source adapters. Existing ABI/archive 0.1.0 models remain the canonical contract.

The official source contains a nested conversation-bearing ZIP with 13 JSON array
shards and a separate account component. Other nested ZIPs/HTML/assets are preserved
and classified without automatic dialogue or account merging. The inspector already
provides the bounds and exact raw/logical/member fingerprints needed for this path.

## Replay architecture

Preserve a single exact ZIP. Parse one bounded conversation at a time in two passes.
The first computes source-key assignments, old/current revisions, coherent snapshot
revisions, ordered part IDs and normalized hashes; publish a private metadata plan
before writing source rows. The second reconstructs from the immutable ZIP and
commits at most 256 records per batch, atomically with keys/cursor/receipt.

This metadata is necessary because an interrupted conversation can have committed
references to records whose keys are not yet committed. New UUIDs on restart would
break those links. Recomputing revisions against partially committed latest rows
would also change the batch request. Retain planned versions and part UUIDs along
with identities; omit all message text, titles, tool payloads and reasoning.

Replay metadata version 0.3.0 stores one crosswalk per source key and references each
normalized record by identity index, revision and ordered part IDs. The header binds
store, namespace, raw/logical fingerprints, qualified components, key adapter and
processing specification. The checkpoint is capped at 64 MiB and contains no source
text, title, tool payload or reasoning. A bounded latest-source lookup runs as one
maintenance reader task, avoiding one query per node and unnecessary history reads.
The `chatgpt_zip_v1` key adapter uses an injective JSON pair encoding; existing
scraper and mapping JSON crosswalks retain their prior semantics.

Completed component cursors enable no-op replay. Lost/corrupt metadata after a
partial commit requires recovery, never fresh IDs, silent source repair or deletion.
Raw/member hashes and reconstructed record hashes are independently verified.
Reasoning/recaps and hidden flags are excluded; completed legacy text can be visible
only under the observed recipient/status/end-turn rule. Unsupported multimodal
parts and unresolved local assets remain explicit coverage, without remote fetch.

## New human memory boundary

The existing authoring draft binds a trusted human subject/character and preserves
the exact entry as a native Document with Evidence, then uses RememberRequest.
Default authority stays read-only; explicit operator composition supplies confirmed
user authority. Source and belief commits have separate idempotent receipts: a
crash may leave a reusable source observation, and exact-input retry resolves it.

This surface is not an LLM mutation tool. Optional interpretation is still checked
by the engine's entity/predicate/modality/evidence invariants. Imported historical
claims use their original source evidence through the candidate/confirmation path,
rather than being rewritten as a new human observation. ChatGPT memory summaries
remain a separate future adapter and are not a prerequisite for native memories.

## Coupling, acceptance and open work

The bulk lookup is engine maintenance coupling, not an ABI extension. Parsing and
library growth stay outside the character's accepted-belief fast path. No extra
database daemon, import-time LLM or realtime archive scan is introduced.

Synthetic tests pass for graph/visibility/privacy behavior, Unicode, changed-source
revisions, stable part IDs, namespace binding, no-op replay, corrupted/lost checkpoint
failure, and recovery after interruption inside a 1,201-record graph. The full local
quality gate passed (294 passed, one opt-in real-model test skipped). The actual
archive import wrote 50,520 records over 13 cursors; replay wrote zero. On this
machine the first import took 138.6 seconds, an instrumented temporary run took
134.07 seconds with sampled process RSS of 268,574,720 bytes, and the checkpoint,
database and preserved ZIP were 16,947,692, 170,311,680 and 881,600,298 bytes.

The existing whole-message primary-GPU extractor matched zero of the archive's 9,778
user messages, so no candidate or character memory was created. To close the narrow
phase 3 memory gate, add a source-backed rule with sanitized tests or select a
supported source claim for operator review, then verify confirmation/adoption,
restart, recall and exact evidence. Import does not confer grants or activate beliefs.
Saved-memory inclusion stays unestablished. Full approval/seeding/bundle policy
belongs to phase 4 and generalized model extraction to phase 6.
