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
credentials or timeout leave checkpoints pending. Optional calendar interpretation defaults off; no connected-session background
inference is added.

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
No extra planning, model summarization, ownership or implicit automatic deep trigger
is introduced. Completed embedding-cache hits are reused; new deep CPU encodes are
deferred until the stronger shared-resource gate is measured.

A coalesced room connection lifecycle drains media before release of the gateway
reservation and stops new extraction steps on reconnection. Background capture uses
the durable engine checkpoints and existing P3 profile. The optional calendar
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

A demand-refreshed browser summary reports capture/acceptance/index counts. Idle
native backlog self-continues in bounded batches, pauses on reconnect and skips
failed checkpoints until explicit retry. No new background inference scheduler or
per-turn status/profile machinery is added.

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
