# Phase 8 implementation proposal

Date: 2026-10-09. Status: pre-code architecture review; implementation has not started.
Base: origin/master at 640888d59ffaa0f501b7453e161fd62c9e945ab6.
Work area: separate clone, branch phase-eight, independent environment and ignored data.
Authority: [PLAN.md](../PLAN.md), including all P8-AC-01 through P8-AC-30 and its
engineering standards. The main checkout, active ingest, model files and runtime
state remain untouched. This proposal does not close any Phase 8 acceptance gate.

## 1. Desired behavior

Deliver all of Phase 8, not only the temporal amendment:

- A production browser and terminal/LiveKit text paths use one character core.
- CPU STT/VAD and local Kokoro TTS make voice use that same memory/prompt path.
- Completed native turns are durably captured; eligible memories are automatically
  accepted/indexed when background generation is admissible, without /remember.
- Per-character source selection controls local/imported recall independently of capture.
- Explicit deep recall investigates supported relationships and authorized archive
  observations, with progress, cancellation, inspection and bounded prompt selection.
- Ordinary temporal questions resolve to actual time-axis filters before ranking.
- Supported event dates, reviewed existing-memory enrichment and historical delivery
  make those questions meaningful without rewriting the current import.

Automatic capture is immediate; extraction is deferred while realtime sessions are
connected. Existing conservative P3/P4 admission still applies. The UI must show that
distinction, rather than imply memory learning runs continuously between spoken turns.

Current code already supplies the archive, policy acceptance, support consolidation,
indexes, profile, gateway and managed text-room slice. Browser/audio, automatic capture
orchestration, source-selection controls, full deep orchestration/neighborhood lookup,
ordinary temporal resolution and selector-aware delivery are integration gaps.

## 2. Ownership and introduced coupling

| Owner | Responsibility and boundary |
|---|---|
| ember-memory ABI | Typed capabilities, source selection, historical validation and bounded related-read extensions; no SQLite, LiveKit or Ollama imports |
| ember-memory engine | Canonical policy/checkpoints, source/date filtering, exact evidence, accepted revisions, background admission and index publication |
| ember-agent composition | Sole runtime engine owner, lifecycle of capture processing, shared services, health and injected clients |
| CharacterCore | One personality/history/prompt/answer path with temporal resolution and selected dependencies |
| Agent deep service | Explicit bounded read orchestration, association labels, condensation and cancellation through public clients |
| Realtime/audio adapters | Final transcript input, delivery acknowledgement, STT/VAD/TTS and interruption; no second character state |
| Browser/control adapter | Typed owner-bound actions, scoped room/session credentials and served React/TypeScript assets |

New coupling is deliberate: completed-turn capture produces exact source references;
the existing owner schedules their processing; source-selection changes invalidate
selection/dependencies; a historical selection retains its selector through delivery.
The browser and audio consume application contracts, never engine internals.
No new memory daemon, graph database, operational task framework or GPU scheduler.

## 3. Existing abstractions to reuse

Reuse LocalRuntime/RuntimeServices, CharacterCore, GatewayLLM/HostedRoom, the gateway's
P0/P3/P4 admission and realtime reservations, ArchiveDialogueSink and immutable batches,
ProcessingService/ReviewService, NativeAcceptanceService and support consolidation.
Reuse UpdateRequest compare-and-swap, source grants/suppression/origins, profile selection,
retrieve_context, related resolve/expand/validate, indexed retrieval, cache/generation
leases, bounded reader/writer/encoding pools and the outbox/index refresh path.

FastAPI/uvicorn already exist. Add application control/static routes to the application
owner; do not put memory ownership in the gateway or create a maintenance subprocess.
The new frontend stays inside ember-agent/frontend: React, TypeScript and Vite with
a committed npm lockfile. Explicit setup builds assets; production startup serves them
without a separate development server or package download.

Audio stays behind existing LiveKit adapters. Configure local CPU faster-whisper/Silero
and Kokoro-FastAPI; setup installs dependencies/models explicitly. Kokoro supports
attach/managed ownership with protocol/voice health checks; stop only owned descendants.
Qwen generation continues exclusively through the existing gateway.

## 4. Necessary additions and implementation order

