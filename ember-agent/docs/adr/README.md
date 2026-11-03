# Agent architecture decisions

These records own character/runtime, composition, prompts, gateway and providers.
[Root ADRs](../../../docs/adr/README.md) track phase outcomes/integration;
[PLAN.md](../../../PLAN.md) is authoritative.
[Memory ADRs](../../../ember-memory/docs/adr/README.md) own canonical memory detail.

| Agent record | Scope/status | Root integration |
|---|---|---|
| [Agent ADR 0001](0001-composition-and-gateway.md) | Historical Phase -1 configuration/design; extended by Agent ADR 0002/0003 | [Phase -1](../../../docs/adr/0001-phase-minus-one-scope.md), [phase 1](../../../docs/adr/0004-phase-one-contracts.md), [phase 2](../../../docs/adr/0005-phase-two-canonical-engine.md) |
| [Agent ADR 0002](0002-phase-three-p0-turn-slice.md) | P0/turn slice implemented and covered by the current full test gate; real-source memory adoption and full voice-room acceptance remain open | [Root phase 3](../../../docs/adr/0006-phase-three-source-and-runtime.md) |
| [Agent ADR 0003](0003-managed-livekit-text-runtime.md) | Managed server/gateway startup, single SDK text room, participant, credentials and ownership; real-room acceptance | [Root phase 3](../../../docs/adr/0006-phase-three-source-and-runtime.md) |
| [Agent ADR 0004](0004-conservative-background-gateway.md) | Same gateway gains scoped P3/P4 queues, schema output and durable realtime ownership; 406-test full gate passed; architecture review pending | [Root phase 6](../../../docs/adr/0009-phase-six-extraction-and-background-inference.md) |
| [Agent ADR 0005](0005-bounded-profile-and-delivery-validation.md) | Shared terminal/LiveKit text core includes bounded profile pins and validates exact dependencies before visible delivery; implemented experimentally, review checkpoint | [Root phase 7](../../../docs/adr/0013-phase-seven-native-consolidation-and-profile.md) |


The offline doctor, composition, gateway/text commands and LiveKit adapter exist.
Managed LiveKit text startup and a terminal participant are implemented in
[Agent ADR 0003](0003-managed-livekit-text-runtime.md).
The current full suite passes; source-backed recall/adoption still needs operator
review. Full voice-room delivery and browser/audio integration remain phase 8. Installing a package still does not itself start a character or service.

IDs are local to this directory; qualify references with the component and exact
file link. Keep agent decisions with this component if repositories are separated.
When delivering an agent-owned phase, update its local ADR and the root phase
record/index with actual behavior and acceptance. A memory delivery does not imply
that corresponding agent integration has shipped.

Phase 7 profile integration reuses the same CharacterCore for terminal and LiveKit
text and uses public ABI validation contracts; see Agent ADR 0005 and Root ADR 0013.
It does not add continuous native extraction or deep recall orchestration.

The planned temporal extension in [Root ADR 0014](../../../docs/adr/0014-planned-conversational-temporal-recall.md)
and [Agent ADR 0005](0005-bounded-profile-and-delivery-validation.md#planned-phase-8-temporal-extension)
covers shared ordinary query resolution, small follow-up scope, default-off one-call
interpretation and historical delivery. This accepted Phase 8 design does not make
voice, DeepRecallService or temporal interpretation currently operational.

[Agent ADR 0006](0006-phase-eight-calendar-and-capture.md) records in-progress ordinary temporal recall and idle capture integration.

The 2026-10-16 review corrections are recorded in the component Phase 8 ADR and
[Root ADR 0015](../../../docs/adr/0015-phase-eight-implementation.md).

The same records include the user-directed
[ordinary word-recall correction](../../../docs/phase-8-word-recall.md): compact
extracted values with a bounded share of the configured input allowance.

Agent ADR 0006 also records ephemeral thinking/selected-recall UI, experimental
gateway 0.4.0, keyboard/scroll controls and pending UI deployment authorization.
