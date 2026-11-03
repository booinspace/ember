# Phase 8 implementation validation

Status: **in progress, not accepted**. The current implementation is in `ember`,
including its uncommitted Phase 8 changes at `1107679`. Earlier isolated-clone
evidence below remains historical. Main-device integration is recorded in the
2026-10-13/14 sections; the 2026-10-16 review changes have not restarted services
or modified production memory data.

## Executed verification

The earlier isolated Python gate passed **601 tests, 6 skipped**, in 119.42
seconds on 2026-10-13. Strict Pyright, Ruff formatting/lint and the locked
offline environment check pass. One external Starlette/httpx deprecation warning
remains; it does not indicate a first-party test failure.

Focused verification covers day/month speech-style dates, invalid numeric dates,
Unicode span offsets, older years and technical identifiers; authorized aliases
also retrieve entity-valued facts whose payload contains only an entity ID;
London DST and leap years; schema-4/5 bundle restoration and migration; explicit
conflicts without accidental incident merging; bounded browser status history;
stalled stream cancellation and exact-span inspector links; mixed accepted/raw archive neighborhoods and grant
revocation; reviewed enrichment restart, CAS, replay, selected-bundle history and
unchanged-vector reuse; an additional focused two-character/library test proves
that enrichment preserves approval snapshots, other seeds and an old job; idle capture backlog continuation, explicit failed checkpoint retry and missing-credential
blocking without an idle spin loop; spoken deep routing and original input preservation; and CPU
speech initialization ownership and permission-validator failure fencing.

Frontend Prettier, TypeScript and the production Vite build pass. **Six frontend
tests** cover contracts, connection ownership and rendered inspector content. They do not establish rendered
browser, microphone or audible playback acceptance. The lazy LiveKit SDK chunk
is approximately 562 KB minified (148 KB gzip); the build reports its size warning.

Installed LiveKit transport tests use the existing binary read-only, with
synthetic generation and temporary isolated roots/ports. Real-model configuration
is unset. These are transport tests, not Qwen or speech-quality measurements.

## Performance evidence and limits

Reports are synthetic and aggregate-only. Retrieval probes use installed CPU
BGE small with one thread; the parser-only report uses no encoder or inference.
No private archive is scanned or reimported.

| Report | Result and practical limit |
|---|---|
| [Initial read contention](reports/phase-8-memory-benchmark-initial.json) | 1,000 ordinary requests per condition. Ordinary p95 rose from 17.74 to 32.69 ms during ungated deep work: regression gate failed. Preserve this evidence. |
| [First foreground guard](reports/phase-8-memory-benchmark-foreground-guard.json) | Repeated ordinary regression gates passed; all 307 attempted investigations deferred. |
| [2026-10-13 foreground guard](reports/phase-8-memory-benchmark-20261013.json) | Ordinary p50/p95/p99: 13.39/14.73/20.07 ms disabled; 13.38/14.91/19.96 ms enabled idle; 13.52/15.27/20.45 ms with attempted deep work. All 292 investigations deferred. No sampled ordinary call exceeded 150 ms or degraded. |
| [Nonempty temporal retrieval](reports/phase-8-temporal-benchmark-20261013.json) | 1,000 warm samples per selector, 1,000 general records plus 18 dated examples. Structured ended-event/state/observed/recorded-history retrieval p95 0.86–1.01 ms; topical event retrieval 6.67 ms. Historical validation p95 0.54–0.93 ms; qualified-row formatting 0.005–0.008 ms. |

The temporal measurement selected one or two memories within the 768-unit engine
allocation; it verifies the six-memory upper bound, not six populated prompt rows.
It separates engine retrieval, dependency validation and row formatting, and is
not full CharacterCore preparation or final-answer latency. The guard report's
parser p95 was 0.0225 ms before the later explicit spoken-day syntax expansion.
[Current parser measurements](reports/phase-8-calendar-benchmark-20261013.json)
have 1,000 samples for each of seven cases: summer/day/Unicode/older-year p95
0.020–0.022 ms, two-period ranges 0.064 ms, technical ordinary queries 0.008 ms
and missing-year clarification 0.008 ms. No interpretation call is made.

