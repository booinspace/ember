# Agent ADR 0006: Local calendar recall and idle native consolidation

Status: partial Phase 8 implementation, experimental and not deployed.

## Decision

CharacterCore owns a small typed CalendarResolver. Inject an aware conversation
reference clock and configured IANA timezone, default Europe/London. Windows setup
installs tzdata; startup validates the configured zone without downloading anything.
The engine owns SQL, ranking and current authorization.

Resolve dates/months/seasons/years and calendar-relative periods locally. Event,
observed and recorded-history wording selects the appropriate TemporalQuery.
Date-only requests use structured retrieval. Topic-bearing requests reuse existing
lexical/vector search. Unsupported/ambiguous scopes return clarification rather than
performing an unfiltered search. Follow-ups retain only the last typed scope.

Carry historical selectors into retained-history dependency certificates. Keep
profile/manual pins current. Render historical qualifiers and strict undated coverage
in the prompt. Retain six ordinary memories and existing prompt/metadata ceilings.

Native capture and acceptance are engine-owned. LocalRuntime drains bounded P3
batches through the existing HTTP gateway. The [connected-idle amendment](#connected-idle-native-capture)
extends the original disconnect-only lifecycle without expanding native acceptance
or import approval authority. Optional calendar interpretation defaults off.

## Acceptance boundaries

Calendar, DST/leap-year, historical delivery and source-backed extraction have
synthetic acceptance tests. Browser controls, optional interpretation, deep service
and SDK playout-prefix acknowledgement are implemented. Complete rendered-browser,
buffered media revocation and real GPU/audio acceptance remain open.

[Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md) records integration,
scope and open gates. [Agent ADR 0005](0005-bounded-profile-and-delivery-validation.md)
retains historical Phase 7 evidence.

## Browser, speech and deep orchestration

The application browser owns controls and explicit investigation tasks; it shares
the sole runtime/character and SDK room. Private activation and bounded room tokens
keep inference credentials out of the client. Optional CPU speech loads local
artifacts off-loop with one owned native STT worker; cancellation retains admission
until that worker finishes. No startup download or direct Ollama fallback exists.

The SDK wrapper holds one authorized prepared turn. Playout observations bind
acknowledged output by that owned turn, not by the SDK's unrelated message UUID.
Only acknowledged complete output enters retained conversation history. TTS frames
recheck the same certificate. Buffered media revocation/real audio tests remain open.

Deep service orchestration uses typed ABI results, exact excerpts and existing
related reads; the character business layer still knows no SQL. Full results stay
in an ephemeral bounded inspector, with separately budgeted P0 prompt selection.
The initial implementation introduced no extra planning, model summarization,
ownership or implicit automatic deep trigger. The opt-in `natural_deep_recall`
setting now uses v1 phrase matching at the same voice and text entry points,
without changing service limits or permissions. Completed embedding-cache hits
are reused; new deep CPU encodes are
deferred until the stronger shared-resource gate is measured.

The original room connection lifecycle drained media before releasing the gateway
reservation and stopped new extraction steps on reconnection. Capture still uses
durable engine checkpoints and the existing P3 profile. The optional calendar
interpreter is independently configured, one-call, source-checked and bounded.
See Root ADR 0015 and the acceptance tracker for remaining delivery gates.

## Runtime ownership update: 2026-10-13

The shared resolver accepts explicit English day dates as well as ISO dates.
Invalid days and ambiguous slash/dot dates clarify; they cannot degrade to a
whole-month/year selector. Recorded history requires an explicit before cutoff;
a person's past beliefs do not implicitly select the system's recorded axis.

Text /recall and spoken recall deeply route through the same injected investigation
callback. Original input is observed before expansion, so cancellation or deferred
deep work cannot lose input or archive a rewritten command as the user's source.
Deep answer preparation requests current pins without a discarded ordinary search.

Kokoro startup is an injected composition callback after installed CPU inputs load.
The existing owned process supports installed executable/argument/cwd configuration;
CPU/offline environment guards apply to that child only. Input initialization remains
owned through cancellation and STT cleanup; provider failures retain text operation.

The room watches dependencies through generation and buffered playout. Unexpected
validation failure stops admission and disconnects media before draining the SDK.
Actual audio revocation remains unverified. Browser connection teardown aborts
streams and removes audio, and cannot reopen after pending authorization completes.
Status history uses the existing bounded map while active requests retain ownership.

A demand-refreshed browser summary reports capture/acceptance counts and index
status. In the original disconnect-only path, bounded batches continue until
reconnect and skip failed checkpoints until explicit retry. No new background
inference scheduler or per-turn status/profile machinery is added.

Inspector inclusion explanations reuse ABI RankingExplanation. Dates and periods
remain separate from observation/recording clocks. Citation links match source
identity, exact codepoint interval and content/quote hashes; sharing a source record
cannot mislabel a different passage as support. Rendered-content tests verify escaping
and link identity; actual browser/media operation remains a release gate. Missing
background credentials leave the existing connection lifecycle blocked rather than
self-signalling a non-progressing idle drain.

Calendar matches use original Unicode offsets; case folding is confined to semantic
comparison and topic normalization. Technical year-like IDs are excluded per match,
so an RTX model number does not erase or conflict with a separate calendar scope.
Deep inclusion accepts eligible non-generic ResolvedEntity references already returned
by related lookup; an entity-valued fact need not repeat a literal display name in its
payload. This reuses the existing alias contract and preserves independent clusters.


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


## Deep recall checkpoints and diagnostics: 2026-10-14

Reuse the completed RelatedResponse dependency certificate as a request-local checkpoint.
If later optional expansion/final hydration reaches a read/time limit, return only the
last completed checkpoint under its original temporal selector. An unvalidated candidate
is never promoted to a result. Optional archive work preserves a completed final response.
Current inspection/delivery validation still rejects changed policy, revoked grants,
suppressed memories or invalid source headers; cancellation/foreground deferral retain
existing behavior. No new persistent result cache, queue or model call is introduced.

RecallCounters now forwards content-free limits_hit diagnostics. The browser distinguishes
budget exhaustion from absence of memories and disables answer selection without prompt
material. Public memory ABI/schema versions do not change. Bounds remain unchanged after
the engine removed repeated original-branch hydration. See [Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md)
and [Memory ADR 0009](../../../ember-memory/docs/adr/0009-phase-eight-scope-and-temporal-capture.md).
## Review corrections and degraded text: 2026-10-16

The composer negotiates selected dependency certification only after category and
whole-turn trimming. It retains exact historical selectors and the relevant
profile/history boundaries. Older backends keep conservative validation. Deep
rows exclude current profile/manual pins, and only certified selected observations
enter prompt dependencies. Re-enabling capture signals the existing lifecycle
without another connection event. At this stage, P3 still required disconnection.

Unavailable LiveKit at startup now selects authenticated HTTP text using the same
core, memory client, personality, deep service and pinned gateway. Gateway startup
is independent of LiveKit readiness; unavailable model/gateway yields controls only.
No direct Ollama request or second character system is added. The browser reports
feature readiness and uses a small lazy text adapter when voice is unavailable.

The HTTP adapter owns one renewable 45-second lease, one 190-second bounded turn
and a 128-ID replay window. The browser renews every 15 seconds. Lease expiry,
explicit disconnect and shutdown cancel/drain native generation before the existing
connection lifecycle releases realtime. Buffered responses enter history/archive
only after an exact pending-turn acknowledgement; failed output is discarded while
original input remains durable. Initial room setup failure supports fallback;
in-flight voice turns are never automatically replayed over HTTP. No websocket
dependency, inference scheduler, background writer or startup download is added.

Authenticated HTTP, real-listener startup with unavailable services, replay/overlap,
unacknowledged output, expiry cleanup and browser close/heartbeat tests use isolated
synthetic stores. They establish ownership/protocol behavior, not real Qwen latency
or physical audio acceptance. See Root ADR 0015 and the 2026-10-16 review report.

## Ordinary extracted-value packing: 2026-10-16

The user replaced the ordinary six-row/768-byte restriction with individual-word
search and bounded relevant values. CharacterCore requests up to 100 candidates
from the public memory client with a 65,536-byte working-selection allowance.
It no longer blanks queries and searches a small unfiltered importance sample when
the index is unavailable; the engine owns indexed and degraded word selection.

The ordinary prompt includes original extracted values, with subject attribution,
polarity, modality, goal/plan state and supported dates. Memory IDs, revisions,
quotes and transport envelopes stay out of model context and remain in the exact
selected delivery certificate. Recorded timestamps appear only for recorded-history
questions. Existing detailed deep rows remain unchanged. Whole oversized rows are
skipped, and identical displayed values are deduplicated before packing.

Ordinary memories use at most one quarter of the configured input guard, capped at
6,144 conservative UTF-8 units. The total prompt guard and output reservation remain
independent and mandatory. These units do not claim tokenizer calibration. A
32,768-token deployment is supported; the inspected active config still declares
8,192 context/7,000 input. No running service or deployment configuration was changed.

Real-engine synthetic composer tests cover noisy labels, distinct imported-like
subject identity, per-word recall across kinds, scope/suppression, compact values,
small/large context allocations, oversized values and delivery invalidation. This
does not certify model answer quality or new fast-path performance. See
[Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md) and the
[design](../../../docs/phase-8-word-recall.md).

The operator subsequently approved 32k activation. The private deployment was
updated with a backup, and an owned graceful restart restored normal LiveKit and
voice/text readiness. Standard HTTP and RoomClient text checks both completed the
expected saved-name recall with 20 selected memories. Final gateway and loaded model
report 32,768 context. No new startup/transport code was needed for this activation;
the transient busy report was not reproduced. See Root ADR 0015 and the activation
section in the phase validation record, including the diagnostic client's SDK cleanup
warning and the remaining physical audio/quality gates.

## Ephemeral browser turn details: 2026-10-16

The [UI design](../../../docs/phase-8-turn-details-ui.md) adds optional thinking,
selected-value expansions and composer controls before Phase 9. Core preparation
records exact final profile/ordinary/deep values with their delivery dependencies
in a single RAM view. Inspection rechecks access, returns no-store data and clears
revoked content. Stale events cannot update another turn. There is no new memory
lookup or persistent store.

Gateway wire 0.4.0 exposes the explicit native thinking channel only for opt-in plain
P0 text, under `live_thinking_v1` and the configured output reserve. Old/default
requests preserve their output bound. Native serialization, SDK speech/transcripts,
visible-only durable dialogue and subsequent prompts exclude thinking. The view's
100-value / 64 KiB memory / 32 KiB thinking / 128 KiB total bounds include JSON
escaping, with labelled truncation. The browser polls serially at 500 ms, aborts on
teardown and uses no persistent browser storage for these details.

The UI uses escaped, collapsed panels. Enter submits once, Shift+Enter adds a newline,
and IME/repeat keys cannot submit. Sent/final transcribed user messages force viewport
scrolling; assistant updates follow only near the bottom. Existing UI transports and
the single character core/gateway remain shared. Root ADR 0015 records the passing
673 Python / 18 frontend / 3 browser checks and the pending activation: automatic
approval review rejected the restart as outside the earlier 32k rollout authorization.

## JSON-safe plan inspection: 2026-10-21

The shared recall-value projection converts PlanPayload.steps from its immutable
tuple to a JSON array. This preserves existing prompt serialization and plan
qualifiers while satisfying the ephemeral view's JsonValue contract. Previously
validation failed before generation whenever a selected plan reached inspection,
including a plan with no steps. No new abstraction or per-turn lookup is required.
Real-engine regressions cover empty/populated steps and repeated completed turns;
27 focused tests and strict types pass. Root ADR 0015 and the phase validation
record document the isolated large-set reproduction and restart requirement.

## LAN reverse proxy

The runtime can be configured with an HTTPS public origin and one trusted Nginx peer. This uses the existing browser and LiveKit owners; the proxy handles TLS and forwards signalling. Direct public-origin admission grants the existing operator session, so network access to the proxy is the authorization boundary. See [the root design](../../../docs/phase-8-lan-https-design.md) for configuration and validation limits.

## Long speech correction

The existing CPU recognizer owns sequential 30-second transcription chunks for
a VAD utterance of up to five minutes plus prefix padding, with one final result
and bounded worker lifecycle. The SDK VAD drops further continuous audio until
a pause. See the [root design](../../../docs/phase-8-long-speech.md).

## Managed CPU Phonon input

The agent starts the explicitly configured local Phonon-2 CPU process offline,
checks model and CPU health, and owns its shutdown. A failed or unsupported
service disables voice rather than selecting Whisper implicitly. See the
[root design](../../../docs/phase-8-long-speech.md).

## Mobile browser layout

The browser adapts its existing controls and conversation to narrow and short
viewports, with accessible names and larger touch targets. It uses existing runtime
contracts; see the [root design](../../../docs/phase-8-mobile-ui.md).

## Memory graph browser

The existing React build serves `/memory-graph` in both browser modes. The operator
session and origin checks protect the read-only endpoint. Search text stays in
the request body and graph responses use `Cache-Control: no-store`. The runtime
passes the operator's query to the active character's engine through composition.
The canvas combines completed batches, deduplicates entity nodes and links, and
draws a relationship when both ends are loaded. The inspector pages its keyboard-
accessible list independently of the canvas.

## Voice delivery recovery

The hosted room retains and fences the LiveKit speech handle and its linked tasks
before reconciling missing delivery receipts or admitting a new turn. An absent
receipt is never treated as delivered speech. See the
[root design](../../../docs/phase-8-voice-delivery-recovery.md) for SDK coupling.

## Independent voice/UI amendment

The operator requested CPU Phonon voice/speed controls and immediate browser reply
text while Phase 9 continues. The [design and validation](../../../docs/voice-controls-and-read-ahead.md)
retain synchronized played-prefix receipts, installed-only voice choices and the
existing room/audio owner. This adds no memory/schema, task or inference-scheduler
contract and does not reopen Phase 8 or advance a future phase.

## TTS service configuration amendment — 2026-11-04

`TTSServiceSettings` / `services.tts` own generic command/endpoint lifecycle settings.
Only the wire-config parser retains the legacy `services.kokoro` alias. The existing
OpenAI-compatible audio adapter receives configured model, voice and `tts_speed` at
startup; optional browser selection reports that same pace. Kokoro-specific provisioning
and model/voice IDs stay at the adapter/deployment boundary. See the
[configuration runbook](../../../docs/tts-configuration.md) and Root ADR 0015.

## Connected-idle native capture

One runtime-owned lifecycle handles native capture during terminal input waits
or idle periods in hosted LiveKit and HTTP text. It gates connected P3 on
`idle_native_capture_v1` and the selected transport's idle check without releasing
realtime. Older gateways keep the original disconnect-only path. See [Agent ADR 0014](0014-gateway-release-and-realtime-ownership.md#native-capture-permit)
for credential and gateway enforcement.

A completed extraction window can be staged while a turn, LiveKit playout, or HTTP
receipt is pending. The lifecycle fences acceptance as foreground starts and waits
for any already-submitted acceptance write to settle. Resumption uses pinned jobs
and existing exact native policy receipts, not a new import or acceptance path.
Input, policy, connection, and delivery changes wake bounded batches; disabled
capture pauses work, and missing credentials report blocked rather than polling.

## Supporting source follow-ups

Standalone questions such as “Where did I say that?” use one completed, acknowledged
reply from the current conversation. When it had one unambiguous recalled memory and no
competing source context, the core retains its exact supporting span and revision in memory.
It reads that span through `evidence_read`, preserves the prior time selector, and revalidates
before delivery. Multiple claims clarify; missing or invalid evidence abstains. Quote lookup
has a 500 ms soft bound. Exact wordings bypass inference. For short, likely paraphrases,
one local foreground call receives only the current question and proposes `source_followup`
or `other`, with a four-second deadline. `other` continues normal routing; invalid or
unavailable classification asks for a rephrase. Both source routes use the same verifier
and acknowledgment checks. No archive fallback or new persisted metadata. A
supporting span does not prove first occurrence or model attribution. See
[paired replay](../../../docs/memory-benchmark.md#source-follow-up-replay).

## Deep archive relevance preview

Composition passes the deployment's optional `deep_archive_relevance` to `DeepRecallService`.
Only its hybrid `search_archive` call carries the existing memory extension. No request schema,
UI toggle, new retrieval primitive, or model call. Missing setting sends the same request as
before; unknown settings fail config validation. Accepted-memory search and exact evidence reads
are unchanged. Grants, budgets, and delivery checks remain in their existing owners. Coverage
still describes search execution, not exhaustiveness; filtering can drop relevant hits.
