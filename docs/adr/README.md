# Architecture decision records

This is the starting point for Ember architecture review. [PLAN.md](../../PLAN.md)
is the authoritative design, phase order and engineering standard. ADRs record why
an architectural choice was made, its owner, coupling, invariants, failure behavior,
acceptance evidence and remaining limits. They do not make a planned feature live.

## Phase status and decisions

| Phase/decision | Root record | Delivered status | Component detail |
|---|---|---|---|
| Phase -1: preparation | [Root ADR 0001](0001-phase-minus-one-scope.md) | Scaffold, offline inspection/configuration and shared engineering tooling implemented | [Memory ownership](../../ember-memory/docs/adr/0001-ownership.md); [agent composition boundary](../../ember-agent/docs/adr/0001-composition-and-gateway.md) |
| Project naming | [Root ADR 0002](0002-ember-name.md) | Ember application/component naming implemented | Both package READMEs and CLI aliases |
| Phase 0a: reconnaissance | [Root ADR 0003](0003-phase-zero-reconnaissance.md) | Preparation implemented; official ZIP now inspected, phase 0b source-dependent acceptance still open | [Research](../phase-0-research.md); [source model](../phase-0-source-model.md); [current findings](../../PLAN.md) |
| Phase 1: ABI/archive | [Root ADR 0004](0004-phase-one-contracts.md) | Experimental ABI/archive 0.1.0 implemented; source-dependent and ABI 1.0 acceptance open | [Memory ADR 0002](../../ember-memory/docs/adr/0002-phase-one-before-export.md) |
| Phase 2: canonical engine | [Root ADR 0005](0005-phase-two-canonical-engine.md) | Engine 0.1.0 implemented and tested; architecture review checkpoint before phase 3 | [Memory ADR 0003](../../ember-memory/docs/adr/0003-canonical-engine-and-workers.md); [walkthrough](../phase-2-canonical-engine.md) |
| Phase 3: importer/realtime slice | [Root ADR 0006](0006-phase-three-source-and-runtime.md) | ZIP import, P0 and runnable managed LiveKit text slice verified; operator-reviewed claim adoption remains open | [PLAN section 11](../../PLAN.md); [Memory ADR 0004](../../ember-memory/docs/adr/0004-source-import-and-authoring.md); [Agent ADR 0002](../../ember-agent/docs/adr/0002-phase-three-p0-turn-slice.md); [Agent ADR 0003](../../ember-agent/docs/adr/0003-managed-livekit-text-runtime.md) |
| Phase 4: libraries/characters/bundles | [Root ADR 0007](0007-phase-four-libraries-characters-bundles.md) | Implemented; 332 tests passed including actual LiveKit/Qwen; architecture review checkpoint | [Memory ADR 0005](../../ember-memory/docs/adr/0005-libraries-seeds-and-bundles.md); [Windows runbook](../phase-4-runbook.md) |
| Phase 5: retrieval/indexes | [Root ADR 0008](0008-phase-five-retrieval-and-indexes.md) | 360 tests passed including real BGE/LiveKit/Qwen; architecture review and full performance/capacity acceptance open | [Memory ADR 0006](../../ember-memory/docs/adr/0006-derived-retrieval-and-generations.md); [runbook](../phase-5-runbook.md) |
| Phase 6: full extraction | [Root ADR 0009](0009-phase-six-extraction-and-background-inference.md) | Extractor 0.1.2 exact-evidence/schema fix; 408-test full gate passed; five-window private archive sample produced four pending proposals; architecture review pending | [Memory ADR 0007](../../ember-memory/docs/adr/0007-extraction-jobs-and-summary-sources.md); [Agent ADR 0004](../../ember-agent/docs/adr/0004-conservative-background-gateway.md); [operator runbook](../phase-6-runbook.md) |
| Phase 7/8 criteria and deep-recall design | [Root ADR 0010](0010-planned-deep-associative-recall-and-phase-acceptance.md) | Acceptance IDs and Phase 8 deep-recall architecture remain authoritative; Phase 7 implementation is recorded in Root ADR 0013 | [PLAN retrieval design](../../PLAN.md#deep-associative-recall-planned-phase-8-architecture); [phase criteria](../../PLAN.md#acceptance-evidence-and-phase-completion) |
| Phase 7 bounded user profile design | [Root ADR 0011](0011-planned-phase-seven-user-profile.md) | Historical design, now implemented experimentally and extended by Root ADR 0013 | [PLAN profile design](../../PLAN.md#bounded-user-profile-phase-7-implementation); [Phase 7 criteria](../../PLAN.md#phase-7-acceptance-criteria-native-activation-and-consolidation) |
| Phase 7 implementation proposal | [Root ADR 0012](0012-planned-phase-seven-implementation.md) | Historical approved pre-code proposal; delivered implementation/evidence in Root ADR 0013 | [Implementation proposal and AC matrix](../phase-7-implementation-proposal.md); native authority, consolidation/time, profile integration and performance gates |
| Phase 7: native policy, consolidation/time and bounded profile | [Root ADR 0013](0013-phase-seven-native-consolidation-and-profile.md) | Implemented experimentally in isolated worktree; full AC matrix and performance limits recorded; awaiting architecture review | [Phase 7 validation](../phase-7-validation.md); [runbook](../phase-7-runbook.md); [Memory ADR 0008](../../ember-memory/docs/adr/0008-native-consolidation-and-profile.md); [Agent ADR 0005](../../ember-agent/docs/adr/0005-bounded-profile-and-delivery-validation.md) |
| Phase 8 conversational temporal recall design | [Root ADR 0014](0014-planned-conversational-temporal-recall.md) | Accepted design only; ordinary time-axis queries, reviewed event-date enrichment, historical delivery and P8-AC-23 through P8-AC-30 remain planned | [PLAN temporal recall](../../PLAN.md#conversational-temporal-recall-planned-phase-8-architecture); [Memory ADR 0008 extension](../../ember-memory/docs/adr/0008-native-consolidation-and-profile.md#planned-phase-8-temporal-extension); [Agent ADR 0005 extension](../../ember-agent/docs/adr/0005-bounded-profile-and-delivery-validation.md#planned-phase-8-temporal-extension) |


Phase 0b's supported export inspection and import are verified; saved-memory
inclusion remains unestablished. Phase 3 implementation and private import passed
the recorded checks; exact-claim operator review/adoption remains open. Phase 4 library/character/bundle
implementation is recorded separately in Root ADR 0007. Phase 5 implementation and open acceptance gates are recorded in Root ADR 0008.
Phase 6 implementation and opt-in audited window retry/continuation are recorded in
Root ADR 0009. Phase 7 implementation and its current architecture-review checkpoint
are recorded in Root ADR 0013.
Phase 6 now also includes interactive ingestion/review/seeding with durable human
decisions and restart-safe indexing; see Root ADR 0009 and Memory ADR 0007.

Phase 7 native policy, consolidation/time and profile integration are implemented
experimentally in the isolated worktree. [Root ADR 0013](0013-phase-seven-native-consolidation-and-profile.md)
and its linked component ADRs record owners, coupling and behavior; the
[acceptance matrix](../phase-7-validation.md) distinguishes checked requirements from
open global capacity/platform gates. The worktree has not been applied to the primary
checkout or its production data. Stop at architecture review before Phase 8.
[Root ADR 0011](0011-planned-phase-seven-user-profile.md) and [ADR 0012](0012-planned-phase-seven-implementation.md)
remain historical design records; their current links point to the implemented PLAN section.
Early grant/suppression/temporal primitives in phase 2 do not close the later
seeding, bundle, indexing, extraction or consolidation acceptance gates.

## How the documentation trees relate

| Tree | Scope | Relationship to this index |
|---|---|---|
| `docs/` | Application-wide phase status, walkthroughs, compatibility, shared standards and runbooks | Supplies phase evidence and operational explanations |
| `docs/adr/` | Overall phase decisions and integration between the two first-party components | Tracks delivered behavior and links the owning component's detailed decision |
| `ember-memory/docs/` | Memory product decisions: ABI/archive, storage, evidence, policy and worker lifetime | [Memory ADR index](../../ember-memory/docs/adr/README.md) holds memory-owned technical detail |
| `ember-agent/docs/` | Character/runtime decisions: composition, prompts, gateway, realtime and providers | [Agent ADR index](../../ember-agent/docs/adr/README.md) separates design boundaries from shipped runtime |

The root phase ADR describes the application consequence; the component ADR owns
its implementation detail. Link between them rather than duplicating a detailed
schema, policy or scheduler specification. Runtime behavior remains in code and
acceptance tests, with release status in [compatibility](../compatibility.md).
PLAN takes precedence if a record conflicts with it; reconcile the affected ADRs
explicitly rather than treating historical text as the current design.

All three ADR trees have independent numbering. **Root ADR 0003 is phase 0a;
Memory ADR 0003 is the phase 2 engine.** Qualify references with the component or
link the exact path. Numbers identify decisions, not phases. Existing records
retain their IDs and links when a later decision extends them.

A component may eventually move to its own repository, as PLAN allows. Its own
ADRs stay with that component; workspace records explain their integration.
That packaging boundary creates no additional canonical owner or runtime dependency
between documentation trees.

## Maintenance and review

For every phase, use PLAN's eight checks: desired behavior, owner, existing
abstraction, necessary new primitive, contracts/invariants, failures, acceptance
and implementation. Record introduced coupling and meaningful tradeoffs. Update
this index, the root phase ADR and affected component ADR in the same change.
A phase primarily owned by one component does not require an invented decision in
the other; link its existing interface/ownership boundary when relevant.

Keep original decisions and historical validation identifiable. Mark a changed
choice as extended or superseded and link its replacement; do not rewrite a past
scaffold restriction into a permanent ban. Separate accepted design, implemented
behavior, pending review and unfulfilled acceptance gates. Summarize the tested
phase and stop for user review before beginning the next one.

Phase 8 implementation is in progress: [Root ADR 0015](0015-phase-eight-implementation.md) and
[acceptance status](../phase-8-validation.md) record runtime/capture, calendar,
deep source/alias recall, enrichment, measured performance and remaining gates.
The experimental main-device runtime has installed browser/speech evidence; the full phase remains incomplete. See the Phase 8 runbook for the single launch command.

The [2026-10-16 review](../reports/phase-8-architecture-review-20261016.md) and
[remediation design](../phase-8-review-remediation.md) cover source qualification,
selected prompt certificates, capture resumption and degraded browser operation.

The user-directed [ordinary word-recall correction](../phase-8-word-recall.md)
supersedes the ordinary six-row/768-byte limits with word-ranked candidates and
bounded extracted-value packing. Root ADR 0015 and both component Phase 8 ADRs
record this change; deep bounds and remaining release gates stay explicit.

The subsequent operator-approved 32k activation and bounded live Connect/recall
checks are recorded in Root ADR 0015 and the phase validation record.

The [browser turn-details amendment](../phase-8-turn-details-ui.md) adds ephemeral
thinking/selected-recall expansions and keyboard/scroll controls. Root ADR 0015 and
Agent ADR 0006 record its passing checks and pending UI deployment authorization.

Planned: [Root ADR 0017 — Hermes and Windows runner evaluation](0016-planned-hermes-and-runner-evaluation.md).
In progress: [Phase 9 operational tasks](0017-phase-nine-operational-tasks.md).

The [LAN HTTPS reverse-proxy extension](../phase-8-lan-https-design.md) uses
explicit Nginx trust and direct operator admission. Isolated tests and live HTTPS
control checks pass; room join, client microphone and media acceptance remain open.

The [long-speech correction](../phase-8-long-speech.md) records the VAD padding
fix, ordered CPU transcription chunks and remaining five-minute limit.

The long-speech correction also records managed CPU Phonon-2 input setup and
its remaining physical-microphone acceptance gate.

The [mobile browser layout](../phase-8-mobile-ui.md) records responsive and semantic
frontend changes and the outstanding real-device checks.

The [voice delivery recovery](../phase-8-voice-delivery-recovery.md) records the
missing-receipt fix and LiveKit handle/task coupling.

## Independent voice/UI amendment

The operator requested CPU Phonon voice/speed controls and immediate browser reply
text while Phase 9 continues. The [design and validation](../voice-controls-and-read-ahead.md)
retain synchronized played-prefix receipts, installed-only voice choices and the
existing room/audio owner. This adds no memory/schema, task or inference-scheduler
contract and does not reopen Phase 8 or advance a future phase.

Implemented: [Root ADR 0018 — Shared system and character Markdown prompts](0018-shared-character-prompts.md).
The [design/runbook](../personality-prompts-proposal.md) records the independent editor amendment;
Phase 9 task, memory and inference authority remain unchanged.
