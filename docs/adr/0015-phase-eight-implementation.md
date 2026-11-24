# Root ADR 0015: Phase 8 implementation and acceptance boundaries

Status: experimental implementation in progress; main-device text/voice startup has functional evidence.
Base: origin/master 640888d59ffaa0f501b7453e161fd62c9e945ab6.
Date: 2026-10-09; implementation/review updates through 2026-10-16. Phase 8 is not complete.

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
and repeated acceptance cannot duplicate memories. The gateway remains final GPU
admission authority. The original disconnect-only lifecycle is extended by
[connected-idle native capture](#connected-idle-native-capture).

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

## Runtime and conversational deep recall implementation

Optional `natural_deep_recall` routes specific, directly phrased memory questions
from voice and text to the existing investigation before reply preparation. No
extra model call, broader memory access, or change to ordinary recall ranking.
See [component ADR 0006](../../ember-agent/docs/adr/0006-phase-eight-calendar-and-capture.md).

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

The original disconnect-only lifecycle coalesced join/disconnect events in one
application-owned task. It drained the transport before releasing the gateway
reservation. Reconnection reserved realtime capacity and paused new P3 steps
without cancelling the running operation or repinning old jobs. The
[connected-idle amendment](#connected-idle-native-capture) reuses this owner.

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


## Main-checkout launch convenience: 2026-10-13

A root start.ps1 wrapper resolves the configured deployment and installed root
executable, then delegates to ember start --open-browser. Service policy, character
binding, memory ownership and shutdown remain in the existing Python composition.
BrowserServer opens its exact ready activation URL off-loop through the OS browser;
no new daemon, watcher, token transport or engine owner is introduced. A disabled
browser fails before service startup, and desktop opening failure retains the runtime
without logging the activation capability. Existing explicit configuration and
setup-only dependency/frontend installation rules remain unchanged.


## Installed voice and browser corrections: 2026-10-14

The configured main-device runtime now starts installed CPU Whisper/Silero inputs
and managed Kokoro-FastAPI/af_alloy with the existing owned-process lifecycle. The
provider remains an ignored installed tool with its own uv environment, not a third
product or vendored agent framework. Explicit setup pins source/assets; runtime only
loads/starts them. Child-only offline, CPU, OMP/MKL and warning-log guards preserve
Qwen's GPU environment. Health narrows both legacy voice names and current voice
objects, matching exact IDs rather than display labels.

Two actual browser faults required corrections beyond native-room tests. LiveKit's
dependent agent expired the room without a human, leaving stale discovery. The
explicitly hosted participant is now standard with unchanged room-scoped media/data
limits; the runtime remains the room's deletion owner. Native tests wait beyond short
empty/departure timers and rejoin. Forced loopback node advertisement also failed
browser peer connection on Windows. Let LiveKit select host-local media addresses
and enable loopback candidates; control/signalling remain bound to loopback. Existing
media listeners can use local interfaces and still require signed capabilities/DTLS.
No browser security flag, firewall change, TURN service or keepalive worker is added.

The SDK publishes microphone transcription from the hosted participant. The frontend
recognizes its own current microphone track ID, accepts only final user snapshots and
avoids duplicate transcript text. Audio autoplay refusal preserves text connectivity.
Connection errors identify authorization, join and worker readiness without leaking
provider credentials. A disconnected composer explains how to enable Send.

Delivery cleanup still precedes new input observation. Gateway reservation follows
the durable observation through one additional callback in existing composition;
admission failure/cancellation cannot lose a completed original input or authorize
inference. The core's single pending-delivery invariant remains intact. No extra
memory lookup, scheduler, worker or per-turn profile machinery is introduced.

Installed-browser acceptance in an isolated synthetic character passed typed and
microphone input through accepted-memory recall, the single Qwen gateway, Kokoro and
received audio RTP. See the aggregate speech/browser report and acceptance tracker.
Physical microphone/headset, broad quality/scale, buffered-revocation and other Phase 8
release gates remain open. Doctor now distinguishes configured/unprobed speech and
browser from actual health rather than retaining obsolete Phase 3 labels.


## Large private-corpus deep recall correction: 2026-10-14

The running browser API reproduced resource exhaustion despite finding candidates.
Profiling an isolated SQLite backup of 12,563 accepted character memories identified
repeated original-branch walks during excerpt validation. Each excerpt reloaded the
conversation and preceding nodes, spending thousands of statements on shared context.

Reuse the existing deep read profile and source resolver. Add an optional engine-local
SourceBranchResolver capability, implemented only by DeepSources. The engine uses exact
selected-path keys in one parameterized SQLite lookup and verifies the matching node;
missing earlier nodes preserve ambiguity. Paths above 1,024 nodes remain explicitly
unavailable. There is no new graph table, schema migration or public memory ABI change.
Ordinary source resolution retains its existing behavior. Cache verified immutable
source records only within one investigation, bounded by 256 entries and 2 MiB of
serialized payload. This cache is not an authorization cache: exact quote hashes and
fresh grants, suppression, selection and source headers are still validated.

Related lookup reuses a current revision already loaded for the same selected memory.
The agent retains the last completed dependency-certified checkpoint; subsequent
resource/time exhaustion returns only that checkpoint, qualified as partial. Optional
archive expansion cannot erase final validated memories. Inspection and answer delivery
continue current-policy validation; privacy failures and cancellation never use this
resource fallback. Content-free counters now identify the exhausted limit, and the UI
disables answering when no retrieved material fits the answer selection.

The five-second deadline, 4,096 SQL statements, 2,048 materialized rows, 16 MiB cumulative
read envelope, six-memory prompt ceiling and foreground deferral remain unchanged.
The measured fix removed redundant work rather than enlarging the execution envelope.
The same fourteen private queries over three rounds reduced budget failures from 30/42
to 0/42 and increased investigations containing memories from 9 to 39. The remaining
three are strict event-date queries with undated records excluded. Deep p50/p95 were
476/577 ms. Ordinary warm p95 was 42.89 ms disabled, 42.82 ms enabled idle and 43.74 ms
under competing attempts; each condition had 1,000 reads. Existing regression caps passed,
but all 638 contending deep attempts deferred. This is foreground-priority evidence,
not completed simultaneous deep semantic execution or a relevance-quality benchmark.

See [aggregate measurements](../reports/phase-8-deep-large-set-20261014.json),
[validation](../phase-8-validation.md), Memory ADR 0009 and Agent ADR 0006.
Historical measurements and outstanding Phase 8 acceptance gates remain unchanged.
## Independent review remediation: 2026-10-16

Review the current `ember` checkout, including existing uncommitted work, rather
than the historical development clone. The [remediation design](../phase-8-review-remediation.md)
records behavior, ownership, primitives, bounds and tests before implementation.
The [engineering review](../reports/phase-8-architecture-review-20261016.md)
distinguishes fixes from remaining acceptance evidence.

Native acceptance now checks original full-source qualifications even when a
model selects a shorter factual-looking quote. Existing receipts and job pins
remain immutable. Selected prompt certification uses the existing metadata-only
validation path and an opt-in negotiated flag; it reconstructs only required
evidence/grant/time dependencies after trimming. Public schema artifacts are
updated; default old serialization, storage versions and migration behavior remain.
Deep condensation removes current pins and omits uncertified raw observations.

Capture policy writes signal the existing lifecycle task so disconnected backlog
resumes without another room event. Browser startup can degrade to HTTP text over
the same character core, or controls only when the gateway is unavailable. Its
single 45-second renewable transport lease owns one generation and 128 replay IDs;
disconnect/expiry drains before reservation release. Buffered text is recorded only
after explicit acknowledgement. No new database, inference scheduler, upstream
fallback or install-at-startup path is introduced. Initial service/room failures
select fallback; established rooms are not silently replayed into another transport.

The full Phase 8 acceptance gates remain open. No production store was inspected
or mutated and no live service was restarted during this review.

## User-directed ordinary word recall: 2026-10-16

Following the separate review, the user reported missing durable recall and asked
for individual-word search over eligible memories with compact extracted values
in context. Read-only inspection confirmed existing accepted memories were present;
predicate-label matches and small candidate/prompt limits prevented useful recall.
See the [preimplementation design](../phase-8-word-recall.md).

The engine now prioritizes extracted-value word matches before the FTS candidate
limit and combines these with the existing single semantic lookup. Importance
orders otherwise comparable lexical values. Canonical fallback filters words before
its limit. Existing FTS/vector generations and source scopes remain valid; there
is no schema migration or automatic rebuild. The runtime ranking policy is exposed
as `ember.rrf-extracted-words@0.2.0`, separately from the generation's recorded ranker.

At the user's direction, ordinary context honors the requested ABI limit up to 100
total rows including pins, replacing the former six-row bound. The agent selects
whole extracted values into a quarter of its input guard, capped at 6,144 UTF-8
units. Working selection has a separate 65,536-byte bound. Subject/negation/modality,
intent/state and meaningful dates survive compaction; evidence and memory identities
remain in exact delivery dependencies. Oversized and duplicate displayed values do
not prevent smaller distinct values from fitting. Explicit deep recall keeps its
previous bounds. No new public contract, model inference or provider coupling is
introduced; the existing engine/composer responsibility boundary is preserved.

Sanitized real-engine/composer tests cover a name episode with a distinct subject
among noisy name predicates, individual Unicode words across all supported memory
kinds, relevance before limits, suppression/source exclusion, 8k/32k allocations,
oversized/duplicate values and delivery invalidation. Existing native/profile/import
policies are unchanged. No live generation, service restart or canonical mutation
was performed. Current config inspection identified an 8,192 context setting;
32,768 remains a deployment choice requiring the usual gateway restart. Previous
ordinary performance results do not certify the enlarged candidate bound.

The operator subsequently authorized the private 32k configuration and activation.
A backed-up atomic config update and graceful owned restart completed. Authenticated
HTTP text and normal LiveKit room checks both returned the expected saved-name answer
with 20 recalled memories. Final browser/gateway state reports LiveKit, voice/text
ready, a healthy journal and 32,768 context; the active model reports the same context.
The reported transient Connect-busy error was not reproduced during inspection.
No schema migration, index rebuild or imported-claim approval was required. See
the [activation evidence](../phase-8-validation.md) for scope and limitations.

## Browser turn details and composer: 2026-10-16

The user's [UI amendment](../phase-8-turn-details-ui.md) adds a Thinking toggle,
collapsed current-turn thinking/recall expansions, Enter/Shift+Enter behavior and
user-message scrolling before Phase 9. CharacterCore owns one ephemeral selected
view; the existing authenticated control API serves both LiveKit and HTTP clients.
It retains the final delivery certificate privately, validates current access on
every read and clears invalidated content. No additional search, model generation,
database or Phase 9 event infrastructure is introduced.

Experimental gateway wire 0.4.0 negotiates `live_thinking_v1` for plain P0 text only.
The configured reserve bounds output (maximum 8,192 tokens); default/older requests
retain their previous output bounds. Unrequested native thinking is discarded, and
native frame serialization always excludes it. Opted-in thinking reaches only the
ephemeral browser view, never speech, archives, history, prompts or diagnostics.
Memory ABI/schema/index versions and source activation are unchanged.

The view is bounded by 100 values, 64 KiB memory data, 32 KiB thinking and 128 KiB
serialized data including escaping. Browser reads are serialized at 500 ms while
connected and cancel on teardown. React escapes expansions. Keyboard handling
respects IME, repeat keys and duplicate sends; assistant updates follow only when
the reader remains near the bottom.

The installed full gate passes 673 Python tests (10 skipped), Ruff and strict types;
18 frontend tests, build/formatting and three isolated browser checks pass. Three
guarded live cases skip without their configured fixtures. Only synthetic display
markers appear in layout artifacts. The model declares thinking support via local
metadata; no real thinking was retained. Automatic approval review rejected the
proposed active restart because the earlier 32k approval covered a separate rollout.
Activation and live feature verification await new explicit authorization.

## Recalled plan display correction: 2026-10-21

Plan payloads store steps as an immutable Python tuple. The shared compact recall
projection now emits those steps as a JSON array before the ephemeral turn view
validates them. Previously even an empty tuple raised a validation error during
preparation; LiveKit surfaced it as an application error before generation. This
was more visible with the enlarged input budget admitting additional plan memories.
The change reuses the existing projection; the serialized model prompt is identical
and there is no new lookup, model call, persistence, migration or permission path.

Real-engine synthetic regressions cover empty and populated steps across two
completed turns, including identical prompt/display values and preserved proposed
state and PLAN modality. The exact reported query also reproduced the failure on
the existing isolated large-set snapshot at the deployed input budget. After the
fix it prepared twice with 20 recalled memories, including four plans, and valid
turn-view JSON. No private values are recorded here and no model generation or
live character mutation was needed. The 27 focused tests, Ruff and strict Pyright
pass. The active process must be restarted to load the correction.

## Voice room startup and RVN context, 2026-11-03

LiveKit rooms have a 30-second empty timeout. Prepare local speech providers
before creating the room, then join with the owned host immediately. Previously
the room could disappear during speech startup and both signalling paths returned
404. The fallback browser text transport remains available when voice cannot start.

The local launcher configuration requests 131,072 context tokens for the pinned
RVN IQ3_S MTP model, with 126,976 input units and 4,096 output reserve. A short
isolated generation verified the runner context and 100% GPU placement. An
isolated startup with installed LiveKit, Whisper and Kokoro reported voice ready.
These checks establish startup and short generation. Long-prompt and physical
microphone performance were outside this run.

### Gradium Phonon spoken output

The workstation's default TTS uses the Apache-licensed Phonon 31M English
q8 checkpoint on Windows CPU. A loopback server streams 24 kHz mono PCM
through the existing LiveKit TTS adapter. The legacy service key retains
configuration compatibility. Startup uses provisioned local weights.
An isolated managed service check generated 55,920 samples through the
actual LiveKit TTS adapter. See the phase runbook for the pin and rollback.

## Reverse-proxy LAN access

The browser runtime can explicitly trust one private IPv4 Nginx peer and an HTTPS public origin. On that origin, loading the page issues the existing operator session without a copied activation link; access therefore grants full operator authority. Exact host, peer and HTTPS-forwarded checks precede admission, and state-changing requests retain exact Origin checks. Nginx owns TLS and secure LiveKit signalling, while the runtime and memory owners remain unchanged. The detailed configuration, network boundaries and open end-to-end gates are in [the LAN HTTPS design](../phase-8-lan-https-design.md).

## Long speech correction

The VAD's padding made its previous 30-second buffer exceed the recognizer's
30-second limit. The agent now retains up to five minutes plus padding and runs
bounded 30-second recognizer chunks in the existing owned worker. The installed
VAD still discards speech beyond that buffer until a pause. See the
[design and limits](../phase-8-long-speech.md); no model gateway or memory
ownership changes.

## Managed CPU Phonon speech input

User-directed deployment selects Phonon-2 speech input on CPU. Explicit setup
provisions the runtime and pinned model; startup uses an offline owned service,
validates the model and CPU health, and degrades voice on failure. Existing
Whisper remains available as an explicit rollback. The primary deployment was
started with Phonon-2 CPU and two threads; the browser reports voice ready.
See the [service design](../phase-8-long-speech.md) and validation record.

## Mobile browser layout

The existing frontend reflows the header, conversation and controls for narrow and
short screens and exposes semantic names and microphone state. The
[mobile design](../phase-8-mobile-ui.md) records synthetic browser checks and the
remaining real-device acceptance. Runtime, memory and provider boundaries are unchanged.

## Memory graph browser

The workspace links to a read-only graph of the active character's current
eligible memories. Composition connects the authenticated browser route to a
bounded engine read without expanding the public ABI. The canvas starts with
1,000 eligible memories. Load more adds 1,000 and Load all continues from the
same cursor until the query is exhausted. Search and type filters apply across
the eligible store. See
[Agent ADR 0006](../../ember-agent/docs/adr/0006-phase-eight-calendar-and-capture.md#memory-graph-browser)
and [Memory ADR 0009](../../ember-memory/docs/adr/0009-phase-eight-scope-and-temporal-capture.md#memory-graph-browser).

Hindsight's [link expansion](https://github.com/vectorize-io/hindsight/blob/80bd005fc2be5c9604a1a0c9741ea93d5ac85156/hindsight-api-slim/hindsight_api/engine/memories/pg/link_expansion.py)
suggests a bounded one-hop candidate arm for ordinary recall. Evaluating that
change would require recall-quality and latency measurements within the existing
150 ms soft deadline. The graph is available independently of prompt packing.

## Voice delivery recovery

The realtime owner reconciles a missing speech receipt only after fencing the
retained LiveKit speech handle, its linked tasks and gateway generation. It records
an empty incomplete delivery when no receipt exists. The [design](../phase-8-voice-delivery-recovery.md)
records the private LiveKit 1.8.5 task-list coupling and remaining physical acceptance.

## Independent post-delivery voice/UI amendment

The operator requested CPU Phonon voice/speed controls and immediate browser reply
text while Phase 9 continues. The [design and validation](../voice-controls-and-read-ahead.md)
retain synchronized played-prefix receipts, installed-only voice choices and the
existing room/audio owner. This adds no memory/schema, task or inference-scheduler
contract and does not reopen Phase 8 or advance a future phase.

## TTS configuration amendment — 2026-11-04

[Engine-neutral TTS settings](../tts-configuration.md) replace provider-branded shared
service types and runtime identifiers. A legacy TOML alias preserves existing files;
Kokoro and Phonon retain the existing OpenAI-compatible adapter. Deployment-level
voice/pace defaults initialize the adapter and optional browser controls consistently.
This introduces no character, memory, gateway or Phase 9 contract dependency.

## Connected-idle native capture

Terminal chat, hosted LiveKit, and degraded HTTP text negotiate
`idle_native_capture_v1` and keep the realtime reservation between turns. Native
P3 can run in a permitted idle gap; older gateways stay disconnect-only. The
[runtime lifecycle](../../ember-agent/docs/adr/0006-phase-eight-calendar-and-capture.md#connected-idle-native-capture)
resumes bounded batches on activity and policy changes. The
[gateway](0024-phase-thirteen-inference-hardening.md#connected-idle-native-capture)
fences queued work on foreground activity but does not preempt a running window.

The existing processing job stages validated output. Acceptance waits for the
turn or delivery to settle and still uses the exact native policy and durable
receipts. No new writer, scheduler, or import authority is introduced. Checkout
implementation is not deployment or acceptance. [P8-AC-07](../../PLAN.md#phase-8-acceptance-criteria-automatic-capture-and-memory-source-controls)
evidence for this path, including live latency on the exact runner profile, remains open.

## Current-belief preview

[Memory ADR 0009](../../ember-memory/docs/adr/0009-phase-eight-scope-and-temporal-capture.md#current-belief-preview)
records an explicit, engine-owned route for accepted current claims. Ordinary chat, historical
lookup, and archive evidence remain unchanged. The [paired replay](../memory-benchmark.md#current-belief-preview)
checks scoped eligibility before limits (P5-AC-02). Offline tests check current validity and
delivery expiry (P8-AC-26) for this route only. Raw-history extraction (P6-AC-10), live chat
quality, and scale were not run. No deployment.

## Explicit typed recall

`current: <subject>` selects accepted current claims only. `history: <subject> in <period>`
selects exact identities with event-time overlap; `history: <subject> recorded-before <date>`
selects the pre-cutoff recorded view. Dates use the configured conversation timezone. Missing
or ambiguous scope clarifies. Typed turns do not read pins, profile, raw archive, recent
excerpts, or prior replies. They do not invoke interpretation, tools, or deep recall.

The engine returns an unresolved-conflict flag for each selected current revision, never the
deferred candidate or its evidence. Prompt packing retains flags only for surviving revisions.
The visible reply discloses unresolved conflict even if the model omits it. Final certification,
streaming, and acknowledgment recheck revisions, flags, temporal scope, and source policy.
Initial typed retrieval and certification share a 150 ms soft deadline after capability lookup.
Timeouts and empty results do not fall back. Ordinary recall and archive ranking are unchanged.
See [validation](../memory-benchmark.md#typed-recall-on-miku-fedora) for scope and limits.

## Supporting source follow-ups

[Agent ADR 0006](../../ember-agent/docs/adr/0006-phase-eight-calendar-and-capture.md#supporting-source-follow-ups)
records bounded exact evidence lookup after a completed reply. The
[paired replay](../memory-benchmark.md#source-follow-up-replay) checks verbatim output and
source controls for this path (P5-AC-04, P8-AC-26). Live transport and real-source acceptance
were not run. Archive ranking and permissions are unchanged.

## Deep archive relevance preview

[Agent ADR 0006](../../ember-agent/docs/adr/0006-phase-eight-calendar-and-capture.md#deep-archive-relevance-preview)
records the default-off deployment binding for the existing hybrid archive option. It changes
only optional archive candidates in deep investigations. AMB measures the engine option;
the browser route needs separate validation (P8-AC-13/16/17). No permission or schema change.
