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

Native capture is engine-owned; LocalRuntime may drain a bounded batch only after
releasing its realtime gateway reservation. Use the existing P3 HTTP adapter and
conservative native acceptance, never imported approval authority. Missing background
credentials or timeout leave checkpoints pending. No Qwen fallback is enabled and no
connected-session background inference is added.

## Acceptance boundaries

Calendar, DST/leap-year, historical delivery and source-backed extraction have
synthetic acceptance tests. Browser controls/audio delivery, optional interpretation,
deep investigations and real GPU/audio latency remain open. Current LiveKit text
delivery does not yet establish audible playback acknowledgement.

[Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md) records integration,
scope and open gates. [Agent ADR 0005](0005-bounded-profile-and-delivery-validation.md)
retains historical Phase 7 evidence.