**Completed concurrent deep work has not passed its performance gate.** Shipping
behavior remains conservative foreground deferral and cache-only deep embeddings.
Cold/distinct queries, large archives, refresh/copy/encoder contention, peak RSS,
broader curated evidence precision/support recall and actual Qwen/voice/interpretation
latency remain unmeasured. Passing deferral measurements cannot close those gates.

## Curated retrieval evidence

[The ranked NAS probe](reports/phase-8-curated-recall-ranked-20261013.json) uses
1,000 general synthetic records plus eight explicitly dated, entity-annotated
NAS memories, including two independent alternative incidents. Dates are fixture
metadata supplied by the confirmed-user authoring API, not model-extracted dates.
One warmed topical query selected all eight with eight exact supporting passages:
precision/support recall 1.0/1.0 for this fixture, approximately 40.8 ms. Cross-character
denial and cached inspector invalidation after source exclusion passed. Shared NAS
identity did not merge the eight independent groups. Inclusion explanations now
reuse existing RankingExplanation; no new relationship/projection layer is added.

This is one explicit, warm, synthetic case. It does not establish paraphrase/cold
quality, inferred project grouping, imported-corpus recall or final Qwen reconstruction.
The inspector has date/period ordering, modality, inclusion reasons and exact-span
links to already-authorized observations. Its server-rendered test verifies escaping
and rejects a different passage in the same source as a citation; live browser/media
acceptance remains open.

## Acceptance tracking

Implemented paths below do not substitute for complete acceptance matrices.

| AC | Implemented evidence; remaining gate |
|---|---|
| 01 | Original input and native checkpoint are atomic; disconnected P3 accepts qualifying native claims. Full voice delivery/capture integration remains open. |
| 02 | Synthetic Milo extraction/acceptance/index/recall/restart and second-character isolation pass. Real browser/voice demonstration remains open. |
| 03 | Capture and recall policies are independent and persistent. Imports are not auto-approved. |
| 04 | Persisted selection, library-labelled browser controls, CLI and restored bundles. Full configuration/selected-import matrix remains open. |
| 05 | Origin SQL precedes limits in canonical/indexed retrieval, profiles/pins and validation. Complete restrictive top-k/performance matrix remains open. |
| 06 | Known source-dependent history invalidates; original input is retained. Raw deep fallback excludes generated assistant answers. Full history/voice matrix remains open. |
| 07 | Connection lifecycle drains SDK output before releasing the reservation; reconnect stops new P3 steps. Idle backlog continues without another disconnect. Real extraction/room contention remains open. |
| 08 | Exact checkpoints, replay/restart and played-prefix acknowledgements pass. Failed work is inspectable, does not block later checkpoints, and requires explicit retry. Full transport interruption matrix remains open. |
| 09 | Browser shows source/capture/provenance and demand-refreshed processing/index counts; CLI exposes review reasons. Full individual candidate browser review and restart cache matrix remain open. |
| 10 | React text/control and CPU Whisper/Silero/local Kokoro share the core. Profile ownership remains shared. Actual microphone/transcription/Qwen/TTS playback remains open. |
| 11 | Browser and managed/attached installed Kokoro share runtime ownership. Speech startup loads STT first; CPU initialization cancellation drains. Full readiness/crash matrix remains open. |
| 12 | Serialized UTF-8 guard, qualified rows and whole-turn trimming are tested. Actual tokenizer and oversized dependency matrices remain open. |
| 13 | Browser, terminal /recall and spoken recall deeply use the sole owner and no planner. Actual spoken inference demonstration remains open. |
| 14 | Typed relationships, independent incident groups and mixed archive neighborhoods are implemented. One warm NAS fixture passes retrieval precision/support recall; broader query and Qwen reconstruction acceptance remain open. |
| 15 | Calendar axes, natural day syntax, ranges, DST/leap years and ambiguity pass. Supported event/device anchors and full transitions remain open. |
| 16 | Authorized exact spans, archive-only and mixed accepted/raw neighborhood tests pass. Full source navigation/conversational evidence follow-ups remain open. |
| 17 | Historical/current authorization and inspector invalidation are tested; synthesized frames revalidate. Validator failure fences the room. Actual buffered-audio revocation and complete hidden-reasoning/denial matrix remain open. |
| 18 | Bounded admission, native SQL/row/byte meter, foreground deferral, cancellation drain and bounded status history pass. Complete fan-out/generation/peak-memory envelope remains open. |
| 19 | Full inspector results remain separate from qualified prompt selection/P0. Curated contradiction/qualification and oversized selection matrices remain open. |
| 20 | Small synthetic warm/deferral read gates pass after preserving the initial failure. Full cold/warm/refresh/large-corpus/quality gates remain open. |
| 21 | Progress/cancel/partial reasons, accepted-versus-observed and evidence groups are exposed. Date/period timeline and exact-span source links have rendered-content coverage; complete interactive browser/media acceptance remains open. |
| 22 | Identity/control/checkpoint restart and ephemeral result cancellation exist. Full browser/audio restart and fake-backend matrix remain open. |
| 23 | Text/transcription share local date/month/season/year/range resolution, without common-query inference. Actual scoped voice answer and complete syntax matrix remain open. |
| 24 | Dated archive 0.3.0/native 0.3.1 preserve exact passages, source clocks, precision and event/state distinctions. Full source-precision/classification matrix remains open. |
| 25 | Reviewed single/batch enrichment resumes per item; replay/CAS, IDs/origins, unchanged vectors and selected-bundle review history pass. A two-character/library approval/old-job isolation test passes; full completed-job and import matrix remains open. |
| 26 | Ended episodes, historical states and recorded revision validation pass, with current pins separate. Complete revoked/suppressed/indexed matrix remains open. |
| 27 | Small typed scope follow-ups pass, including requested axis changes. Ambiguous/current comparisons and selected-reference lifetime matrix remain open. |
| 28 | Default-off one-call P0 interpretation has bounded schema/input/axis checks and explicit invalid/timeout/cancel outcomes. Actual serial Qwen latency remains open. |
| 29 | Strict undated exclusion and past-subset/absence qualifications are emitted; raw evidence stays separate. Full coarse/planned-action/coverage matrix remains open. |
| 30 | Separate nonempty temporal retrieval/validation/formatting and limited parsing evidence now exist. Cold/refresh/contention/RSS/final-answer/interpretation measurements remain open. |

