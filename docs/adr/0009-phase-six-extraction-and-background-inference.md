# Root ADR 0009: Phase 6 extraction and conservative background inference

Date: 2026-10-08. Status: implemented and validated; operator architecture review pending.

1. Behavior: structured model extraction, exact evidence validation, durable resumable
   jobs, independently versioned conservative decisions, and a saved-memory summary
   adapter. Provide CLI review/approval/seeding so the operator runs personal imports.
2. Ownership: memory owns sources/candidates/jobs/policy; agent owns the same inference
   gateway and realtime session ownership. Provider transport stays behind a Protocol.
3. Reuse: canonical archive writer/evidence, candidate/approval/seeding models, outbox,
   bounded workers, generation gateway and separate library/character scope.
4. New primitives: immutable source windows, durable checkpoints/failures/review
   decisions, extraction model port, summary source adapter, bounded P3/P4 queues and
   conservative realtime reservations. No second scheduler or agent dependency in ABI.
5. Contracts: only completed structured output commits; window/candidates/checkpoint
   are atomic. Original spans remain evidence; summaries are attributed and cannot
   independently meet automatic factual confidence. Model output cannot confer actor,
   ownership, approval, grant or activation authority. Re-extraction cannot rewrite
   accepted/manual beliefs or bypass suppression. Source/model/policy versions pin jobs.
6. Failure: interrupted pure model steps retry explicitly from the last checkpoint;
   completed windows are not replayed. Malformed output, altered source/model pin,
   revoked scope and uncertain gateway release fail closed with safe persisted codes.
   No partial output, hidden reasoning or source contents enter ordinary logs.
7. Acceptance: summary text/JSON preservation and replay, Unicode spans, modality and
   source authority, policies/conflicts/duplicates/rejections, resume/failure/cancel,
   source suppression, bounded queues, separate credentials, connected-session block,
   safe quiescence, buffered JSON, and synthetic extraction→review→seed→restart→recall.
   Real pinned Qwen extraction is tested on synthetic data; personal archive extraction
   remains an operator-run workflow and is not implementation acceptance.
8. Implement, run the full quality gate, document exact operator commands, update PLAN
   and component ADRs, then stop for architecture review before Phase 7.

Memory ADR 0007 owns detailed processing design; Agent ADR 0004 owns scheduling and
session lifetime. The operator workflow is in [the Phase 6 runbook](../phase-6-runbook.md).
Phase 5's measured performance gates remain open.

## Delivered architecture and coupling

```text
summary CLI -> summary adapter -> existing archive writer -> source library
extract CLI -> ProcessingService -> ExtractionModel Protocol -> local gateway HTTP
  -> agent-owned P3/P4 admission -> existing pinned Ollama adapter
complete output -> exact original evidence -> immutable candidates/policy decisions
operator review -> existing library approval -> independent character seed -> index update
character core -> existing bounded memory client -> recalled approved memory -> P0 reply
```

The memory core owns SQLite, provenance, policy and resumable workflow, while the same
agent gateway owns GPU admission. The optional memory HTTP adapter consumes a narrow
versioned gateway protocol; it has no Python import of ember-agent. No native generation
route was added outside the agent's existing Ollama adapter. The character/realtime core
continues using public memory and model contracts. Importers continue depending on the
archive ImportTarget Protocol, with no concrete engine, agent or HTTP dependency.

Canonical source paging/suppression keys and selected graph traversal were factored
into shared engine helpers, so extraction does not depend on a built search generation
or a duplicate graph interpretation. Extraction adds no work to ordinary recall beyond
existing accepted-memory/outbox changes. CPU embeddings are built explicitly after
seeding; source import and proposal generation do not create a character's embeddings.

Engine 0.4.0 / schema 0.4.0 (user_version 4), ABI 0.3.0 and gateway wire 0.2.0 are
experimental. Archive and bundle formats remain 0.1.0. ABI 0.3 allows the planned
modalities on episode writes; unknown fact semantics still fail. Schema 1/2/3 upgrades
retain consistent backups and roll back failed DDL. Job/window headers are immutable
at the SQLite boundary while status/attempts remain operational mutable state.

Full source bundles retain processing/review history and resumable jobs. Independent
seeds copy only referenced extraction/policy definitions. Selective bundles retain that
specification closure and selected exact evidence, omitting private bulk jobs/candidates/
review logs. They explicitly remain partial sources and cannot resume a full import.

## Policy, failures and review limits

### Extractor 0.1.1: exact disambiguation coordinates

