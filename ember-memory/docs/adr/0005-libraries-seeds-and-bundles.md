# Memory ADR 0005: library approvals, independent seeds and exact-span bundles

Date: 2026-10-08.
Status: Phase 4 implemented and tested; awaiting user architecture review.
Extends Memory ADR 0003/0004. [Root ADR 0007](../../../docs/adr/0007-phase-four-libraries-characters-bundles.md)
records the eight checks, application coupling and Phase 4 acceptance boundary.

## Decision and ownership

Use the existing canonical engine's store/worker/authority primitives. LibraryService
is trusted blocking maintenance, read-only without explicit confirmed_user authority.
Source imports and model generation never call approval. An operator may propose a
library-owned candidate, review its exact evidence, explicitly approve it, and seed
specified immutable knowledge revisions into existing independent characters.

The ABI already defines candidates, approvals, entities, evidence and origins. No
provider, SQLite, bundle format or maintenance capability is added to the public ABI.

## Canonical schema 0.2.0

user_version 2 adds expires_at to exact snapshot source_grants and these tables:

| Table | Purpose |
|---|---|
| candidates / approved_knowledge | Immutable candidate and manually approved library revisions |
| knowledge_entities | Exact minimal entity revisions frozen at approval |
| maintenance_receipts | Semantic approval/seed replay hashes and durable receipts |
| entity_origins | Stable destination entity mapping across seed operations |
| materialized_evidence | Original span/quote/hash for a partial portable evidence store |
| store_coverage | full, evidence_only, or selected_character coverage |

Existing immutable sources, history, IDs, grants, suppression and receipts are preserved.
An OS-owned SQLite backup is created before each schema-1 upgrade attempt. Existing
backup files are retained, never overwritten. A failed migration rolls back schema/data
and is resumable; a successful migration in another store need not roll back because
both schema versions are supported at startup. No indexes need rebuilding in this
additive change. Unknown versions or corrupt identity fail closed.

## Approval/seed invariants

- Only same-library evidence/entities may support approved library knowledge. Exact
  spans must resolve; a supporting assertion is required. Imported/model authority
  and verdict strings have no effect on trusted authority.
- Candidate/approval/entity-version links/audit/receipt commit together. Revisions
  append consecutively. Equal input/key replays; altered content conflicts.
- Seeds assign new destination memory and entity UUID4s; all origin and exact evidence
  references remain explicit. Semantic facts, episodes and plan goal links are remapped.
  Plan goal dependencies must be in the selected set. Entity context comes from its
  approved revision; existing destination origin mappings remain independent snapshots.
- Seed entities, grants, beliefs, origin dependency rows, outbox, receipt and audit
  commit in the destination transaction. Supporting evidence_read grants never imply
  archive_search. Suppression is checked after remapping and before commit, including
  origin fingerprints, so a forgotten seed cannot reappear under a new operation key.
- Revocation/expiry retain accepted beliefs. Policy epochs invalidate current delivery;
  expiry is additionally rechecked because passage of time does not change an epoch.
- A fresh character has no imported beliefs/grants. Seeded uses explicit seed only.
  Selected-archive/primary behavior is composed with separately explicit snapshot
  archive grants. Later imports do not expand any capability automatically.

## Bundle 0.1.0 format and lifecycle

Bundles are inspectable directories containing manifest.json, scoped registry.db and
canonical UUID-named store directories. Manifest paths are relative POSIX names;
file sizes/SHA-256, store identity, version and coverage must match. No symlinks,
unlisted files, WAL/owner sidecars or traversal members are accepted. Registry
catalogs contain only included/referenced stores, and restore combines validated
catalog entries; internal root identity may differ without changing logical IDs.

BundleService opens its own exclusive CanonicalEngine. Chat must be stopped; root
ownership rejects simultaneous owners. No external client can submit to this private
engine. SQLite backup runs on an owned reader; raw files stream in 1 MiB blocks.
Root ownership is held until publication as the dependency/source-deletion barrier.
Writable registry/evidence/selected-state projections are built in local scratch
space, closed, then copied/flushed as sealed files into bundle staging. This keeps
live projection connections off a NAS destination. Bundles can reside on configured
backup storage, while restored live SQLite roots
must pass the existing local-root policy. The format contains no machine paths.

| Kind/mode | Contents | Restore dependency |
|---|---|---|
| Source | Full canonical library, candidates/approvals, rawsource, assets, manifests, specifications | None |
| Character linked | Full native history/beliefs/revisions, entities, grants, suppression, pins, audit/receipts, configuration | Full source bundle for each referenced library |
| Character self_contained | Selected belief histories and required canonical state, subject/personality configuration, exact authorized evidence spans | None |

Selective materialization creates fresh databases by copying required rows; it never
deletes rows from a copied full DB, which could leave private bytes in free pages.
Unrelated history and even unquoted text in the same source part stay excluded.
Shared evidence retains original source/evidence UUIDs, revisions, Unicode/byte
coordinates and hashes. It is represented as partial materialized evidence, not as
a reduced message pretending to be the complete immutable original. Original-source
resolution/archive search reports unavailable; the existing evidence resolver can
return a verified quote. Evidence-only libraries reject archive writes and cannot
claim complete source-backup coverage.

All selected revisions retain logical evidence dependencies. Currently revoked or
suppressed source bytes are not materialized; their permissions/suppression still
govern reads. Granted but unavailable evidence blocks a self-contained export.
Optional assets/raw source scopes are not implicitly included; a full source bundle
is an explicit operation. Partial exports preserve safe receipts only when all their
affected memories are included. Derived index/cache data is omitted and remains
rebuildable. Operational tasks/providers/credentials are not resumed by restore.

Selective export is bounded to 10,000 selected memory IDs, 100,000 revisions/spans,
64 MiB of serialized selected revisions and 64 MiB of quote text. Manifest size is
2 MiB, with up to 100,000 members. Oversized selections fail explicitly; linked
SQLite backups remain streaming. Grant matching is constant-time per row. Restore
validates members and copied bytes, DB identity/schema/coverage, dependency presence,
then permitted evidence before publishing a new root. Providers remain disabled.
Existing roots/bundle outputs are rejected rather than overwritten.

## Acceptance and limits

All 332 tests passed on Windows/Python 3.12.15, including 27 new engine acceptance
tests and both new actual-room portable-seed cases (synthetic model and actual pinned
Qwen). Root/agent/memory locked setup, Ruff format/lint, strict Pyright, ABI schema
and all lockfile checks passed. The validation-owned gateway was shut down; independent
Ollama remained running. The source-deletion barrier and local projection staging are
covered; NAS transport and Linux are not locally exercised. Detailed evidence is
linked from Root ADR 0007 and PLAN.
Windows storage/actual-room tests are distinct from future Linux/large-scale gates.
No new extraction rule, personal memory activation, search projection, audio pipeline
or model priority is included. Stop for user architecture review before Phase 5.

## Character Markdown configuration amendment — 2026-11-04

[Root ADR 0018](../../../docs/adr/0018-shared-character-prompts.md) adds application-owned
`personality.md` and `personality-migration.json` to selective character configuration
copying. Linked exports already copy the character config tree. These remain opaque
files: the engine neither constructs prompts nor bundles shared deployment system rules.
The existing bundle format, memory ABI and canonical schema are unchanged. Synthetic
linked/selective restore tests verify the character file and display metadata survive.