## Review and release gates

The installed requested code-review plugin describes a GitHub pull-request workflow;
this session exposes CI diagnostics but no callable local review launcher. This repo
has a private self-hosted origin, not a supported provider PR. **No plugin review
has run.** Local audit fixes and tests are not reported as the requested plugin review.

Local audit addressed: premature/unbounded browser status retention, exact spoken
dates broadening to months, legacy bundle version rejection, conflicts merging
independent incidents, stalled browser streams/pending connection teardown,
unconfirmed cancellation display, validation-watcher failure leaving output alive,
CPU speech startup cancellation/unused provider ownership, missing-background-token
idle spinning, lost inclusion explanations and source links matching a different
passage within the same record; Unicode calendar offsets; technical identifiers
mistaken for event years; and structured entity-valued facts rejected despite an
authorized matching alias.

The last human confirmation says ingest uses the GPU. Actual Qwen and voice
acceptance remain deferred until an authorized idle window. Synthetic generation
establishes transport behavior, not model quality, first-answer latency or audio quality.

See [Root ADR 0015](adr/0015-phase-eight-implementation.md) and the approved
[implementation proposal](phase-8-implementation-proposal.md).


## Main-checkout launch verification: 2026-10-13

The configured primary non-MTP deployment passed a startup-only smoke check after
the operator reported finished ingest. It served the production frontend, completed
private capability bootstrap, returned authenticated state matching the existing
character binding, reported CPU semantic retrieval available, and drained owned
services on shutdown. The activation discovery file was removed. No prompt was
submitted and no candidate was approved or seeded. Voice was disabled; actual
microphone, synthesis, Qwen answer and broad acceptance gates remain open.

The root PowerShell wrapper passed syntax validation. Browser-launch regression
coverage checks ready-state admission, exact private URL dispatch, OS refusal/error
without runtime loss, CLI routing, and rejection of disabled browser before startup.
The current frontend built and its six tests passed.

The updated root quality gate passed: 603 Python tests passed, six explicit
service/model tests skipped, Ruff formatting/lint and strict Pyright passed.
Contract schemas remain current. Frontend formatting, build and six tests passed.


## Installed browser/voice evidence: 2026-10-14

