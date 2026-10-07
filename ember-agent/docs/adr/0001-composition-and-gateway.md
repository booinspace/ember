# ADR: composition owns concrete construction and model routing

Status: accepted from the supplied design.

Character logic consumes the public AsyncMemoryClient contract once implemented.
Composition constructs the in-process engine and lifecycle handles. The optional
local-deployment packaging extra is installation wiring, not permission for core
modules to depend on engine internals.

One agent-owned gateway routes every primary-Qwen generation request. It begins
with P0 in phase 3, adds P3/P4 in phase 6 and P1/P2 in phase 9. Preemption remains
disabled until actual cancellation/capacity-release measurements pass acceptance.
No direct Ollama generation fallback is permitted. Metadata checks may be read-only.

Optional providers cannot own personality/history, memory policy, voice continuity,
or inference priority. Phase -1 does not launch or claim a functioning runtime.

## Implemented status and root/component relationship

Composition/gateway ownership remains accepted design. Phase -1 configuration/
doctor is implemented, phase 1 provides the public ABI, and phase 2 provides
engine-owned clients. Agent composition, gateway, LiveKit and complete application
startup/shutdown have not been implemented.

[Root phase -1](../../../docs/adr/0001-phase-minus-one-scope.md),
[root phase 1](../../../docs/adr/0004-phase-one-contracts.md) and
[root phase 2](../../../docs/adr/0005-phase-two-canonical-engine.md) record delivered
outcomes. [Memory ADR 0003](../../../ember-memory/docs/adr/0003-canonical-engine-and-workers.md)
owns database/worker detail; agent composition will drain its clients and close the
engine off the realtime loop. SQLite is embedded and requires no separate database
server. Gateway process ownership is a separate future agent responsibility.

[Agent ADR index](README.md) explains scope/independent numbering;
[root ADR index](../../../docs/adr/README.md) is the application navigation point.
