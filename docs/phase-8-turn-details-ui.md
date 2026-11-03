# Phase 8 live turn details and composer controls

The user requested a thinking display, expandable recalled facts, automatic
scrolling after sending and Enter-to-send before proceeding to Phase 9.

## Behavior and ownership

The existing browser UI gains a Thinking toggle and two collapsed details panels
for the current/latest reply. Thinking is opt-in because the existing runtime
disables it. Only the primary model's explicit thinking channel is displayed;
there is no invented reasoning or auxiliary generation. Thinking never enters
speech, conversational history, archive, extraction, indexes, logs or reports.
It exists only in a bounded in-memory current-turn view and the authenticated
browser's memory. The user explicitly authorized displaying this model output;
the existing prohibition on persisting hidden reasoning remains.

CharacterCore owns a single ephemeral current-turn view. After final prompt
selection/certification it records the actual selected profile, ordinary and deep
accepted-memory values. The view retains the exact delivery certificate privately.
The existing authenticated browser-control server exposes the view and thinking
setting to both LiveKit and HTTP text clients. Every view read validates current
memory permissions; invalidation clears recalled values and thinking. No extra
memory search, source hydration, database or Phase 9 event bus is introduced.

The gateway adds negotiated `live_thinking_v1` and experimental wire 0.4.0.
Only explicit P0 text requests can opt in. Earlier versions/default requests retain
their previous output bounds and omit the new false setting. Thinking requests
fit the configured output reserve, with a maximum 8,192 output tokens; the browser
feature requires at least a 4,096-token reserve. The sole gateway remains the only
generation path. Structured/background workloads never expose thinking.

The current-turn view holds at most 100 displayed memory values, 32 KiB of thinking
and 128 KiB total serialized data. Thinking overflow is labelled and omitted from
display without changing the generated answer. Browser polling is serialized at
500 ms while connected, cancels on teardown, and retains only the latest view.
Memory/body values render as escaped text. Inspection is current permission checked,
not a diagnostic export or persistent transcript. Disconnecting/restarting does not
grant a second session or change source activation.

Enter submits once; Shift+Enter inserts a newline. IME composition and repeated
keydown events cannot accidentally submit. Sending synchronously reserves the
frontend turn to prevent duplicate submissions. The message viewport follows each
sent/final transcribed user message; assistant updates follow only while the user
has stayed near the bottom.

## Acceptance

- Synthetic gateway frames prove opt-in thinking reaches only its distinct event
  channel, never visible text/history/archive; old requests and P3/P4 remain unchanged.
- Real engine/composer tests prove the expansion contains final selected values,
  with subject/modality/time preserved and omitted candidates excluded.
- Authenticated route tests cover thinking settings, output budget, display bounds,
  unknown/invalidated context and revocation without exposing private fixtures.
- Frontend/browser checks cover expanding escaped thinking/facts, toggle state,
  Enter versus Shift+Enter, IME/repeat handling, duplicate submission, scrolling,
  polling cleanup and both transport modes.
- Run installed quality gates and verify layout before activating the changed
  runtime through its existing owned lifecycle. No Phase 9 implementation starts.