Actual installed Edge connected to the primary runtime and enabled Send/microphone
without submitting a conversation to the private character. Kokoro/af_alloy synthesized
real PCM/WAV and CPU int8 faster-whisper-small.en recognized the known synthetic phrase.
The managed provider was started/stopped through retained owned handles. Runtime guards
force CPU/offline operation and warning-only speech logs.

A separate synthetic character, accepted/indexed color memory and the single shared
primary gateway then passed two real browser cases: typed question to recalled Qwen
answer/spoken RTP, and synthetic microphone to final Whisper transcription, recalled
Qwen answer/spoken RTP. First successful receipts measured 4,952 ms and 8,832 ms to the
supported visible answer, with 50,989 and 40,308 inbound audio bytes respectively. Those
samples include browser/utterance/generation/playout behavior and are not p95, Qwen-only
TTFT, strict retrieval measurements or optional interpretation measurements. See the
[aggregate receipts](reports/phase-8-speech-browser-20261014.json). The final lifecycle re-run also passed both cases: 9,527 ms / 54,328 bytes
for text and 6,767 ms / 24,344 bytes for voice. Variability and the tiny sample
count prevent a percentile or regression claim.

The browser run caught errors native fixtures missed: expired dependent-agent rooms,
unusable forced-loopback media candidates, current provider voice objects, and agent-
published microphone transcripts incorrectly shown/duplicated as assistant text.
Regressions cover these protocol boundaries, audio-refusal text fallback and original
input persistence when gateway admission fails or is cancelled. Optional browser tests
check exact synthetic-character identity and zero libraries before sending input; they
skip without an explicit fixture and never use a physical microphone or private archive.

P8-AC-10 and P8-AC-11 now have installed-provider/installed-browser functional evidence.
Physical microphone/headset/speech quality, full degraded LiveKit/browser fallback,
buffered mid-playout policy revocation, broad recall quality, large-corpus/refresh/RSS
and other outstanding criteria remain open. Phase 8 remains in progress.


The final main-checkout gate passed 607 Python tests with six explicit service/model
cases skipped; Ruff formatting/lint, strict Pyright and schema freshness passed.
Frontend formatting/build/type checks passed, as did 11 unit tests and both installed-
browser speech/recall cases after the final lifecycle changes. The main runtime
reported voice ready and its existing character index available. No additional
private import review/approval/seeding or primary-character conversation occurred.


## Large private-memory-set regression: 2026-10-14

The operator authorized testing the large imported character memory set. The running
browser API reproduced resource exhaustion; further profiling/benchmarks used read-only
SQLite backups and copied disposable indexes under an ignored isolated root. Original
archive, character sources, approvals, seed origins and running runtime ownership were
preserved. No generation, approval, extraction or seeding was performed for this check.

[Aggregate before/after evidence](reports/phase-8-deep-large-set-20261014.json) contains no
private query text, findings, quotes, store/character IDs or credentials. It measures the
same fourteen queries over three rounds on 12,563 canonical character memories. Earlier
branch hydration spent thousands of SQL statements re-reading shared conversation nodes.
The new bounded selected-path lookup, request-local immutable cache and revision reuse
remove that repetition. Completed certificate checkpoints survive later budget exhaustion;
they still undergo current policy/source validation on inspection and answer delivery.

| Measurement | Before | After |
|---|---:|---:|
| Budget failures / 42 investigations | 30 | 0 |
| Investigations containing accepted memories | 9 | 39 |
| Deep p50 | 859 ms | 476 ms |
| Deep p95 | 983 ms | 577 ms |
| Maximum deep duration | 1,016 ms | 593 ms |

The remaining three zero-memory outcomes are strict event-time queries that exclude
undated memories, not execution failures. Counts establish usable retrieval, not semantic
precision/recall or support-quality scores. Cold deep semantics remain conservatively
unavailable without a cached query embedding, with lexical retrieval still usable.

Each ordinary condition measured 1,000 warm retrievals on the snapshot: p95 42.89 ms with
deep disabled, 42.82 ms enabled idle, and 43.74 ms with competing deep attempts. Existing
p50/p95/p99 and partial/timeout regression caps passed; none exceeded 150 ms. All 638
contending attempts were deferred, so completed concurrent deep semantic admission remains
uncertified. No refreshed-index, peak-RSS, multi-character, Qwen or speech timing gate is
closed by this report. SQL/row/byte/deadline limits were not increased.