1. **Storage/ABI compatibility and source policy.** Add compact per-character source
   selection/capture settings, epochs and durable exact-source capture checkpoints.
   Enforce selection by native/imported origins before lexical/vector limits and
   across profile, summaries, evidence, cached history and final delivery. Selection
   changes do not grant access or forget records. Extend existing eligibility helpers
   instead of duplicating predicates across each caller.

2. **Historical eligibility and event dates.** Distinguish episode occurrence from
   current-state validity in canonical/index scope and certificates. Extend
   ContextDependencies with capability-gated query-qualified selections; keep existing
   ID/revision references and separately scoped current profile pins. Add one small
   agent temporal resolver with an injected clock/IANA zone and bounded previous scope.
   New extraction versions carry exact date evidence/precision/basis; old job definitions
   and serialized pins remain unchanged. Reviewed enrichment reuses processing/review
   and metadata-only UpdateRequest revisions, preserving identities/approvals/origins.

3. **Automatic native capture.** Commit a capture marker with each completed source
   delivery, keyed by stable delivery/source identity. Process bounded exact-reference
   batches through existing jobs, rather than resnapshot the whole conversation history
   after every turn. The composition-owned runner uses P3 only after disconnection or
   explicit drained suspension, applies native policy, then coalesces index updates.
   Pending/rejected/failed proposals remain inspectable; old imports remain manual.
   Include partial-delivery truthfulness and transport delivery acknowledgement so
   text generation completion is not mistaken for audio actually played.

4. **Explicit deep recall and resource isolation.** Add the planned DeepRecallService
   and typed ephemeral result/budget contracts. Extend optional related lookup only
   for missing aliases/source neighborhoods and read manifests; retain engine ownership
   of joins and authorization. Bind a deep read profile to every nested read/encode/
   evidence call. Extend existing admission/cache machinery to favor foreground;
   do not merely allocate another executor. Condense deterministically into existing
   prompt categories; final synthesis alone uses Qwen.

5. **Browser/audio and launcher integration.** Add owner-bound control/session routes,
   serve the production frontend, scoped participant tokens and LiveKit browser input.
   Connect CPU STT/VAD and Kokoro TTS to the same core. Expose source/capture/profile
   controls, provenance, deep inspection/cancellation, partial/deferred outcomes and
   feature health. Keep controls/text available under documented audio/model failures.
   Barge-in stops playout; generation drains under current gateway rules rather than
   pretending Phase 13 model preemption already exists.

6. **Validation, measurements and review.** Complete the matrix below and reconcile
   findings before updating implemented status. No new functionality is released to
   the primary checkout as part of this isolated work.

Plan experimental ABI 0.5.0, engine 0.6.0/schema 6 and derived layout 0.6.0 where the
new canonical/projection fields require them. Version/export contracts and update
all package pins/locks together. Canonical upgrades require exclusive ownership and
consistent backups; derived upgrades require explicit update with vector reuse.
Startup never rebuilds indexes or downloads assets. Legacy 0.1.x/0.2.0 extraction
specifications remain loadable/replayable byte-for-byte; introduce a new extractor
version and native policy definition instead of editing their hashes.

## 5. Contracts, invariants, complexity and scalability

- Ordinary recall keeps one bounded context operation, six distinct memories plus
  eligible pins, a 150 ms soft retrieval deadline and no deep expansion/hydration.
  Date-only input uses structured retrieval; topical temporal input reuses the one
  existing query embedding. Per-category accounting preserves the 7,000 input ceiling
  and response reserve; whole-turn trimming cannot remove essential qualifiers.
- Reference time is the requesting conversation clock; default zone is Europe/London,
  with meteorological summer June 1 to September 1, exclusive. Explicit setup supplies
  IANA data on Windows. Source-relative extraction uses the original source clock.
  Recorded history uses the existing as-of cutoff, not an invented recorded-range API.
- Optional Qwen interpretation defaults off: at most one bounded schema-constrained
  foreground P0 call for unresolved phrasing, with no retry loop or write/access
  authority. Unsupported anchors and ambiguity clarify. Deep recall still performs
  no extra planning or summarization generation.
- Native acceptance and operator approval remain different authorities. Capture,
  source selection, profile settings, archive grants and model identity remain
  independent. Enrichment only applies reviewed temporal proposals; it neither
  approves new semantic claims nor silently updates seeded character copies.
- Historical selections validate against their original query scope; current
  forgetting, revocation, source selection and suppression govern every delivery.
  Current profile pins remain current. Keep the combined 256-reference/64-KiB cap.
