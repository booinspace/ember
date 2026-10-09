# Phase 8 implementation validation

Status: in progress, not complete. Work is isolated from the primary checkout and
ongoing private ingest, starting at origin/master 640888d.

The latest complete suite run passed **511 tests with 11 skipped** in 107.81 seconds.
Skipped gates include opt-in real model/LiveKit/embedding integrations. This run
preceded the latest CLI temporal-profile wiring. Subsequent checks must be recorded
after those edits; do not treat the earlier run as validation of later changes.

Focused evidence covers persisted native/import selection and restored controls,
ended-event recall, historical state and recorded revisions, London DST/leap dates,
date ambiguity/follow-ups, source-relative event extraction, native capture restart
and duplicate acceptance, reviewed enrichment and concurrent revision rejection.
No primary archive, memory database, gateway or GPU process was used.

## AC tracking

| Acceptance criterion | Current evidence and remaining work |
|---|---|
| P8-AC-01 | Atomic native capture checkpoint and idle P3 drain added; audible delivery and full runtime integration remain open |
| P8-AC-02 | Synthetic native capture/acceptance/recall test passes; actual later browser/voice-session demonstration open |
| P8-AC-03 | Capture and origin recall settings are independent; tested across restart |
| P8-AC-04 | Persisted controls, CLI and bundle tests added; runtime config/browser controls open |
| P8-AC-05 | Origin filters cover canonical/indexed search, profile, pins, archive and certs; complete restrictive-before-top-k tests/performance open |
| P8-AC-06 | Policy changes invalidate retained certificates; full retained-history/browser/audio matrix open |
| P8-AC-07 | Existing gateway P3 admission reused after reservation release; runtime idle/suspension integration open |
| P8-AC-08 | Durable exact-source jobs, receipts and restart tests pass; transport delivery replay/cancellation matrix open |
| P8-AC-09 | CLI capture and enrichment decisions inspectable; browser reasons/provenance open |
| P8-AC-10 | Browser/STT/Kokoro delivery not yet implemented |
| P8-AC-11 | Existing single-command LiveKit text composition retained; audio/browser ownership/readiness open |
| P8-AC-12 | Existing prompt ceilings/six memories retained; full category accounting and audio qualifiers open |
| P8-AC-13 | DeepRecallService not yet implemented |
| P8-AC-14 | Curated deep NAS association test not yet implemented |
| P8-AC-15 | Local calendar/DST/leap/ambiguity tests pass; deep supported event anchors and device periods open |
| P8-AC-16 | Archive-only/exact-evidence deep follow-ups open |
| P8-AC-17 | Current source policy and historical certificates tested; full deep and audible revocation matrix open |
| P8-AC-18 | Deep resource budgeting/admission/cancellation not yet implemented |
| P8-AC-19 | Bounded deep result/condensation not yet implemented |
| P8-AC-20 | Deep quality and paired contention measurements not yet run |
| P8-AC-21 | Browser mode/progress/cancel/coverage controls not yet implemented |
| P8-AC-22 | Native/source controls and character identities persist; complete browser/audio restart evidence open |
| P8-AC-23 | Ordinary calendar resolver is wired into CharacterCore; voice and real scoped answer test open |
| P8-AC-24 | Separate extractor 0.3.0 resolves supported last December against July source time; full coarse/event-classification matrix open |
| P8-AC-25 | Persisted reviewed character enrichment, replay and CAS test pass; seeded-library isolation/vector reuse/CLI matrix open |
| P8-AC-26 | Ended episode, historical-state and recorded-revision certificates pass; full revoke/suppress/index matrix open |
| P8-AC-27 | Typed last-scope follow-ups and ambiguity tests pass; real multi-turn and scope lifetime matrix open |
| P8-AC-28 | Optional interpretation remains off and unimplemented |
| P8-AC-29 | Ordinary prompts disclose strict event-time undated exclusions; engine coverage/planned-action matrix open |
| P8-AC-30 | Separate temporal/parser/validation/fallback and paired regression benchmarks open |

## Review and release gates

The requested code-review plugin has no exposed local-review launcher in this tool
session; its bundled provider review requires an accessible provider PR. No plugin
review has run. Resolve that gate before claiming requested review complete.
Ordinary local analysis and tests do not substitute for the requested plugin.

The ongoing primary ingest must finish or an explicitly authorized idle GPU window
must exist before real Qwen/voice acceptance and inference timing. Existing Phase 7
measurements are historical evidence, not Phase 8 interpretation/audio measurements.

See [Root ADR 0015](adr/0015-phase-eight-implementation.md) and the approved
[implementation proposal](phase-8-implementation-proposal.md).
