# Root ADR 0015: Phase 8 implementation and acceptance boundaries

Status: implementation in progress in the independent ember-phase-eight clone.
Base: origin/master 640888d59ffaa0f501b7453e161fd62c9e945ab6.
Date: 2026-10-09. Phase 8 is not complete or deployed.

## Decision and ownership

The approved [implementation proposal](../phase-8-implementation-proposal.md) remains
the scope. Extend the existing canonical owner, processing jobs, ABI, character core
and local composition. Add neither a database nor an inference scheduler.

The engine owns persisted recall selection, current authorization, SQL eligibility,
native checkpoints and exact date evidence. The agent owns conversational calendar
interpretation, prompt/history selection, delivery and service lifecycle.

Recall selection is independent of capture, import approval, grants and model identity.
Origin selection narrows eligibility before candidate limits, including profiles,
pins and archive search. Settings and policy epochs commit atomically under confirmed
user authority with revision checks; bundle restore preserves the controls.

Ended episodes describe occurrence intervals and remain recallable. Current-state
facts retain validity checks. A historical dependency carries its original selector.
Validation reuses canonical eligibility without hydration or similarity search;
current suppression, forgetting, grant expiry and origin controls still apply.
Current pins retain current eligibility. Dependency limits remain 256 references
combined and 64 KiB, with at most 32 temporal groups of 100 references each.

Native delivery and its capture checkpoint share the archive transaction. Bounded
incremental jobs select exact owned user messages. Old JobRequest serialization and
extractor 0.1.2/0.2.0 hashes remain unchanged. Capture reuses native acceptance receipts,
and repeated acceptance cannot duplicate memories. The runtime releases its realtime
reservation before bounded P3 consolidation; the gateway remains final GPU admission
authority. Realtime connected-session extraction is not enabled.

Extractor 0.3.0 has a separate dated schema and prompt. It accepts exact supporting
calendar expressions, not model-generated timestamps. Source-relative dates use the
original observation clock. Unknown dates stay unknown; hypothetical/planned content
cannot become a completed episode. Existing jobs are never repinned.

## Versions and scalability

Experimental ABI 0.5.0, engine 0.6.0 and schema 6 introduce these contracts. A backed-up
additive schema-5 migration skips prior projection backfills. Index layout 0.6.0
distinguishes episode occurrence from state expiry; old indexes require canonical
eligibility or explicit rebuild rather than silently changing their vector filters.

Default current recall retains the existing certified projection path. Restrictive
origin policies use canonical eligibility. Temporal parsing is local and bounded by
the existing 8192-character query limit. Date-only recall is structured; topical
calendar queries reuse the existing lexical/vector search. Measure restrictive and
historical paths separately; no constant-time claim is made for corpus-sized vectors.

## Evidence and open gates

[Validation status](../phase-8-validation.md) records only executed tests.
Root ADRs [0010](0010-planned-deep-associative-recall-and-phase-acceptance.md) and
[0014](0014-planned-conversational-temporal-recall.md) retain planned acceptance.
[Memory ADR 0009](../../ember-memory/docs/adr/0009-phase-eight-scope-and-temporal-capture.md)
and [Agent ADR 0006](../../ember-agent/docs/adr/0006-phase-eight-calendar-and-capture.md)
own component decisions.

Full browser/voice acceptance, supported event/device anchors,
complete contention/quality measurements and requested code-review remain open.
Reviewed manual batch enrichment is now implemented; its full library/seed isolation
matrix remains open. No real Qwen/audio acceptance has run while
the primary ingest owns the GPU. Synthetic tests cannot replace those measurements.

## Runtime and explicit deep recall implementation

Browser controls use an application-owned loopback FastAPI server and the existing
runtime owner. Private file/fragment activation creates an HttpOnly SameSite cookie;
only room-scoped participant capabilities reach the browser. The TypeScript/React
client connects to the existing SDK room. It never receives gateway/server secrets
or opens a canonical store. Build dependencies and assets belong to explicit setup.

DeepRecallService is agent orchestration over the public ABI. Its read-only
request handle copies character/retriever state but reuses worker pools, encoder,
completed query embeddings and immutable generations. One active investigation
and bounded queues own cancellation through native completion. Deep work never
starts an uncached encode; a cold generation cannot initiate an unbounded checksum
read and instead uses canonical eligibility. SQL statements, rows and materialized
bytes are actually metered, with a five-second deadline including admission.
These implementation bounds still require working-memory and contention evidence.

Related reads return exact source passages and typed existing relationships.
Independent shared topics, dates, users and source records cannot merge incidents.
Inspector results are ephemeral, context-bound and revalidated on access. Final
answer context uses deterministic whole qualified rows within existing memory
and excerpt categories, not a generated summary or full result JSON. No canonical
write or grant occurs. Raw archive fallback uses original user/document observations:
generated native answers could contain text copied from subsequently disabled
sources and therefore must not act as an unrestricted recall cache.

