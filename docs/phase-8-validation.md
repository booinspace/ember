# Phase 8 implementation validation

Status: **in progress, not complete or deployed**. Work is isolated in
ember-phase-eight, based on origin/master 640888d. The primary checkout, private
database, gateway, ongoing ingest and GPU have not been changed.

## Executed verification

The latest full Python gate passed **601 tests, 6 skipped**, in 119.42
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
