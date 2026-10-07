# Agent architecture decisions

These records own character/runtime, composition, prompts, gateway and providers.
[Root ADRs](../../../docs/adr/README.md) track phase outcomes/integration;
[PLAN.md](../../../PLAN.md) is authoritative.
[Memory ADRs](../../../ember-memory/docs/adr/README.md) own canonical memory detail.

| Agent record | Scope/status | Root integration |
|---|---|---|
| [Agent ADR 0001](0001-composition-and-gateway.md) | Composition/gateway design accepted; configuration implemented, runtime/gateway planned | [Phase -1](../../../docs/adr/0001-phase-minus-one-scope.md), [phase 1](../../../docs/adr/0004-phase-one-contracts.md), [phase 2](../../../docs/adr/0005-phase-two-canonical-engine.md) |

The offline doctor and local installation wiring exist. Engine APIs now support
explicit construction, but agent startup, gateway and LiveKit are not implemented.
Installing the engine does not start a memory server or establish a running
character. Phase 3 has not begun.

IDs are local to this directory; qualify references with the component and exact
file link. Keep agent decisions with this component if repositories are separated.
When delivering an agent-owned phase, update its local ADR and the root phase
record/index with actual behavior and acceptance. A memory delivery does not imply
that corresponding agent integration has shipped.