The first operator-run archive sample exposed a contract mismatch: the prompt requested a
`quote_start`, while the structured schema allowed the model to omit it. A repeated quote
then failed closed as `quote_missing_or_ambiguous`. Extractor 0.1.1 makes this field
required-but-nullable, asks the model to prefer a quote unique within its bounded window,
and requires an exact zero-based Unicode codepoint offset when that quote repeats. The
host still derives unique positions itself and verifies every supplied repeated-quote
position against the exact source text. Ambiguous or incorrect evidence remains a safe
failed step; no fuzzy matching or candidate weakening was introduced. Prompt/schema hashes
pin this revision, so an earlier job is not silently reinterpreted and a new key is needed.

This adds no runtime dependency or ownership change: the existing `AtomicClaim` schema,
versioned prompt, exact-evidence validator and resumable job contract own the behavior.
Acceptance covers missing required coordinates, exact repeated-quote resolution and
rejection without disambiguation. The original failed private archive job remains an
immutable diagnostic record; the corrected run uses a new pinned job.

The next archive sample showed a second schema gap: `predicate`, `value` and
`entity_name` were optional for every claim, so Qwen could label entries as facts while
omitting all fact semantics. Extractor 0.1.2 uses a discriminated fact/non-fact schema.
Fact output requires a nonempty predicate, explicit nullable value/entity fields, and
host-side validation of scalar versus `person.knows` shape. Non-fact output requires its
fact-only fields to be null. Unsupported but complete predicates remain proposals for the
existing conservative policy to reject from approval. This keeps generic model claims
untrusted while making the output schema express the rules already enforced by the engine.
The new schema/prompt hashes again require a new job key.

The initial policy is immutable, independently versioned and hash-pinned by jobs, with
confidence/importance thresholds, compatible semantics, source authority and ambiguity/
duplicate/conflict/rejection checks. Model support/confidence are untrusted inputs.
Summary confidence remains capped at 0.79; actual personal activation requires explicit
operator review of original quotes. Manual overrides and initial policy reasons remain
separate, preserving original support strength. Reevaluate appends changed decisions;
it does not rewrite accepted beliefs. Human corrections use the existing author/update
interfaces; re-extraction proposes new candidates and respects current suppression.

Within each pure step, staged entities, candidates, policy records and completed
checkpoint commit together. Model output is never checkpointed before completed JSON
validation. Failure records contain bounded safe codes/ordinals, not exception bodies.
Completed windows are skipped on resume; failed windows require explicit retry. One
root owns one processing runner and its bounded worker, retaining ownership through
cancellation drain. CLI maintenance cannot run concurrently with the canonical runtime.

Review validates the entire selected page before writes, then commits per candidate
using stable approval receipts; it is not a whole-page transaction. A rejection never
implicitly forgets an approved belief. Exact proposal fingerprints flag duplicates and
single-valued predicate conflicts; similarity-based merging, cross-source consolidation,
valid-time correction and supersession belong to Phase 7. Operator confirmation is the
implemented summary acceptance path; automatic independent corroboration is not inferred
from duplicate model runs or generated summaries. No corpus-wide extraction completeness
or claim-accuracy guarantee is made by a job's completed-window count.

The gateway queues eight pending requests per P3/P4 class, alternating classes with FIFO
within each. Realtime ownership blocks background work for the whole connected terminal/
LiveKit host lifetime, including idle time. Startup reserves first, parks pre-generation
metadata admission or waits for an already running generation to drain. Native cancellation
and live preemption remain unproven/disabled. A subscriber disconnect removes queued work;
active work keeps the runner slot until actual completion. JSON is buffered and validated
against its schema before publication. Durable session state survives restart and journal
failure closes admission. Recovery acknowledgement remains an explicit operator assertion.
P1/P2/full scheduling/accounting and measured preemption remain Phases 9/13.

