# 0019 — Direct MCP through the existing tool/task owner

Status: implemented; original Phase 10 acceptance verified for the documented profile.

Ember Agent adds the pinned official MCP SDK behind a bounded adapter. Neither the
Memory ABI nor CharacterCore learns MCP transports or grants. Explicit deployment
configuration and per-tool policy authorize invocation; discovered metadata and
server annotations remain untrusted. Native tools work with MCP and the harness
absent. The initial interaction is the authenticated operator tool UI.

The existing TaskRouter owns submissions, attempts, confirmations, effects,
receipts, cancellation and terminal notifications. One additive typed invocation
payload extends its existing task contract; legacy payloads and idempotency still
work. MCP clients/processes and credential scopes are separate for each runtime
actor and call. There is no additional job owner, model router or memory writer.

Normalized IDs bind configuration, schemas, metadata, grant/effect and credential
identity. Write approval displays a readable server/tool/effect plus exact args.
The durable fence precedes effect invocation. Trusted adapter dispatch evidence
distinguishes known non-dispatch failures from unknown effects. A no-dispatch fence
is still consumed; automatic replay remains prohibited. Cancelled result data may
be suppressed while the durable outcome remains inspectable.

The adapter bounds wire data, metadata, schemas, calls and outputs, rejects remote
schema references/regex/combinators and unsupported execution/sampling requests,
and never follows server resource links or creates model/tool authority from text.
Owned process groups/jobs close on cancellation, with native Windows job drain
verified before shutdown returns. Server failure degrades only that provider.

The supported service profile is atomic/stateless and operator-trusted. It does not
claim OS sandboxing or persistent in-memory stdio workflows. Conversational MCP
loops are not enabled by this phase. Removing MCP leaves personality, native
conversation, memory, capture and the inference gateway usable.

[Runbook](../phase-10-runbook.md) owns setup/profile details;
[validation](../phase-10-validation.md) maps the original six acceptance criteria
and records the independent review and benchmark paper trail.
[Agent ADR 0009](../../ember-agent/docs/adr/0009-direct-mcp.md) owns component details.

## Shared outbound client

Direct MCP reuses Ember's outbound client with the existing SDK transports and
process ownership. It still lists and checks approved metadata before dispatch
and performs one low-level call without SDK tool retries. Nonterminal results
are rejected before legacy SDK normalization can remove their status marker.
An approved write with such a result stays uncertain and is not replayed.
Public web retrieval
uses a distinct transport profile and bindings; it does not inherit any direct
server, schema grant, credential, or write confirmation. Neither outbound profile
changes the independent inbound Memory MCP. See [ADR 0027](0027-public-web-retrieval.md#declarative-provider-bindings).
