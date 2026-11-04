# 0021 — Isolated Hermes task delegation

Status: selected native Windows one-shot profile qualified; production adoption
remains gated by Phase 8A. Acceptance evidence is in
[Phase 11 validation](../phase-11-validation.md).

Hermes is the selected optional agentic provider. Ember retains its character,
canonical memory, task store/outbox, tool authority and sole inference gateway.
No upstream framework is vendored or added to either product environment.

The supported profile is one bounded, read-only delegated workflow. An external
Hermes checkout and its Python 3.14 environment are pinned separately from Ember's
Python 3.12 runtime. The operator selected native Windows deployment; its
AppContainer isolation passed native positive/negative and crash-recovery probes. Linux bubblewrap was used
for initial SDK reconnaissance and is retained as a separate platform adapter,
not the Windows deployment. The worker has a temporary home, isolated networking
and only brokered tools. Native macOS worker isolation is not claimed; controller
and model/task contracts remain independent of the worker OS.

Task-local OpenAI mapping carries complete assistant/tool-result IDs to the
existing authenticated P2 gateway. An HTTPX transport carries SDK requests over
owned standard input/output without opening a listener. It has no runner
credentials or fallback route. Existing Ollama/vLLM requests without tool history retain their
serialized contracts. All supported model calls are capped by the task deadline,
12-round limit and 16,384-byte conservative input guard; the actual runner context
capacity is reported separately. Output remains at most 1,192 tokens per step.

SearXNG requests go through the native read-only ToolRouter at one operator-selected
origin. Each response copies at most 2,048 UTF-8 JSON bytes of complete references,
without changing the native receipt. Only copied references receive citation IDs.
The worker uses the pinned SDK's supported one-shot and concise guidance settings;
its complete dialogue still passes the unchanged 16,384-byte input guard. Before
each model call, validated host policy removes exhausted/ungranted tools and
restricts finish citations to issued IDs. The original request is validated before
projection, and the projected request is validated again without changing its
server-owned context, model, workload or deadline.
Only bounded search snippets and public HTTP references are returned;
reference URLs are not fetched. Structured completion may cite only broker-issued
references. Context is not promoted into canonical memory.

Optional personal recall uses the existing ABI/deep service with no archive
hydration, at most six accepted memories and a 1,024-byte copied-context ceiling.
Current dependencies are checked before every disclosure/model step, while the
worker runs, and before result publication. Public search is denied after private
context disclosure. Worker context is ephemeral; success requires an exact verified
purge receipt and clean process exit. Interrupted tasks are reconciled by the
existing task owner and never automatically replayed.

The profile advertises no interactive user-input, mid-run update or execution-resume
capability. Changed goals require a new explicit task. Existing native/MCP approval
flows remain independent. Arbitrary shell/file tools, autonomous learning, independent
personal memory and cloud fallbacks are excluded from the worker profile.

Hermes Desktop/messaging ordinary chat and cross-interface transcript continuity
are separate integrations. Pointing Hermes at an inference endpoint does not apply
Ember's CharacterCore prompts, recall or capture automatically. Future channels must
bind sender/session identity to that owner rather than create a second personality
or personal-memory authority. Phase 12 may add reviewed skill proposals; session
review/checkpoints are future memory amendments, not automatic writes from this worker.

## Ordinary native client amendment — 4 November 2026

A separately paired Hermes Desktop profile now maps normal chat to CharacterCore,
then the existing P0 gateway. Server-owned per-chat contexts share the character,
prompt files, ABI and capture owner; client transcript/system text is not prompt
authority. Native supported context/middleware/tool-result hooks project only the
current original user input, an exact previous-delivery echo and complete current
search IDs. Input remains 16 KiB and output at most 512 tokens. Native context
capacity metadata is truthful and does not enlarge the prompt budget.

Explicit public search runs before recall, through the installed SDK SearXNG
provider. A bound public result precedes one character generation; no private-derived
query or later search is permitted. The browser voice path reuses that provider
without depending on P2 admission during its own realtime session. Stop speaking
adds a retained exact-handle interrupt to the existing LiveKit owner, including fixed
task notices; it does not change model cancellation/draining contracts.

The user explicitly deferred copied-chat deletion after the actual Desktop retention
canary failed to clear its selected renderer. Optional private recall may therefore
be enabled with that known local-copy gap. Current ABI authorization is still checked
for generated content, buffered deliveries, stream deltas/retries and next-turn
receipts. Hermes' independent memory/learning remains disabled. Automatic deletion
of affected Desktop chats, durable cross-restart chat continuity, messaging and the
remaining Phase 8A adoption/performance criteria remain open. This authorization
amends the prior private-recall/purge gate for the dedicated client only; it does not
weaken the isolated worker's purge receipt or tool authority.

## Tool-provider and launcher simplification — 4 November 2026

The operator chose tool delegation instead of voice-session mirroring. Keep the
qualified direct provider path for explicit voice search; create no native chat and
make no extra model call for that preflight. Desktop ordinary chat remains paired
with CharacterCore and the single inference gateway. The unfinished alternative
voice-session bridge is removed, including its config, protocol and runtime hooks.

External adapter source is grouped under `scripts/hermes/` with an inventory.
`start-hermes.ps1` at the repository root selects a regular named Ember profile.
Private profile state stays in Hermes' supported user storage. The named profile is
published complete and standalone, preventing its process-global provider hook from
being adopted by other profiles' shared backend. Old profile/chat data is preserved;
no transcript merge, capture-owner change or memory/schema migration is introduced.