CPU whisper and Silero are optional installed providers; local Kokoro uses the
existing OpenAI-compatible speech adapter. Text works when speech is unavailable.
An SDK playout observation acknowledges the original request's delivered prefix;
generated/unheard suffixes never enter history or the native source archive.
Input observation precedes generation so a failed answer cannot lose user input.
Current implementation validates synthesized frames; complete buffered-transport
revocation is still an explicit acceptance gap.

One application-owned connection task coalesces join/disconnect events. It drains
the transport before releasing the existing gateway reservation. Reconnection
first reserves realtime capacity; a continuation predicate prevents the next P3
step without cancelling the upstream operation or repinning an old extraction job.
Connected-session silence never starts consolidation. This extends the existing
lifecycle and processing loop, not inference scheduling or writer ownership.

Optional CalendarInterpreter defaults off independently of deep recall. Only an
unresolved ordinary calendar request may issue one schema-constrained P0 call,
with a capped output/deadline and exact current-input support. Explicit years,
requested axis and normalized calendar precision are checked locally; unsupported
event/device anchors require clarification. Added serial latency is measured
separately and has not yet been measured with Qwen.

## Integration update: 2026-10-13

Reuse the existing capture ledger, processing jobs and continuation predicate. A
bounded idle batch signals another batch when runnable work remains; failed
checkpoints stay inspectable, are excluded from automatic draining and require
explicit retry without repinning their job. This avoids both starvation of later
sources and an idle retry loop. Browser processing/index status is demand-refreshed
aggregate data, not another per-turn worker or observer.

Reviewed enrichment accepts at most 64 revision-pinned proposals per file. Each
item uses existing proposal review, CAS and mutation receipts; one conflicting
item does not undo completed items, and replay reports already-applied. Selected
bundles retain review history only when the proposal's exact evidence closure is
already included. Restoring schema-4/5 exports uses existing backed-up migrations.
No library update silently mutates a seeded character.

Deep raw observations may complement accepted memories inside explicitly authorized
source/conversation neighborhoods. Their archive capability remains separate from
accepted evidence-read capability. Current revocation of either invalidates the
combined result. Conflict associations are explicit but do not by themselves unite
independent incidents. Source-only snippets never become accepted beliefs.

Managed Kokoro uses the existing installed-command/OwnedProcess lifecycle after
CPU STT readiness; child-only CUDA visibility/offline guards preserve the parent
gateway environment. Missing artifacts degrade speech without launching an unused
server. Cancellation owns and drains input initialization before releasing loaded
STT resources. A crashed permission validator fences the room before draining
the session; a dead watcher cannot leave dependent output running.

Browser close aborts bounded transcription streams, drains owned readers and
removes unsubscribed audio. Pending room authorization cannot reopen a disposed
connection; unexpected disconnects clear controls. Status history remains bounded
while an older investigation is active. These use SDK cancellation and existing
task ownership, without another transport manager.

Measured ordinary read p95 is 14.73 ms disabled and 15.27 ms with attempted deep
work in the October 13 synthetic guard run. All 292 investigations deferred.
The earlier ungated run failed regression limits and is retained. Thus concurrent
completed deep semantic mode remains unavailable. Separate nonempty temporal
warm retrieval/validation/formatting measurements now exist, but full quality,
large-corpus/refresh/RSS and real inference/audio gates remain open. See the
[acceptance tracker](../phase-8-validation.md) for executed evidence and limits.

The warm curated NAS probe now selects eight expected dated, entity-annotated
memories with eight exact supporting passages and independent groups. This is one
synthetic retrieval case, not broad paraphrase/imported-corpus or Qwen reconstruction
acceptance. Existing RankingExplanation records related inclusion; inspector ordering
and source links match exact span coordinates/hashes rather than just source IDs.

A missing background token is a blocked processing outcome. Returning a positive
pending count without progress would cause the idle continuation to spin; the
focused test proves one attempt and a durable queued checkpoint. No polling/retry
scheduler is introduced. Reviewed enrichment also has a focused library/two-character
test: later library approvals and seed replay do not overwrite an enriched character
or mutate another character, an old approval or an existing job.

The agent also reuses authorized non-generic ResolvedEntity references to include
entity-valued facts. Payloads containing only IDs cannot pass a literal-name test,
so requiring that test alone would discard valid related results. No new alias
table, graph expansion or hydration call is introduced. Current parser timing is
separate: roughly 0.02 ms warm p95 for common single scopes and 0.064 ms for the
two-period range case; this is not final-answer or interpretation latency.
