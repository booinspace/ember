# 0011 — Task-local Hermes provider and model mapping

**Current status:** Historical. Task and Desktop adapters are removed; see
[Agent ADR 0015](0015-managed-opencode-preview.md). No existing private Hermes
state is migrated.

Status: task provider implemented; selected PC deployment uses the qualified WSL2
worker under the operator-authorized harness/dashboard amendment. Desktop is deferred;
the broader original Phase 8A adoption gate remains open.

[Root ADR 0021](../../../docs/adr/0021-hermes-delegation.md) owns the cross-component
decision. `HermesTask` implements the existing `AgenticTaskProvider.execute` contract;
`TaskRouter` retains durable admission, cancellation, terminal receipts, notifications
and artifact export. The optional `delegate` operation is additive and default-off.
No canonical schema/Memory ABI migration is needed.

The actual pinned third-party worker is external. Its safe-worker policy disables
ambient user configuration, memory/context injection, skill learning/review and
fallback routing. The selected Windows desktop runs only the task worker in Ubuntu WSL2/bubblewrap;
Core, voice, memory and the gateway remain Windows-native. The native Windows
AppContainer adapter remains available for separately qualified deployments. Three
explicit broker operations are installed: `searx_search`, optional `ember_recall`,
and `ember_finish`. Capability discovery and task grants are distinct.

`OpenAIModelBridge` maps only the qualified worker request subset into fixed P2
requests through the existing model client. Complete tool-call/result history is
validated in neutral contracts and preserved by both upstream adapters. Unsupported
fields/routes, altered pins, partial output, invalid citations, stale context and
unverified cleanup cannot publish task success.

See [runbook](../../../docs/phase-11-runbook.md) and
[acceptance record](../../../docs/phase-11-validation.md) for the tested configuration,
independent review, actual task evidence and supported limits.

The [native-client amendment](../../../docs/adr/0021-hermes-delegation.md#ordinary-native-client-amendment--4-november-2026)
adds an independent paired ordinary-chat ingress. `HermesChats` creates bounded
server-owned CharacterCore contexts through composition, shares the existing ABI,
prompts and sink, and routes one final generation through P0. Supported native SDK
request hooks preserve exact session/turn/request and tool-result identity. Search
is an explicit public preflight before recall; private context never enters a search
query. Response retries and delivery receipts revalidate dependencies. The operator
may opt into shared recall under the user's explicit deferred Desktop-copy purge
policy. The isolated one-shot P2 worker contracts above remain unchanged.

Stop speaking uses room-bound RPC ownership and each retained LiveKit SpeechHandle,
including task notices. Browser playback is silenced immediately; subsequent turns
have their own speech identity. Native model abort is still Phase 13 work.

The [tool-provider simplification](../../../docs/adr/0021-hermes-delegation.md#tool-provider-and-launcher-simplification--4-november-2026)
keeps separate voice/Desktop histories and the existing Core/task contracts. There
is no native voice-session bridge. External source is grouped in `scripts/hermes/`;
the repository launcher selects a complete standalone named Desktop profile. This
changes deployment paths, so explicit `search_command` paths must be updated when
installing it. Existing sealed task-worker copies and receipts stay valid.


The [harness/dashboard amendment](../../../docs/harness-dashboard-amendment.md)
adds a neutral startup factory and delegate_task function. HarnessToolBroker reuses
ToolRouter and MCPTask.invoke; existing approval/effect owners are unchanged.
The extended worker negotiates ember_tool plus a bounded explicit catalog;
legacy three-tool peers remain supported when no new grants are requested.
Copied MCP context pins current tool metadata/credential identity locally and
blocks later public search/external MCP calls. Pre-effect receipt headroom is
checked without enlarging the 16 KiB task guard. Changed bridge sources require
explicit resealing and platform isolation/SDK qualification.

Dashboard capture hooks are optional and observer failures cannot change delivery
or leave the Core/model bridge busy. Activity is content-free and bounded to 256
coalesced records. Up to eight immutable visible requests remain in memory for
five minutes; replacement, new Core input, revoked dependencies or shutdown retire
disclosure. No inspector is attached to the deferred Desktop's rewritten ingress.
Host sampling uses the installed psutil and optional nvidia-smi off-loop, cached
three seconds; routine browser polling carries no prompt/recall values. Exact
inspection is lazy, operator-authenticated and no-store. Phase 13 retains model
abort/release ownership, and Phase 12 will qualify harness-owned skills separately.

The selected WSL command is deployment configuration, not CharacterCore logic.
`owned_stdio` retains the Windows launcher job/pipes; the Linux controller reaps
its bubblewrap namespace on EOF and uses die-with-parent semantics. Actual tests
also kill the Windows controller and verify no nonce-marked Linux worker survives.
Shared SDK proofs retain complete tool history, oversized-input rejection and the
large escaped MCP receipt without raising the 16 KiB guard. The installed Linux SDK
is reused; there is no WSL model, gateway credential or additional weights copy.
