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

## Initial bounded quote retry and continuation (2026-10-08)

The processing runner owns optional retry/continuation, and the CLI supplies runtime
controls through the existing service call. `window_attempts` is bounded to 1-3;
`continue_on_error` defaults to false. Only `quote_missing_or_ambiguous` and
`quote_not_exact` are eligible because validation raises them before entering the
candidate/checkpoint transaction. Other failures stop even when continuation is enabled.

Failed attempts append audit records. Transient recovery clears the current window
error while retaining prior attempts. Exhausted windows remain unresolved and do not
advance completed counts; continuation processes later pending windows. Overall status
and CLI exit remain failed while any unresolved window exists. Window limits count
distinct attempted windows, and restarting can explicitly select failed windows without
replaying completed candidates. Cancellation still drains owned work as before.

These controls change neither ClaimBatch nor ExtractionSpec and require no migration,
extractor version change or new job key. Imported candidate approval/seed authority is
unchanged. Root integration and planned Phase 7/8 scope remain in
[Root ADR 0009](../../../../docs/adr/0009-phase-six-extraction-and-background-inference.md).

## Window-local failures and successful continued execution (2026-10-08)

The initial quote-only rule now includes rejected claim/JSON-schema output. The
gateway adapter distinguishes structured-output rejection from HTTP/protocol/identity
failures so infrastructure errors cannot silently consume an entire archive.
Suppressed/unsupported source windows are skipped without inference or retry.
Known window-output failures retry at most window_attempts times, then continuation
retains an unresolved failure and selects the next pending window without a failure
count threshold. All candidate writes remain atomic and fully validated.

JobStatus adds optional transient run_outcome metadata for a single execution:
finished_with_failures permits CLI exit 0 after all requested work was attempted;
stopped_on_error preserves nonzero exit for global failures. Durable job status/counts
remain truthful, and failed windows remain explicitly recoverable. Status-only reads
do not reconstruct a prior invocation outcome. Default fail-fast behavior is retained.

This extends only runner/transport diagnostics and CLI behavior. ClaimBatch and the
pinned extraction specification are unchanged; no migration or new job is needed.
The full approval/review/seeding path remains explicit.

## CLI JSON output and PowerShell ownership failure (2026-10-08)

A concurrent summary import correctly failed because the archive extractor owned
the canonical root. Its diagnostic was previously printed on stdout, so direct
ConvertFrom-Json parsing obscured the real error and subsequent pasted commands
received empty store/job IDs. Summary/extraction diagnostics now use stderr.
Runbook examples check the native exit code before parsing JSON and require dependent
steps to stop after failure. The root's single-owner lock and gateway admission
remain unchanged; no running import is interrupted or lock removed by this fix.
Regression coverage verifies busy-root and invalid-input failures leave stdout empty.

Review export also reports an existing output file explicitly instead of masking
FileExistsError as generic invalid state. The runbook opens the editor only after
successful export so a failed export cannot leave an empty editor-created review.
Existing reviewed decisions are never overwritten. The 56 processing tests and
format/lint/strict-type checks passed, including preservation of edited review files.

## Interactive operator composition (2026-10-08)

The memory CLI owns `ingest` orchestration and injectable terminal I/O. It reuses
normalizers and the canonical root, extraction gateway adapter, review authority,
library seeding and CPU index updater. Configuration coupling is limited to a typed,
read-only deployment-v1 subset and the established profile-character binding filename;
relative paths match runtime composition. No agent package is imported in production.

ProcessingService.definitions lists bounded verified job pages. ReviewService.export
adds opt-in latest decision discovery while preserving the previous pending-only
default. LibraryService.has_seed queries existing origin dependencies before seeding;
any historical copy is retained without automatic refresh or resurrection. These
helpers are read-only and require no schema/version change.

Interactive approval passes the exact hash-verified candidate and original quotes to
the existing confirmed-user ReviewService.apply. No terminal text mutates claims,
confidence, policy verdicts or evidence. Accept-all acts only on pending supported
candidates with explicit confirmation; prior decisions remain immutable audit history.
Seeding requires its own affirmative response and commits one selected approved
snapshot per deterministic receipt. Interrupted review/seeding can resume. Source and
model pins remain immutable; source changes require an explicitly new snapshot.

Synthetic acceptance covers real import/extraction/review/seed/index composition,
reopened recall/evidence, default-no and reject behavior, paged bulk approval, old
manual seeds, interrupted review and index repair. No private data is processed.
[Root ADR 0009](../../../docs/adr/0009-phase-six-extraction-and-background-inference.md)
owns application coupling and phase delivery; the root runbook owns operator commands.

Validation: 442 tests passed, eight optional real-service/model tests skipped; all 14
interactive acceptance tests passed. Ruff format/lint, strict Pyright, installed
ingest help and git diff checks passed. Tests used synthetic sources only.

## Read historical extractor specifications, execute current code (2026-10-08)

ExtractionSpec accepts known persisted extractor identities 0.1.0, 0.1.1 and 0.1.2,
with references retaining each actual version. Previously the 0.1.2-only literal
misclassified intact historical job payloads as corrupt during paged discovery.
ProcessingService creation/resume and GatewayExtractionModel execution reject outdated
identities explicitly. Historical metadata remains readable for discovery/review,
without silently repinning source, schema, prompt, policy or model. The current
ClaimBatch schema hash is unchanged; no migration or canonical rewrite is needed.
Root ADR 0009 records delivery and synthetic historical/current regression coverage.