- Durable storage grows with sources, memories, distinct supports and capture
  checkpoints. Use indexed/cursor-based bounded batches, not all-pairs clustering,
  full biography generation, full-history resnapshotting or a new persistent graph.
  Source policy scales with configured libraries rather than copied text per memory.
- Existing exact vector search still depends on eligible corpus size; do not promise
  constant-time vector lookup. Retain scoped native limits, query-plan inspection and
  measured cold/warm behavior. Add a small rebuildable projection only when needed
  to satisfy an actual lookup/performance gate.
- Deep starts with six anchors from at most 40 results; two hops, 256 inspected items,
  24 selected memories and 12 chunks, 12 search/24 total ABI calls, three uncached
  query embeddings, 24 bounded evidence spans, 16 MiB request working allocation,
  one active investigation and five seconds including queue time. Keep the PLAN's
  other byte/row/queue limits; count nested work and retain native leases until drain.
  Larger inspection results do not mean larger model prompts.
- Foreground admission, source availability and generation leases stay authoritative.
  A queue limit does not bound CPU/I/O contention by itself. Measure and defer deep
  semantic/derived work when residual native contention violates the gate.

## 6. Failure behavior and operational isolation

Unavailable index/encoder permits bounded labelled lexical/structured fallback, never
an unbounded archive scan. Undated events are omitted from strict event filters with
honest coverage; no-match is not proof an event never occurred. Ambiguous dates,
antecedents, unsupported event anchors or unavailable historical validation clarify
or produce typed limited/unavailable outcomes without substituting current facts.

Capture queue/processing failures retain exact checkpoints and pending reasons.
Restart cannot duplicate approval, seed or speech. Unsupported imported candidates
remain pending/manual. Enrichment conflicts require revalidation, not blind overwrite.

Revocation/forgetting/source changes during search, inspection, caching or streaming
stop dependent output/history. Cancellation stops new admission; running native work
retains ownership until completion. SDK/session copies cannot become a second history
store. Interrupted voice archives truthful delivered content and partial status.

All work uses this clone's independent environment/data/runtime paths and test ports.
No editable install, production DB, source ZIP, gateway credential, service restart,
Ollama unload or GPU generation is performed against the ongoing ingest. Early tests
use fake upstream generation plus isolated synthetic stores and CPU components.
Real Qwen/audio/TTFT gates require a coordinated window after ingest is no longer
using the GPU; defer those checks explicitly rather than mark them passed.

## 7. Acceptance, performance evidence and review

Maintain a per-criterion implementation/test/evidence matrix. These are required
future checks, not tests already run in this proposal:

| AC IDs | Required acceptance evidence |
|---|---|
| P8-AC-01, P8-AC-02, P8-AC-03 | Ordinary Milo dialogue -> durable capture -> policy acceptance -> index -> restart recall; second-character isolation and capture-off retention |
| P8-AC-04, P8-AC-05, P8-AC-06 | Persistent local-only/imports-only/selected-source controls, before-limit filtering, profile/pins/support origins/cache/history, without granting archive access |
| P8-AC-07, P8-AC-08, P8-AC-09 | Connected realtime defers P3; disconnect/suspension drains safely; crash/restart/checkpoint dedup; inspect pending/rejected/status and degraded indexing |
| P8-AC-10, P8-AC-11 | Real browser text and CPU STT -> shared core/Qwen -> Kokoro audio; production static serving, managed/attached ownership and degraded feature health |
| P8-AC-12 | Exact category accounting, whole-turn trimming, serialized input/reserve, profile dedup and explicit overflow without backend truncation |
| P8-AC-13, P8-AC-14, P8-AC-15, P8-AC-16 | Explicit text/browser/spoken deep invocation, source-backed multi-record NAS reconstruction, temporal/event anchors, separately labelled observations and granted exact excerpts |
| P8-AC-17, P8-AC-18 | Mid-operation/stream permission changes, denied edges/hidden reasoning, cross-character isolation, cycles/fan-out/queue saturation, cancellation and native drain |
| P8-AC-19, P8-AC-21, P8-AC-22 | Compact deep prompt versus larger authorized inspector, progress/cancel/partial/deferred reasons, ephemeral result expiry/restart and persistent character settings |
| P8-AC-20 | Frozen curated deep precision/support recall and paired ordinary/deep/refresh contention benchmarks; conservative deferral when a concurrent mode fails |
| P8-AC-23 | Identical text/final-speech date resolution, intended time axis, London/DST/leap-year boundaries, date-only versus topical filtering before limits |
| P8-AC-24, P8-AC-25 | Supported date evidence/source-relative July-to-December case, coarse/unknown precision, reviewed enrichment replay/conflicts/origins/vector reuse and unchanged legacy jobs |
| P8-AC-26, P8-AC-27 | Ended episodes/current states/recorded history reach correct delivery; scoped follow-ups and ambiguous antecedents; current profile/privacy still enforced |
| P8-AC-28, P8-AC-29 | Default-off one-call interpreter, schema/anchor validation and failure/cancel; honest unknown-date/bounded coverage and plans versus completed events |
| P8-AC-30 | Separate parser, temporal SQL/index, validation, formatting, interpreter and complete-turn/audio TTFT measures under cold/warm/degraded/refresh contention |

