# ADR: canonical memory ownership and provisional contracts

Status: accepted from the supplied design.

Memory owns sources/evidence, identity crosswalks, character beliefs/revisions,
grants/suppression, retrieval/extraction, independent seeds, and portable bundles.
Importers write through normalized archive contracts once defined; providers never
own or mutate canonical memory. Search indexes are rebuildable accelerators.

The public ABI package is independent of backend/runtime frameworks. Engine
construction is external to character business logic. Phase -1 reserves package
boundaries and ships read-only inspection only. The generic ABI 0.1 core can
precede the export per [Memory ADR 0002](0002-phase-one-before-export.md). Real
archive evidence still precedes final source-dependent mapping and ABI 1.0
acceptance; pre-1.0 changes require preservation and migrations rather than wiping
history.

## Phase status and documentation scope

This boundary includes planned capabilities, not a claim that every memory feature
exists. Phase 0a implements reconnaissance, phase 1 generic contracts, and phase 2
storage/clients and early policy. Extraction, full seeds/bundles/indexes and ABI 1.0
acceptance remain open.

The scaffold limitation above applies to phase -1. See
[Memory ADR 0002](0002-phase-one-before-export.md) and
[Memory ADR 0003](0003-canonical-engine-and-workers.md) for later implementation.
[Memory ADR index](README.md) links component detail to
[root phase history](../../../docs/adr/README.md). Root records describe integration;
this record owns the durable-memory boundary.
