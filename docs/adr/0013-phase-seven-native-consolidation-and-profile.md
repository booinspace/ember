# Root ADR 0013: Phase 7 native consolidation, temporal reads and bounded profile

Date: 2026-10-09. Status: implemented experimentally; awaiting architecture review.
Extends the accepted pre-code design in [Root ADR 0012](0012-planned-phase-seven-implementation.md)
and [profile ADR 0011](0011-planned-phase-seven-user-profile.md). PLAN.md remains authoritative.
The [acceptance evidence](../phase-7-validation.md) distinguishes executed checks,
measured costs and remaining project-wide gates. Stop before Phase 8.

## Behavior, ownership and existing abstractions

A model proposes; ember-memory verifies original evidence and bound first-party speaker
before policy acceptance. Repeated exact assertions add support to one active belief per
origin/time/modality scope. Explicit corrections and world transitions preserve earlier
revisions and time axes; unresolved conflicts defer rather than overwrite. Imported
claims still require operator approval and independent seeding.

The existing engine owns transactions, grants, suppression, profile selection and workers.
The existing character core consumes public ABI data, shares the Pins budget and validates
visible delivery. Composition binds the native actor. Existing CLI maintenance requires
exclusive ownership. No additional product, database, daemon, planner, scheduler or profile
cache is introduced. See [Memory ADR 0008](../../ember-memory/docs/adr/0008-native-consolidation-and-profile.md)
and [Agent ADR 0005](../../ember-agent/docs/adr/0005-bounded-profile-and-delivery-validation.md).

## Necessary primitives and coupling

| Addition | Why existing contracts were insufficient | Owner/coupling |
|---|---|---|
| Native policy request/receipt and immutable support/edge ledgers | Eligibility was only a proposal; repeats copied beliefs; validated transitions were not publicly inspectable | Memory maintenance and the same canonical writer; no agent import |
| Optional related validation/temporal contracts | Serial gets could not batch fresh source/policy/time checks | ABI 0.4.0; optional capability, legacy client fallback |
| Profile binding, exclusions and pin purpose | Manual pins had no automatic-selection ownership or query-independent field policy | Character store; reuses PinnedMemory and retrieve_context |
| Indexed claim/observed headers | Exact consolidation and field selection must avoid corpus scans | Rebuildable projection; no embedding similarity establishes equivalence |
| Disposable grant-expiry header | Dynamic per-vector source joins caused a measured 1.69-second regression | Existing index builder/refresh and vec0 eligibility; layout 0.5.0, one nullable expiry field |
| Opt-in prefill counter | TTFT did not isolate actual upstream prefill duration | Existing gateway request 0.3.0; legacy request/health JSON stays compatible |

The primary text path has one existing hybrid lookup and no relationship expansion,
full read manifest, extra encoder call, field-by-field get or archive-body hydration.
Four allowlisted fields are enabled initially, bounded by the ABI maximum of eight.
Manual pins come first in the shared 384 conservative serialized-token allocation;
six ordinary slots and the 7000-token input ceiling remain. Exact delivery dependencies
are capped at 256 distinct memory/source references and 64 KiB, including retained history.
Whole history turns are discarded when they exceed the bound or become stale.

## Invariants and failure behavior

Policy/version, candidate/evidence and processing lineage are immutable and inspectable.
Native authority requires versioned first-party attribution and the configured user entity;
confidence alone is insufficient. Imports cannot gain that authority by being recent or
having matching text. Equivalent native and reviewed imported assertions can remain
independent origin-scoped beliefs; repeats within one scope become support records.
Supersession and consolidation preserve origin restrictions, identity/redirect history and
manual rejection/forgetting. Corroborating mentions do not become thousands of mandatory
fast-path source references or copies of prior memory JSON.

Profiles contain accepted facts, never raw candidates or a generated biography. Unknown,
conflicting, excluded, denied, unavailable or time-invalid fields are omitted with reasons.
Values are untrusted user-role data; they do not modify personality or tool authority.
Batch checks run before inclusion/generation and visible text/tools/completion, not progress.
Current grants and exact source headers are rechecked; manifests grant no authority and
make no cross-store atomic-snapshot promise. Bounds/deadlines return partial coverage;
canonical integrity/ownership errors retain their explicit failure behavior.

## Migration, performance and delivery

ABI 0.4.0 / engine 0.5.0 / canonical schema 5 upgrade prior supported stores additively,
under their existing ownership locks, with pre-upgrade SQLite backups and rollback.
Old memory/source JSON and IDs are not rewritten. Legacy extractor 0.1.2 prompt/schema/
policy hashes remain exact; new extraction uses 0.2.0. Bundles retain full or explicitly
partial profile/support/lineage/policy metadata. Archive and bundle formats remain 0.1.0.

The diverse 100008-memory synthetic CPU BGE run measured profile selection/validation/
formatting p95 1.03 ms, enabled recall 109.23 ms, and grant-scoped recall 137.08 ms.
Both recall measurements meet the 150 ms soft deadline and exceed the 50–100 ms warm aim.
Equivalent 128-memory validation was 5.52 ms batched versus 18.64 ms serial. One 121-event
turn used one context call, 23 validation calls, zero expansion, 19 references and 2903
metadata bytes. Actual LiveKit transport and real pinned Qwen checks are recorded separately.
Cold model/index certification, complete streaming cost and concurrent index-copy tails
remain explicit in the evidence; a soft 150 ms deadline is not a hard wall-clock guarantee.
This does not certify the historical Phase 5 million-memory or multi-platform targets.

Implementation was isolated in `ember-phase-seven` to protect the user's editable install
and ongoing ingest. Production memory databases have not been migrated or imported here.
The gateway was restarted only after the user explicitly authorized it. Deployment commands
are in the [runbook](../phase-7-runbook.md); architecture review precedes Phase 8.
