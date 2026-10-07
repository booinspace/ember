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
