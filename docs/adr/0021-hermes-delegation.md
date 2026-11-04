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
