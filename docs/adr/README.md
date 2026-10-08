# Architecture decision records

This is the starting point for Ember architecture review. [PLAN.md](../../PLAN.md)
is the authoritative design, phase order and engineering standard. ADRs record why
an architectural choice was made, its owner, coupling, invariants, failure behavior,
acceptance evidence and remaining limits. They do not make a planned feature live.

## Phase status and decisions

| Phase/decision | Root record | Delivered status | Component detail |
|---|---|---|---|
| Phase -1: preparation | [Root ADR 0001](0001-phase-minus-one-scope.md) | Scaffold, offline inspection/configuration and shared engineering tooling implemented | [Memory ownership](../../ember-memory/docs/adr/0001-ownership.md); [agent composition boundary](../../ember-agent/docs/adr/0001-composition-and-gateway.md) |
| Project naming | [Root ADR 0002](0002-ember-name.md) | Ember application/component naming implemented | Both package READMEs and CLI aliases |
| Phase 0a: reconnaissance | [Root ADR 0003](0003-phase-zero-reconnaissance.md) | Preparation implemented; official ZIP now inspected, phase 0b source-dependent acceptance still open | [Research](../phase-0-research.md); [source model](../phase-0-source-model.md); [current findings](../../PLAN.md) |
| Phase 1: ABI/archive | [Root ADR 0004](0004-phase-one-contracts.md) | Experimental ABI/archive 0.1.0 implemented; source-dependent and ABI 1.0 acceptance open | [Memory ADR 0002](../../ember-memory/docs/adr/0002-phase-one-before-export.md) |
| Phase 2: canonical engine | [Root ADR 0005](0005-phase-two-canonical-engine.md) | Engine 0.1.0 implemented and tested; architecture review checkpoint before phase 3 | [Memory ADR 0003](../../ember-memory/docs/adr/0003-canonical-engine-and-workers.md); [walkthrough](../phase-2-canonical-engine.md) |
| Phase 3: importer/realtime slice | [Root ADR 0006](0006-phase-three-source-and-runtime.md) | ZIP import, P0 and runnable managed LiveKit text slice verified; operator-reviewed claim adoption remains open | [PLAN section 11](../../PLAN.md); [Memory ADR 0004](../../ember-memory/docs/adr/0004-source-import-and-authoring.md); [Agent ADR 0002](../../ember-agent/docs/adr/0002-phase-three-p0-turn-slice.md); [Agent ADR 0003](../../ember-agent/docs/adr/0003-managed-livekit-text-runtime.md) |

Phase 0b's supported export inspection and import are verified; saved-memory
inclusion remains unestablished. Phase 3 implementation and private import passed
the recorded checks; exact-claim operator review/adoption remains open. Phase 4
and later phases have not been implemented.
Early grant/suppression/temporal primitives in phase 2 do not close the later
seeding, bundle, indexing, extraction or consolidation acceptance gates.

## How the documentation trees relate

| Tree | Scope | Relationship to this index |
|---|---|---|
| `docs/` | Application-wide phase status, walkthroughs, compatibility, shared standards and runbooks | Supplies phase evidence and operational explanations |
| `docs/adr/` | Overall phase decisions and integration between the two first-party components | Tracks delivered behavior and links the owning component's detailed decision |
| `ember-memory/docs/` | Memory product decisions: ABI/archive, storage, evidence, policy and worker lifetime | [Memory ADR index](../../ember-memory/docs/adr/README.md) holds memory-owned technical detail |
| `ember-agent/docs/` | Character/runtime decisions: composition, prompts, gateway, realtime and providers | [Agent ADR index](../../ember-agent/docs/adr/README.md) separates design boundaries from shipped runtime |

The root phase ADR describes the application consequence; the component ADR owns
its implementation detail. Link between them rather than duplicating a detailed
schema, policy or scheduler specification. Runtime behavior remains in code and
acceptance tests, with release status in [compatibility](../compatibility.md).
PLAN takes precedence if a record conflicts with it; reconcile the affected ADRs
explicitly rather than treating historical text as the current design.

All three ADR trees have independent numbering. **Root ADR 0003 is phase 0a;
Memory ADR 0003 is the phase 2 engine.** Qualify references with the component or
link the exact path. Numbers identify decisions, not phases. Existing records
retain their IDs and links when a later decision extends them.

A component may eventually move to its own repository, as PLAN allows. Its own
ADRs stay with that component; workspace records explain their integration.
That packaging boundary creates no additional canonical owner or runtime dependency
between documentation trees.

## Maintenance and review

For every phase, use PLAN's eight checks: desired behavior, owner, existing
abstraction, necessary new primitive, contracts/invariants, failures, acceptance
and implementation. Record introduced coupling and meaningful tradeoffs. Update
this index, the root phase ADR and affected component ADR in the same change.
A phase primarily owned by one component does not require an invented decision in
the other; link its existing interface/ownership boundary when relevant.

Keep original decisions and historical validation identifiable. Mark a changed
choice as extended or superseded and link its replacement; do not rewrite a past
scaffold restriction into a permanent ban. Separate accepted design, implemented
behavior, pending review and unfulfilled acceptance gates. Summarize the tested
phase and stop for user review before beginning the next one.