Regression fixtures cover a 300-node original conversation with 24 accepted memories,
exact quote integrity despite immutable cache reuse, selected/alternative/missing original
branches, certified-checkpoint retention after final/optional-archive budget failures, and
invalidation after source selection changes. The UI reports the exhausted limit and refuses
answering from an empty prompt selection. Seven new Python regression cases passed.
The full installed gate passed 610 Python tests with 10 explicit integration/model cases
skipped; frontend tests passed 12 cases. Ruff format/lint, strict Pyright, public schema
freshness and frontend type checking/build passed. Phase 8 remains in progress.

An additional authenticated control-API check constructed LocalRuntime with the current
deployment settings and existing character binding in the isolated snapshot. BrowserControl
bootstrap, investigation and current-policy inspection routes returned 11/24, 6/14 and
12/24 memories/observations in 578, 344 and 515 ms respectively, without exhausted limits.
ASGI transport exercised the same browser routes; no room connection or generation occurred.
The operator's running main process retains its old imports until a normal restart.
## Current-checkout review and remediation: 2026-10-16

The current `ember` checkout, including existing uncommitted changes, was reviewed
against PLAN P8-AC-01–30 and the supplied engineering checklist. See the
[complete review](reports/phase-8-architecture-review-20261016.md) and
[remediation design](phase-8-review-remediation.md).

Verified corrections cover a reproduced hypothetical/assistant-reported native
claim acceptance, the new LAN test's root collection failure, dependencies from
discarded retrieval candidates, repeated profile pins in deep answers and idle
capture resumption. Full-source qualification tests exercise both legacy and dated
extraction through real capture/acceptance and restart. Selected certification
uses metadata only, preserves grant/expiry/revocation/row bounds and negotiates
capability fallback without changing old default serialization.

A missing degraded startup path now serves authenticated browser HTTP text through
the existing character core and gateway, or controls only without a ready gateway.
Tests exercise actual HTTP listener publication and shutdown, canonical input capture,
explicit delivery receipts, replay/overlap, lease expiry/cancellation, and acknowledgement
racing with disconnect. Browser tests cover reply/receipt ordering, pending-connect
teardown and failed heartbeat cleanup. These checks use isolated synthetic stores
and injected unavailable services; no production runtime or data was changed.

The locked offline sync, Ruff formatting/lint, strict Pyright, schema freshness and
full Python gate pass: **659 passed, 10 skipped**. Frontend formatting, TypeScript,
production build and **16 tests** pass. Three guarded installed-browser cases skip
without their explicitly configured fixtures/services. The existing third-party
Starlette/httpx warning and Vite dependency/chunk warnings remain. No new Qwen,
physical voice, RSS/refresh/large-scale or real-time latency claim follows from
these tests. The complete Phase 8 acceptance matrix remains open as described in
the review; the implementation remains experimental.

## Ordinary word recall correction: 2026-10-16

The user requested individual-word recall over all eligible accepted memories and
bounded extracted values in context. See the [design](phase-8-word-recall.md),
Root ADR 0015 and the component Phase 8 ADRs. This supersedes the ordinary six-row
limit and 768-byte allocation; previous performance measurements remain historical.

Seven new sanitized cases exercise the actual canonical engine and shared composer:
indexed and degraded name questions among 110 noisy predicate matches with a
distinct source-like subject; 32 word matches across facts, episodes, goals and
plans beyond the old importance sample; Unicode/case/word boundaries and independent
terms; forgetting/source exclusion; oversized and duplicate values; small and large
configured input guards; selected-delivery invalidation; and polarity, hypothetical
modality and supported date preservation. A larger guard fits 40 distinct short
values. Existing profile allocation and selected-certificate regressions pass with
explicitly budgeted omission fixtures.

The full installed quality gate passes: **666 passed, 10 skipped**; Ruff formatting
and lint and strict Pyright pass. The existing third-party Starlette/httpx warning
remains. Tests introduce no model inference or real personal fixtures. The narrow
live diagnosis used read-only queries; no canonical data, deployment configuration
or service was changed. An indexed lexical check establishes candidate selection,
not complete live Qwen response quality or a new end-to-end latency benchmark.

