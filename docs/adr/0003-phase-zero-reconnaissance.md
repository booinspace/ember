# Root ADR 0003: phase 0a reconnaissance before the real export

Status: phase 0a implemented; phase 0b remains pending the actual archive.
Recorded: 2026-10-07, retrospectively from the implemented preparation work.
Scope: source reconnaissance and its canonical memory/model runtime boundary.

## 1. Desired behavior

Inspect candidate exports without changing or extracting the original, observe
format/graph capabilities and explicit gaps, and prepare provider models while
the personal archive is unavailable. Public observations are hypotheses; synthetic
fixtures cannot establish account-specific support.

## 2. Ownership

ember-memory's importer package owns reconnaissance and provisional ChatGPT models.
Its maintenance CLI owns report output. Root docs contain research, source-model
explanation and phase 0b runbook. ember-agent owns deployment configuration and
future generation routing; inspection does not use generation.

## 3. Existing abstraction

Extend the phase -1 inspector, report model and CLI. Keep transient private provider
objects separate from sanitized diagnostic reports. Do not treat provider IDs,
current-node hints or guessed timestamps as finalized canonical semantics.

## 4. Necessary primitives

Bounded nested-container/shard/directory traversal, incremental conversation parsing,
container/member/logical hashes, explicit coverage and typed provisional graph/
content/metadata models extend inspection rather than creating a second memory owner.
Read-only local model inventory and private pinned profiles prepare runtime
verification without adding a generation client.

## 5. Contracts and invariants

Preserve raw bytes, every observed branch, null-message node, unknown field and
absent/null distinction. Do not choose a canonical branch, fill source time from
inspection time, infer saved-memory inclusion or fetch remote attachments.
Reports expose structural counts/hashes rather than messages, reasoning, titles,
provider IDs or raw paths. Report format 0.0.2 remains preparation metadata and
does not share the ABI/archive version.

## 6. Failure behavior

Unsafe paths, links/reparse points, ambiguous names, encrypted/corrupt containers,
duplicate or malformed JSON and resource limits fail or visibly defer coverage.
Reports refuse to overwrite the input or another report. Unknown and oversized
components remain gaps rather than successful normalization. A configured model
pin is not a fresh runtime verification or benchmark.

## 7. Acceptance tests

Synthetic nested ZIPs, shards, directory stability, streamed conversation variants,
malformed input/resource guards, graph ambiguity and privacy sentinels. See
[research](../phase-0-research.md), [source model](../phase-0-source-model.md) and
[runbook](../phase-0-runbook.md) for actual-export evidence still needed.
Source safety/privacy tests continue in the current gate. This phase performed no
model generation or real-source acceptance.

## 8. Implementation and coupling

Implemented in ember-memory-importers through the existing maintenance CLI and
sanitized report boundary. Streaming adds the importer-local ijson dependency;
provider-specific models remain outside the generic ABI. Inspection does not write
through ArchiveWriter or construct CanonicalEngine.

[Model profile preparation](../model-profiles.md) records local metadata observations
and private configuration, not performance or MTP acceptance. The production
normalizer must translate verified provider fields into the
[phase 1 ABI](0004-phase-one-contracts.md). The
[phase 2 engine](0005-phase-two-canonical-engine.md) uses generic contracts independently
of these hypotheses. Phase 0b and phase 3 real-import/historical-recall gates remain
open. [Phase -1](0001-phase-minus-one-scope.md) established the preparation boundary;
[ADR index](README.md) explains root/component documentation ownership.
