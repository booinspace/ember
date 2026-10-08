# Memory architecture decisions

These records own ember-memory implementation decisions. The
[root ADR index](../../../docs/adr/README.md) tracks application phases and integration;
[PLAN.md](../../../PLAN.md) remains authoritative. Root records link to these details
rather than duplicating schemas, evidence policy or worker implementation.

| Memory record | Scope/status | Root integration |
|---|---|---|
| [Memory ADR 0001](0001-ownership.md) | Ownership accepted; portions implemented in phases 1/2, later capabilities planned | [Phase -1](../../../docs/adr/0001-phase-minus-one-scope.md), [phase 1](../../../docs/adr/0004-phase-one-contracts.md), [phase 2](../../../docs/adr/0005-phase-two-canonical-engine.md) |
| [Memory ADR 0002](0002-phase-one-before-export.md) | Experimental ABI/archive 0.1.0 implemented before the personal export | [Root phase 1](../../../docs/adr/0004-phase-one-contracts.md) |
| [Memory ADR 0003](0003-canonical-engine-and-workers.md) | Engine 0.1.0 implemented/tested; review checkpoint before phase 3 | [Root phase 2](../../../docs/adr/0005-phase-two-canonical-engine.md) |
| [Memory ADR 0004](0004-source-import-and-authoring.md) | Official ZIP replay and native human entry implemented; private import verified, operator-reviewed claim adoption remains open | [Root phase 3](../../../docs/adr/0006-phase-three-source-and-runtime.md) |

Phase 0a preparation is recorded in
[root reconnaissance](../../../docs/adr/0003-phase-zero-reconnaissance.md) and its
linked research/source-model/runbook. It does not imply real-export or production
normalizer acceptance.

Numbering is local: Memory ADR 0003 differs from Root ADR 0003. Use a component
label and link. Keep memory decisions with this component if repositories are
separated, and update root phase status when a local decision changes application
behavior. Runtime/gateway decisions belong to
[agent ADRs](../../../ember-agent/docs/adr/README.md), not this backend.
