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
