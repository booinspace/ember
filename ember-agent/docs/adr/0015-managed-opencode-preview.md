# Agent ADR 0015 — Managed OpenCode preview

**Status:** Default-off OpenCode 2.3.2 adapter for synthetic, non-sensitive tasks
only. [Root ADR 0025](../../../docs/adr/0025-managed-opencode-preview.md) tracks
operator setup, evidence and open acceptance gates.

## Boundary and lifecycle

- `runtime/harness.py` selects the installed adapter at runtime composition. Reuse
  `AgenticTaskProvider`, `TaskRouter`, `ToolContext`, `TaskResult` and the existing
  receipt/attention path. CharacterCore sees optional neutral `delegate_task`, not
  an OpenCode session API. Missing task credentials, a failed pin or a failed
  startup leaves delegation unavailable, not a different provider. The foreground
  model selects `delegate_task` with an empty argument object. CharacterCore binds
  the bounded current user message as the goal, never model-generated text or
  history. The native literal-text tools keep their existing checks.
- `runtime/opencode_harness.py` starts the pinned executable as a child on an
  ephemeral loopback port, using a temporary home, separate credentials, and
  in-memory OpenCode DB. Startup checks the executable digest, authentication,
  reported version/PID/origin, and expected configuration. It does not attach to
  an operator's OpenCode server or inherit the operator's project setup.
- OpenCode connects to a separate bearer-authenticated loopback model ingress.
  The ingress exists under Ember's ownership, but rejects model calls without an
  active task. The streaming bridge binds the task ID and deadline to Ember's P2
  model requests; Ember's gateway keeps sole runner admission and token budgets.
  No model override, separately loaded runner or fallback is selected by the
  adapter. Cancelling the subscriber does not prove native runner release.
- The adapter uses the pinned V2 session API to create one fresh session per task,
  submit the goal, await completion and validate terminal state, task message, agent,
  model and bounded output. Only final summary text and tool names/statuses are
  projected into the result, not the full OpenCode timeline. No task replay on an
  ambiguous response, interactive input, resume between tasks, or artifacts.
  Cleanup revokes ingress, attempts interruption if unfinished, and deletes the
  session; when cleanup cannot be verified, the worker is made unavailable and
  stopped. This is not a certified private-data purge contract.

## Build extension

[Root ADR 0026](../../../docs/adr/0026-managed-opencode-build.md) adds a separate,
explicit `build` task operation. It starts a pinned worker per task in a Linux
container, with bounded writable tmpfs, read-only image, no host mounts or external
network, and fixed-destination pipe relays. The container has no upstream credentials.
The model bearer is readable by same-task processes, but can only reach the task's
bound P2 ingress; Ember revokes it before retaining any preview. The worker chooses
read/edit/write/shell calls; Ember projects terminal text and tool statuses and validates
an immutable ZIP. A separate operator port grant allows one verified local HTTP
preview, not arbitrary network exposure. Unverified cleanup fences further builds.

## Public web research extension

[Root ADR 0027](../../../docs/adr/0027-public-web-retrieval.md) adds independent
native hosted retrieval and an opt-in Research worker. The same Ember-owned
adapter supplies bounded excerpts to Conversation and a private loopback MCP
broker. Provider credentials stay in Ember. Research gets no workspace tools,
other MCP servers, recall, skills, or direct retrieval route. A separate pinned
process and empty temporary workspace keep its static grants distinct from
Inspect; this is application authority, not an OS sandbox or firewall.