Structured schema output uses the existing native adapter's `format` field plus host-side
validation, following [Ollama's structured-output contract](https://docs.ollama.com/capabilities/structured-outputs).
The provider supplies constrained generation, while canonical memory owns semantic/evidence
checks. Neither provider schema conformance nor model verdict grants write authority.

## Acceptance evidence

The final root gate passed **408 tests with no skips**, on Windows/Python 3.12.15,
with actual pinned non-MTP Qwen, CPU BGE and installed LiveKit enabled. Ruff formatting,
lint and strict Pyright passed. The real synthetic extraction test performs P3/Qwen
extraction, explicit review/approval, independent seed/index build, restart, semantic
search and a streamed Qwen reply through the actual LiveKit agent/LLM hook. Existing
actual server/room tests also pass. This is tested model/transport integration on
synthetic data, not real personal claim accuracy/adoption or a voice-room claim.

Coverage includes exact summary replay/CRLF/Unicode, reasoning exclusion, malformed/
incomplete output, isolated contexts/model pins, modalities/unsupported semantics,
known conflicts/duplicates/rejections, checkpoint/failure/cancel/rollback, policy
pinning/reevaluation, source suppression before/after extraction, schema 3 failed-DDL
recovery, full source-job restore, selective specification privacy, queue/session
races, credential scope, restart state and journal failure. All three offline lockfile
checks and generated schema verification are recorded with delivery.

The official private ZIP was not reimported. Three operator-run jobs over its existing
library each planned 26,779 windows. The original contract completed one window without
proposals, then failed twice at exact quote disambiguation. Extractor 0.1.1 corrected that
prompt/schema mismatch. Its sample also completed one window without proposals, then
failed twice because facts omitted predicate/value fields. Extractor 0.1.2 closes that
schema gap. Its five-window sample completed with four proposals and no failed windows;
the exact-evidence review file is available locally and all four decisions remain pending.
No approval or seeding has occurred. Phase 5 performance/capacity/Linux gates remain open.
No corpus-wide extraction completeness, voice integration, ABI 1.0 or preemption
acceptance is claimed. Stop for architecture review before Phase 7.

## Initial quote-failure recovery controls (2026-10-08)

A long operator archive run exposed the distinction between selecting old failed
windows and retrying new failures. Default fail-fast behavior remains unchanged.
The engine runner now accepts bounded window attempts (1-3) and optional continuation
for `quote_missing_or_ambiguous` and `quote_not_exact` only. Both errors occur before
the atomic candidate commit. Infrastructure, pin, source-access and other failures
still stop; no evidence validation is relaxed and no proposal is automatically adopted.

Every failed attempt is recorded. Exhausted windows remain failed while later windows
can commit; the final job cannot report complete with unresolved gaps. Window limits
bound distinct attempted windows. CLI progress includes unresolved failure counts.
Execution controls do not alter model/prompt/schema/policy pins, so existing jobs can
resume without a new key. Tests cover transient recovery, exhausted failures, atomic
rollback, bounded work, retained audit history, restart recovery and nonrecoverable
failures. Validation of this recovery change: 407 tests passed, eight optional
service/model tests skipped; formatting, lint and strict Pyright passed. The
extraction suite passed all 41 tests. No private job was run during these checks.
Component detail is in
[Memory ADR 0007](../../ember-memory/docs/adr/0007-extraction-jobs-and-summary-sources.md).

## Expanded window continuation and execution outcome (2026-10-08)

The operator clarified that --continue-on-error must finish the requested extraction
batch despite bad model outputs, rather than repeatedly requiring manual restarts.
The initial quote-only policy is extended to rejected claim/JSON-schema outputs and
suppressed/unsupported window sources. Output retries remain bounded (1-3); source
skips do not retry or run inference. No failed-window-count threshold stops execution.

Gateway output rejection is separated from transport/protocol failure. Invalid model
JSON/schema and the gateway's known structured-output rejection codes are window-local;
unknown generation, identity/protocol, pin, transport and canonical storage failures
remain explicit stops. Original evidence, atomic commits and approval authority are
unchanged. Unknown failures are never silently ignored.

The CLI returns 0 after a continued batch finishes, even with unresolved windows.
Job status remains failed until all gaps are recovered. A transient run_outcome
distinguishes finished_with_failures from stopped_on_error without rewriting durable
job state. No extraction identity, ClaimBatch schema, database migration or new job
key is needed; existing running processes use this behavior on their next invocation.
Tests cover more than three distinct failures, schema rejection through the gateway,
source skips without inference, persisted gaps, restart and CLI exit behavior.
Validation: 424 tests passed, eight optional service/model tests skipped; all 64
processing/transport tests passed. Formatting, lint, strict Pyright and diff checks
passed. The existing ClaimBatch schema hash was verified unchanged. No private
extraction, approval or seeding was run during validation.

## Planned Phase 7/8 acceptance clarification (2026-10-08)

The operator requested automatic memory capture from ordinary conversation without
/remember and independent recall controls for imported ChatGPT versus local memories.
[PLAN Phase 7 acceptance](../../PLAN.md#phase-7-acceptance-criteria-native-activation-and-consolidation)
assigns native policy acceptance, consolidation and origin preservation to the memory
engine. [PLAN Phase 8 acceptance](../../PLAN.md#phase-8-acceptance-criteria-automatic-capture-and-memory-source-controls)
assigns automatic scheduling, application controls and end-to-end recall tests to
application integration. These are planned requirements, not shipped Phase 6 features.

Imported personal claims keep their operator-review boundary. Automatic native
acceptance uses evidence/policy checks and does not require library seeding. Recall
selection must exclude seeded/derived memories by origin, not just revoke archive
grants, and must respect suppression and invalidate known memory-dependent context.
Conservative background admission remains unchanged until measured Phase 13 acceptance.
Component ADRs will record the concrete contracts and implementation in those phases.

## CLI JSON output and PowerShell ownership failure (2026-10-08)

A concurrent summary import correctly failed because the archive extractor owned
the canonical root. Its diagnostic was previously printed on stdout, so direct
ConvertFrom-Json parsing obscured the real error and subsequent pasted commands
received empty store/job IDs. Summary/extraction diagnostics now use stderr.
Runbook examples check the native exit code before parsing JSON and require dependent
steps to stop after failure. The root's single-owner lock and gateway admission
remain unchanged; no running import is interrupted or lock removed by this fix.
Regression coverage verifies busy-root and invalid-input failures leave stdout empty.
Validation: 427 tests passed, eight optional service/model tests skipped; all 55
processing tests passed. Formatting, lint and strict Pyright passed. The running
private archive extraction was left untouched; no summary import was executed.

Review export also reports an existing output file explicitly instead of masking
FileExistsError as generic invalid state. The runbook opens the editor only after
successful export so a failed export cannot leave an empty editor-created review.
Existing reviewed decisions are never overwritten. The 56 processing tests and
format/lint/strict-type checks passed, including preservation of edited review files.

## Interactive Phase 6 maintenance (2026-10-08)

The operator requested one command for ingestion, candidate decisions and seeding.
`ember-memory ingest` composes the existing services in the memory CLI, retaining a
single exclusive canonical owner. It reads a narrow deployment-v1 configuration view
to select the same character/data/model/token/CPU assets as Ember. It has no production
dependency on ember-agent and does not start services or alter runtime configuration.

Desired behavior is import/select source, create/resume a pinned snapshot, individually
accept/reject/skip or explicitly decide all pending candidates, separately confirm
seeding, then update the configured index. The memory engine retains ownership of
evidence, approvals, character snapshots, audits and indexing. Existing importers,
ProcessingService, ReviewService and LibraryService implement mutations; new terminal
orchestration is a CLI primitive, not a new memory policy or state machine.

Three read-only helpers provide paged job discovery, opt-in latest review decisions,
and historical seed-origin discovery. Default JSON review behavior remains unchanged.
Bulk decisions preserve previous rejects/approvals; unsupported writes remain pending.
Each confirmed decision commits immediately. Each seed uses a deterministic receipt
and existing provenance checks; repeated maintenance leaves old independent snapshots,
including forgotten copies, unchanged. A separate default-no seed confirmation is
required even after accepting all. Partial extraction coverage requires explicit review
confirmation. Index failure does not roll back canonical seeds.

Known window-local extraction failures continue with bounded retry. Pin/protocol,
transport, storage and ownership failures stop; no live writer is displaced.
Quit/EOF/Ctrl+C retain checkpoints. Untrusted text is escaped on display and interactive
maintenance rejects redirected terminal streams. No database migration, ABI, extractor
prompt/schema change, dependency addition or automatic claim adoption is introduced.

Acceptance tests use synthetic temporary stores and a mocked gateway transport with
the real extraction adapter: full import through restarted indexed recall, failed-window
coverage, individual/bulk decisions, >100 candidates, prior manual seeds, durable quit,
default-no seeding, index repair and busy-root/no-terminal behavior. Component detail:
[Memory ADR 0007](../../ember-memory/docs/adr/0007-extraction-jobs-and-summary-sources.md).
Operator commands: [Phase 6 runbook](../phase-6-runbook.md#interactive-ingestion-review-and-seeding).
This extends Phase 6 usability; Phase 7/8 acceptance requirements remain planned.

Validation: 442 tests passed, eight optional real-service/model tests skipped; all 14
interactive acceptance tests passed. Ruff format/lint, strict Pyright, installed
ingest help and git diff checks passed. Tests used synthetic sources only.

## Historical job discovery compatibility (2026-10-08)

Interactive library selection initially attempted to parse all historical extraction
jobs using an extractor literal restricted to 0.1.2. Valid hash-protected 0.1.0/0.1.1
definitions therefore raised a misleading canonical-integrity error and blocked
selection of a current job. Read-only inspection found intact SQLite and payload
hashes; no private database repair or mutation was required.

The persisted specification contract now reads the known historical identities and
references their actual versions. New job creation and resuming incomplete processing
require the current 0.1.2 extractor; the gateway also verifies this identity before
generation. Historical source/job pins, bytes, receipts and progress stay unchanged.
Unknown identities and corrupt payloads still fail validation. There is no database
migration, ABI change or ClaimBatch output-schema/prompt change.

Synthetic regressions cover mixed historical/current discovery, exact stored bytes,
actual version references and refusal to create/resume outdated processing. The
selected private library's four job definitions were verified through read-only SQLite
access; no extraction, candidate review, approval or seed was executed.
