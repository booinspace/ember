# 0011 — Task-local Hermes provider and model mapping

Status: selected native Windows one-shot profile qualified; default-off pending
the original Phase 8A production-adoption gate.

[Root ADR 0021](../../../docs/adr/0021-hermes-delegation.md) owns the cross-component
decision. `HermesTask` implements the existing `AgenticTaskProvider.execute` contract;
`TaskRouter` retains durable admission, cancellation, terminal receipts, notifications
and artifact export. The optional `delegate` operation is additive and default-off.
No canonical schema/Memory ABI migration is needed.

The actual pinned third-party worker is external. Its safe-worker policy disables
ambient user configuration, memory/context injection, skill learning/review and
fallback routing. The selected native Windows deployment uses an AppContainer;
Linux bubblewrap reconnaissance remains a separate platform qualification. Three
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