The broker checks a private worker bearer, active OpenCode session metadata,
and one-use invocation nonces bound to exact tool arguments. Ember issues nonces
only after validating the full P2 tool-call batch and hides them from subsequent
model input. Search inputs must match a `Search: <public query>` line. Read inputs
must be a standalone public URL line or an exact URL returned in this task.
Limits: three unique queries, three unique page URLs, eight model steps.
Submission binds consent to Exa REST, Exa MCP, or Parallel; changing providers invalidates the
queued grant. Cancellation fences model and broker calls before awaiting the
cancellation write or cleanup. If cleanup cannot be verified, Ember disables the
worker instead of reusing it. Each retrieval gets distinct source IDs. Receipts
prove retrieval provenance, not that sources support model claims. No source
means no successful research receipt.
[Keyless Exa MCP](../../../docs/adr/0027-public-web-retrieval.md#keyless-exa-mcp-for-conversation-and-tasks)
can serve Research through Ember's existing task-scoped broker. The worker does
not connect directly to Exa MCP.

## Inspect authority and platform limits

The managed policy permits only `read`, `glob`, and `grep` in the configured
workspace. The model bridge checks tool names and resolved paths before
forwarding tool calls; configuration and application path checks are not
race-proof filesystem confinement. The preview has no shell, edits, plugins,
external MCP grants, private recall, memory writes, live skill provider, public
search or page reading. Ember's separately configured SearXNG and direct MCP are
not routed through this adapter. The independent inbound memory MCP listener in
[Agent ADR 0012](0012-memory-mcp.md) is not attached to it.

The reused owned-worker helper provides Windows Job Object setup/teardown and
POSIX process-group cleanup. Neither is a filesystem sandbox or outbound network
policy. There is no OS sandbox or enforced network egress boundary. A real tool
loop on macOS was observed with a deterministic upstream inside Ember's gateway,
not live Strata. Windows process ownership for this binary and Linux operation
are unqualified. Never use private repositories, archives, credentials or personal
context with the preview. Production or private-data adoption would require an
OS-enforced boundary and separate qualification, not simply passing local tests.

[Agent ADR 0011](0011-hermes-delegation.md) is historical. The provider half of
[Agent ADR 0013](0013-shared-harness-skills.md) is not live. No Hermes profile,
chat, credentials, or private coding notes are migrated.

## Natural-language Conversation tool planning amendment

**Status:** Implemented and tested with scripted backends. No live rollout. See [PLAN criteria](../../../PLAN.md#conversation-tool-planning-preview-acceptance)
and [Root ADR 0027](../../../docs/adr/0027-public-web-retrieval.md#natural-language-conversation-tool-planning-amendment).

- Character runtime makes a separate, bounded planning request through Ember's
  existing local inference gateway. Input is the current user turn, fixed tool
  definitions, bounded untrusted same-turn public evidence, and staged task status.
  No recall, profile, prior chat, provider credentials or workspace results go to
  the planner. Missing local planning means no natural-language tool dispatch; no
  hosted fallback. Explicit command shortcuts retain their existing checks.
  Ordinary reply and recall behavior is unchanged.
- Advertise only configured, eligible public search, public page read, Inspect
  and Build operations. Validate one typed proposal at a time: reject batches,
  unknown fields and invalid arguments before effects. Model output cannot grant
  authority, choose provider, supply trusted identities or declare prerequisites
  satisfied. Runtime checks current-turn intent and standalone task goals; ask
  for clarification when those cannot be established. Bound steps, inputs and
  time; no open-ended loop.
- Reuse Ember retrieval scope checks and exact-action approval patterns. Send an
  unambiguous, user-requested literal query or single URL only after validation.
  Otherwise show the exact proposed public input when safe. The operator must
  send a new provider-specific command or restate the chain with exact input.
  There is no pending approval, generic yes response, or automatic resumption.
  The new turn is checked independently and cannot override scope checks.
- Reuse `TaskRouter`, durable task records/receipts and gateway admission; no new
  worker or generic workflow service. New coupling is a typed durable binding
  between a dependent Build, originating turn and required receipt identities.
  Validate prerequisites before dispatch. Public retrieval may run during chat;
  Inspect and Build remain P2 and can run while Conversation stays connected.
  The trusted task client uses idle P2 admission for Inspect, Build, Web Research,
  and text Summary. Foreground work wins at step boundaries. Active native steps
  are not interrupted.
- Give Build a bounded, untrusted Inspect summary, not workspace access or raw
  tool output. Carry verified public source references into its receipt when used;
  never grant Build retrieval. Failure, cancellation, expiry or uncertain outcome
  blocks the dependent Build; no silent downgrade or replay. See
  [Root ADR 0026](../../../docs/adr/0026-managed-opencode-build.md#conversation-planning-and-build-prerequisites-amendment).