Current deployment config inspection shows 8,192 context tokens and a 7,000 input
ceiling. The composer supports 32,768 when configured, with its memory share capped
at 6,144 conservative UTF-8 units and gateway output reserves still enforced.
Activation/restart, real-model response checks and new expanded-bound performance
measurements remain pending; Phase 8 remains in progress.

## Operator-approved 32k activation: 2026-10-16

The operator explicitly approved activating the recall correction, setting 32,768
context tokens, 28,000 input and a 4,096 output reserve, restarting Ember and checking
recall. The ignored private deployment file was validated and atomically updated,
with a verbatim private backup. Graceful console interrupts were restricted to
verified runtime/launcher membership. Managed services and the runtime lease drained
before replacement; the independently attached Ollama service was retained.

An intervening launch exposed HTTP text fallback and a reported Connect-busy error.
Authenticated Connect/disconnect succeeded when inspected, so the transient busy
report was not reproduced. HTTP generation returned the expected saved-name answer
using 20 memories and its explicit delivery acknowledgement succeeded. A subsequent
clean managed launch restored LiveKit transport and voice readiness. The standard
RoomClient verified the room identity, completed a saved-name question with 20
memories and disconnected its diagnostic participant. No personal prompt, answer,
quote or credential is included in this record. Verification creates normal native
conversation observations; it does not reclassify or approve imported claims.

Final authenticated health reports gateway ready, context 32,768 and journal
available; the loaded Ollama model independently reports context 32,768. Browser
state reports LiveKit transport, text available and voice ready. The current private
activation page was reopened. No canonical migration, source re-extraction or index
rebuild was needed. The diagnostic RoomClient interpreter emitted third-party FFI
destructor assertions after its completed disconnect; the runtime remained healthy.
This is a bounded text recall/connect check, not physical microphone/playback or
broad model-quality/performance acceptance. Phase 8 remains experimental.

## Browser thinking and selected recall: 2026-10-16

The [UI design](phase-8-turn-details-ui.md) and Root ADR 0015 record the change.
Synthetic display markers exercise opt-in gateway thinking, scope/output limits,
native non-serialization, visible-only archive/history, exact selected values,
authenticated controls, revocation and UTF-8/JSON bounds. The installed full gate
passes **673 Python tests, 10 skipped**, Ruff and strict Pyright. Focused temporal/view
tests also pass after the final historical-qualifier and display-bound adjustment.

Frontend formatting, TypeScript, production build and **18 unit tests** pass.
**Three isolated installed-Edge browser cases pass** for Enter/Shift+Enter, IME/repeat
handling, safe live expansions, teardown and scrolling. Three guarded LAN/speech
integration cases remain skipped without fixtures. The synthetic layout screenshot
was visually inspected; it contains no real thinking, personal source or credential.
Existing third-party warning categories remain.

The pinned model declares thinking capability through local metadata inspection;
no model generation was needed for that probe. Activation requires runtime/gateway
reload. Automatic approval review rejected the proposed graceful restart because
the earlier authorization covered the separate 32k rollout. The active process
remains unchanged. Explicit UI deployment approval and live feature verification
are pending; Phase 9 has not started and broader Phase 8 gates remain open.

## Plan-memory turn preparation regression: 2026-10-21

The reported application error reproduced on the existing isolated memory snapshot
with the deployed 28,000-unit input guard. Preparation failed in the current-turn
view with invalid-json-value at steps; no generation had started. A selected plan's
Python tuple violated the display's JSON contract. Converting the shared projection
to an array fixes both empty and populated step lists without altering the JSON
model prompt or canonical records.

After correction, the reported query prepared twice with 20 selected memories and
four displayed plans; view JSON validated. This used the isolated snapshot, no
model generation and no writes to the active character. Synthetic regressions
complete two turns for each plan shape and verify matching prompt/display values,
proposed state and PLAN modality. The turn-details, word-recall, character and deep
routing suites pass 27 tests. Ruff formatting/lint and strict Pyright pass.
The running app was inspected without interruption and still needs a normal
Ctrl+C / start.ps1 restart to load the source fix. This is a preparation regression
check, not a new real-model quality or performance measurement.
