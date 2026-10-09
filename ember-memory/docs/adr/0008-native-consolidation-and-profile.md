# Memory ADR 0008: native policy, linear support and canonical profile selection

Date: 2026-10-09. Status: implemented experimentally; review checkpoint.
Application integration and evidence: [Root ADR 0013](../../../docs/adr/0013-phase-seven-native-consolidation-and-profile.md),
[validation](../../../docs/phase-7-validation.md). Extends Memory ADRs 0005–0007.

## Decision and ownership

Reuse CharacterState, immutable revisions, processing candidates/review definitions,
SupersedeRequest, grants, reverse dependencies, owned workers and bundle publication.
The engine decides authority and acceptance; the model cannot supply a trusted writer.
NativeAcceptanceService accepts only a pinned native 0.2.0 job with original final user
messages, a versioned host delivery key and the configured user actor. It validates exact
spans, kind/modality, literal support, confidence/importance, current policy and conflicts.
Its policy authority remains distinct from confirmed_user. Unsupported evidence defers or
rejects with an immutable receipt; imported approval remains operator-owned.

Claim projection keys include subject, predicate/value/polarity, modality, origin and
validity. Events/past states additionally retain original source/time identity. Same date,
user or topic never asserts an incident. No fuzzy embedding merge or all-pairs clustering.
Equal assertions add a uniquely keyed support row without revising/embedding the survivor.
Storage grows with beliefs + distinct supports + explicit transitions, not copied support
history. Exact same-scope legacy duplicates require confirmed consolidation; original IDs
remain readable with typed redirects. Independent native/imported origin scopes stay intact.

Explicit correction/change wording can supersede a compatible native single-valued fact.
Unknown old validity remains unknown; observed, valid and recorded axes are separate.
Operator-controlled imported supersession retains its seed origin and origin dependencies.
Manual rejection creates persistent claim suppression, and lifecycle/forget/source/origin
restrictions are checked during re-extraction, seed, consolidation, history and related reads.

## Profile and related contracts

A durable primary user binding, enable flag and field exclusions live in the character DB.
Four explicit profile predicates (name/pronouns/timezone/communication style) are selected
through indexed bounded field reads; no biography model, cache, database or subscription.
Only positive, currently eligible accepted facts for the bound owned subject qualify.
Conflict/unknown/unsupported/excluded cases have content-free omission reasons. Approved
and seeded imports qualify with their original grants/lineage after explicit user binding.
Manual pins are never rewritten by automatic selection or exclusion controls.

retrieve_context includes optional profile data and its supporting records alongside the
existing search. Exact selected revision/source dependencies, policy/selection epochs and
known time boundaries form a compact certificate (256 refs / 64 KiB maximum).
lookup_related validate batches current headers, suppression, evidence closure, grants and
source availability without JSON memory/body hydration. resolve/expand is explicit on the
existing deep reader: at most 32 anchors, 100 returned records/edges, 512 logical rows,
32 source calls, 256 refs, bounded hops/cycles and a default two-second deadline. Returned
logical metadata is a consistency check, never a grant or global atomic snapshot.

## Indexed scope and maintenance

The first dynamic foreign-grant implementation defeated the vector metadata fast path.
A 100000-memory benchmark exposed that cost, so existing index projection now stores
scope_until_us and grant eligibility. Part/whole grants retain exact semantics: a permanent
matching grant wins, otherwise the latest authorized matching expiry wins; all hard sources
must qualify. vec0 active/valid_until and lexical header scope apply this before top-k.
World validity is not rewritten by permission expiry. Final canonical checks remain fresh.
Grant refresh touches foreign headers/vectors; broad suppression refresh remains conservative.
Monotonic new-memory/seed additions may use previously certified headers with explicit stale
coverage; restrictive changes invalidate certification. Recent fallback has a covering index.

This adds one disposable header field and indexes to existing projection ownership, not
another authorization layer or per-turn origin scan. Index copy is still O(index bytes),
with bounded/coalesced existing outbox maintenance. Support-only acceptance/seeding that
changes neither searchable content nor grants does not request a new index generation.

## Persistence and compatibility

ABI 0.4.0 / engine 0.5.0 / schema 5; derived layout 0.5.0 / headers 0.2.0. Prior canonical
stores receive backed-up additive migration and paged claim/transition backfill. Relationship
facts/supports/decisions are canonical append-only ledgers; their indexes/claim projections
are rebuildable. IDs and old immutable JSON stay intact. Old indexes need explicit update;
startup never downloads or rebuilds. Archive/bundle 0.1.0 stay unchanged. Full/selective
bundles preserve policy definitions, selected lineage and profile controls with honest coverage.

Golden tests retain the complete 0.1.2 prompt/schema/policy hashes. New optional predicates
use extractor 0.2.0/new jobs; no retroactive adoption or rewriting of running jobs. Existing
processing HTTP requests and gateway health remain 0.2.0 compatible. Worker reentrancy is
forbidden: initialize a client before entering its writer, as the rejection regression tests.

Review correction: certificates reuse the same effective grant-window helper as the
derived index. A temporary alternative cannot introduce an expiry when a permanent
matching grant exists. Among live temporary alternatives the latest expiry wins per
source; independent required sources retain the earliest of those effective boundaries.
An already-expired alternative is ignored, and absent authority fails closed. The
bounded selected-evidence queries and fresh delivery checks retain their existing owner.
