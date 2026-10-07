# ADR 0001: prepare infrastructure before export-driven contracts

Status: accepted and implemented for phase -1; historical scaffold scope.
Scope: workspace preparation and initial component boundaries.

The supplied design begins with actual export reconnaissance. The export is still
pending, and the workspace is empty. We can prepare independently packaged product
roots, offline inspection, deployment validation, privacy/safety tests, and phase 0
operations without choosing canonical schema semantics from guessed export data.

Stage `ember-memory/` and `ember-agent/` as the two planned product roots.
The outer workspace contains coordination docs/scripts, not a third application.
Use `0.0.0` scaffold package versions until actual contracts exist. Avoid allocating
canonical IDs, creating SQLite stores, or implementing placeholder clients that
would falsely advertise memory capabilities. Real model calls and all future
background generation use the single agent-owned gateway in its planned phases.

Read-only inspection is immediately useful and independently testable. It outputs
content-free observations and explicit unknown/deferred coverage. Synthetic tests
exercise behavior without establishing support for the pending real export.

## Implementation record and eight checks

1. **Desired behavior:** prepare offline tooling while the export is pending,
   without inventing source capabilities or character knowledge.
2. **Ownership:** ember-memory owns inspection/importer preparation; ember-agent
   owns configuration/readiness preparation. Root scripts/docs coordinate them.
3. **Existing abstraction:** follow PLAN's two-product boundary and deployment
   configuration; no public canonical memory implementation existed yet.
4. **Necessary primitive:** independent Python 3.12/uv environments, CLI entry
   points, typed configuration/report models and local staging. Empty future
   module folders confer no capabilities.
5. **Contracts/invariants:** retain raw inputs, protect personal data from Git and
   diagnostics, distinguish configured from running/verified features, and route
   future generation through one agent-owned gateway.
6. **Failure behavior:** reject malformed configuration and unsafe inspection;
   unavailable application startup fails explicitly rather than claiming readiness.
7. **Acceptance tests:** offline inspection/configuration/privacy and dependency
   boundaries, synthetic CLI checks, shared Ruff/strict Pyright/pytest gate.
8. **Implementation:** two package roots, locked setup, ignored staging, inspection
   CLI, configuration doctor and CI configuration. Historical results are in
   [phase -1 status](../phase-minus-one.md), not claims that later runtime is ready.

## Coupling and subsequent phases

Shared development configuration and editable sibling installation wire the local
workspace together. They do not create a third product or make character logic
depend on a concrete engine. See
[memory ownership](../../ember-memory/docs/adr/0001-ownership.md) and
[agent composition](../../ember-agent/docs/adr/0001-composition-and-gateway.md).

The 0.0.0 reservations, absent SQLite stores and avoidance of canonical IDs above
describe phase -1 only. [Phase 1](0004-phase-one-contracts.md) subsequently introduced
ABI/archive 0.1.0; [phase 2](0005-phase-two-canonical-engine.md) introduced storage
and clients. Those implementations extend this scaffold decision without closing
phase 0b or runtime acceptance. [ADR index](README.md) tracks current phase status.