Use fake ABI clients to prove architecture independence, sanitized source-backed fixtures
for semantics, actual installed BGE/SQLite/LiveKit for integration and browser automation
for user flows. Exercise original native/imported and independently seeded sources.
Keep extracted source attribution separate from proof an event really happened.
Missing tools/providers from later phases report unavailable; Phase 8 does not implement
the Phase 9 task framework, Phase 10 MCP or Phase 11 Odysseus to pass unrelated future tests.

Run locked uv setup, Ruff format/check, strict Pyright and the complete pytest suite.
Add frontend build/typecheck/tests and relevant browser/audio/end-to-end tests.
Reports distinguish executed, failed and environment-dependent/unrun gates.

Freeze representative query/data mixes and hardware/model/encoder identities. Run the
PLAN's matched conditions with at least 1,000 ordinary recalls per condition, including
cold/distinct and warm queries, varied/large archives, multiple characters and refresh.
Record all samples, p50/p95/p99, coverage/timeout rates, SQL/ABI/embedding counts,
memory/CPU/I/O, event-loop lag, prompt cost and final-answer TTFT/audio latency.

Retain the existing regression caps: p50 increase <= min(5 ms, 5%), p95 <= min(10 ms, 10%),
p99 <= min(20 ms, 15%), and timeout/partial/fallback increase <= one percentage point.
Keep the 150 ms soft retrieval deadline and 50-100 ms warm p95 aim. Passing relative
caps cannot hide a failing baseline; do not redefine the old Phase 5/7 evidence.
The optional interpreter's serial latency is separately reported, not counted as
a successful 150 ms end-to-end response. Curated deep evidence/authorization gates
must meet PLAN section 31, including its 0.85 precision/0.80 support-recall thresholds.

After implementation, run the requested Code Review workflow on the final diff/base,
reproduce each actionable finding, fix real defects with meaningful regression coverage,
and document dismissed findings with reachable-path evidence. Rerun affected checks
and review substantive fixes before completing the acceptance matrix.

The installed Code Review plugin launches reviews for provider-backed pull requests.
This session exposes its CI-diagnostics tool, not its review launcher; the origin is
self-hosted and needs verified provider support. No plugin review has run yet. Resolve
that entrypoint/provider prerequisite before claiming the requested review gate passed;
a local inspection is not silently reported as an @code-review run.

## 8. Documentation, delivery sequence and review stop

This proposal is the requested pre-code checkpoint. No production implementation,
environment install or runtime migration has begun.

Once architecture review is complete, implement in the order above and update the
acceptance matrix as evidence becomes available. Add an isolated Phase 8 runbook,
sanitized benchmark/review reports and setup/compatibility/schema guidance.

Record application integration in Root ADR 0015, engine/ABI/source/time/admission
ownership in Memory ADR 0009, and browser/audio/core/lifecycle ownership in Agent ADR
0006, using those next numbers only while they remain available. Extend/link the
existing design records and all ADR indexes; preserve their historical evidence.
Update PLAN/AGENTS status accurately, without treating interfaces as delivered features.

Do not mark Phase 8 implemented until all applicable AC have executable evidence,
performance/complexity gates pass (or their explicitly permitted conservative mode
passes), real service gates have run and requested review findings are addressed.
Unrun real-model/audio or unavailable review gates remain open. Stop for phase review
before Phase 9. No merge/deployment to the active ingest checkout is included here.
