# ADR: canonical memory ownership and provisional contracts

Status: accepted from the supplied design.

Memory owns sources/evidence, identity crosswalks, character beliefs/revisions,
grants/suppression, retrieval/extraction, independent seeds, and portable bundles.
Importers write through normalized archive contracts once defined; providers never
own or mutate canonical memory. Search indexes are rebuildable accelerators.

The public ABI package is independent of backend/runtime frameworks. Engine
construction is external to character business logic. Phase -1 reserves package
boundaries and ships read-only inspection only. The generic ABI 0.1 core can precede the export per ADR 0002. Real archive evidence
still precedes final source-dependent mapping and ABI 1.0 acceptance; pre-1.0 changes still require preservation and
migrations rather than wiping history.
