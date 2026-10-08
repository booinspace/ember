# Memory ADR 0007: extraction jobs and summary sources

Date: 2026-10-08. Status: implemented and validated; operator architecture review pending.
See [Root ADR 0009](../../../docs/adr/0009-phase-six-extraction-and-background-inference.md).

Source adapters preserve originals and normalize without inference/activation.
chatgpt_memory_summary supports explicit text/Markdown and documented JSON memory
entries; it does not guess that arbitrary conversation JSON contains saved memories.
Structured extraction uses a provider-independent async model Protocol; a narrow HTTP
adapter sends P3/P4 to the existing authenticated gateway, never to Ollama.

A job pins source revisions/hashes, extractor/model/policy specifications and subject.
Bounded exact source windows contain no hidden reasoning. Only source references,
coordinates and hashes are checkpointed, not duplicate raw prompt/output blobs.
Candidate models and decision inputs preserve modality, source authority, original
predicate and normalization choices. Completed windows commit candidates/checkpoints
atomically; failures record safe codes and are resumable. Durable review decisions
survive processing cleanup and re-extraction. Existing human approval and independent
seeding retain authority; model verdicts remain proposals, not confirmed user input.

Summary-only claims remain below automatic confidence until independently supported
or explicitly confirmed. Unsupported predicates/modalities and graph ambiguity remain
reviewable/conservative. No silent valid-time inference or accepted-belief rewrite.

Use [the operator runbook](../../../docs/phase-6-runbook.md). Personal source
processing is left to the operator; no automatic personal approval is enabled.

The stored specification pins exact model digest, extractor/prompt/output-schema/policy
hashes and window settings. The current policy definition is an immutable typed value.
Job input revisions are frozen by a canonical watermark. Planning uses canonical
source pages and shared graph traversal, then commits metadata batches of 128. Inference
uses bounded overlapping source windows without duplicating prompts/raw output in SQLite.
All eleven modalities survive proposed/accepted records and Unicode evidence offsets
are host-verified. Extractor 0.1.1 requires a nullable `quote_start` in structured output,
prefers quotes unique within the bounded window, derives unique positions in the host, and
requires an exact verified supplied hint for repeated quotes. This corrects a prompt/schema
mismatch found in the first operator archive sample. Prompt/schema hashes force a new job
key; prior failed jobs retain their original pinned contract. Extractor 0.1.2 uses a
discriminated structured schema: facts require predicate/value/entity fields with explicit
scalar-versus-entity shape, while episode/goal/plan claims require null fact-only fields.
The host validates the person.knows entity rule; unknown but complete predicate strings
remain visible proposals with no write semantics. Required fields prevent schema-permitted
omission from being confused with a complete fact. Summary observation/valid time remains
unknown.

The default 1,800 UTF-8-byte windows have 64-codepoint overlap, at most four claims and
1,192 output tokens. Failed/truncated/unsupported output is resumable, never silently
executed. Completion reports successful windows, not perfect semantic extraction.
Source part hashes are computed once during planning; prompts resolve and verify original
content only for their current window. Source/graph/access helpers live below indexing
and processing, avoiding a dependency on derived vectors or duplicate graph logic.

Durable tables retain immutable extraction/policy definitions, candidate associations,
policy inputs/verdicts, safe failures and review decisions. Job/window input headers are
immutable; statuses/attempt counts are mutable operational state. Processing uses one
engine-owned root admission and one bounded worker, offloading filesystem/SQLite work;
model calls occur outside write transactions. Exact evidence and all proposals for one
completed window commit with its checkpoint, so retry cannot duplicate completed work.

Manual review files expose exact private quotes and immutable candidate hashes. Only the
operator decision is editable. Review and approval recheck current suppression; library
approval does so inside its transaction. Approval/seeding reuse Phase 4 receipts and
independent entities. Summary-only confidence stays at 0.79 and manual override is a
separate recorded approval. Reject decisions survive re-extraction; reevaluation appends
changed verdicts and never rewrites accepted knowledge. Fingerprints/conflicts are
conservative proposal diagnostics, not semantic consolidation or temporal supersession.
Generated summaries/repeated extraction do not count as independent evidence.

ABI 0.3.0 permits planned modalities for episode writes. Engine/schema 0.4.0 upgrades
schema 1/2/3 with consistent backups and failed-DDL rollback. Full source backups preserve
paused jobs/review history; seeds and selective exports preserve only referenced generic
processing/policy definitions, omitting private job/review data. Archive/bundle 0.1.0 and
canonical IDs/revisions/raw originals retain their meanings. CLI status includes up to
16 safe failed-step codes/ordinals and aggregate counts. Review pages are capped at 1,000;
file application is validated before per-candidate commits and retries idempotently.

The final gate/result is recorded in Root ADR 0009. Tests cover verbatim CRLF/Unicode,
explicit JSON fields, hidden-reasoning exclusion, malformed/missing/ambiguous evidence,
all planned modality behavior, manual review and tamper rejection, unsupported predicates,
known conflicts, rejected proposals, interruption/resume/cancel, atomic rollback,
suppression before/after extraction, policy pinning/reevaluation, schema 3 recovery,
full source-job restore and selective specification closure. Real Qwen extraction from
synthetic saved text is approved, seeded, indexed, restarted and recalled semantically
through the actual LiveKit agent hook and P0 gateway. Personal imports are not test data.
