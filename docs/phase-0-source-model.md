# Provisional source and archive model

Status: phase 0a design, pending actual-export review. `PLAN.md` owns the final
contract requirements. The implemented adapter models live in
`ember-memory/packages/memory-importers/src/ember_memory_importers/chatgpt/source_models.py`;
they are not a published Memory ABI or a production normalizer.

## The boundary that exists now

`SourceConversation` contains the provider's mapping, all `SourceNode` objects,
nullable `SourceMessage` objects, actor/content metadata, raw timestamps, and the
current-node hint. Known fields validate strictly. Unknown JSON fields are retained;
`model_fields_set` and `model_dump(exclude_unset=True)` preserve absent versus null.
Provider IDs are strings and can disagree with mapping keys. That is a reviewable
ambiguity, not a reason to overwrite the graph or synthesize identities.

The source models are transient private-source objects. Their contents can include
reasoning and personal text; they are never diagnostic/report models. Bytes in the
original source archive, rather than a reserialized model, preserve the raw evidence.
Malformed shapes remain visible in reconnaissance; they are not silently coerced
into a successful normalized record.

`ReconnaissanceReport` is a separate, strict Pydantic report contract. It contains
hashed paths, container chains, content hashes, aggregate graph statistics, explicit
coverage outcomes, limits, and privacy/operation declarations. Format `0.0.2` adds
containers/directories/streamed coverage; legacy `0.0.1` reports remain readable.

## Target canonical records for phases 1–3

| Record | Identity and contents | Invariant |
|---|---|---|
| Source store / snapshot | UUID4 store identity, explicit namespace, immutable snapshot manifest, raw and logical fingerprints | Similar provider IDs or text never merge accounts. Local paths belong to internal locators. |
| Provider crosswalk | Store + adapter + provider record kind + external ID → canonical UUID4 + revision | Provider IDs are crosswalk keys, not public canonical IDs. Collisions are flagged. |
| Conversation | Canonical UUID4, revision, original title metadata, source-time observations, marker observations | Re-export changes append revisions; import time never substitutes for source time. |
| Graph node / edge | Canonical node identity, parent/child edges, optional message reference, source key | Preserve every branch and null-message nodes. Missing/asymmetric edges remain explicit. |
| Message / content part | UUID4 identity, positive revision, actor claims, channel/type, ordered parts | Visible text, tool/code output, attachment references, unsupported parts, and exclusions have distinct states. |
| Selection observation | Current-node hint, derived path status, ambiguity reasons, source revision | A path is a disposable derived view. Ambiguity blocks branch-dependent activation. |
| Attachment | Local source locator, exact content hash when available, provider references, availability state | Opaque `.dat` files stay untyped until verified; no automatic remote fetching. |
| Evidence span | Exact record/part revision, half-open codepoint and UTF-8 byte offsets, full-text hash, quote hash | Validate both coordinate systems and Unicode boundaries against the cited immutable text. |
| Memory candidate / belief | Typed claim, actor/modality, temporal bounds, supporting/contradicting evidence, confidence/lifecycle | Imported conversation text is evidence, not automatically approved knowledge. Conflicts remain representable. |

The future public `SourceRef` uses `source_store_id`, `record_kind`, `record_id`,
positive `record_revision`, and optional `part_id`. It contains no provider key,
filesystem path, database handle, or inference-runtime dependency. The generic phase 1 core now defines
its schema and client/writer interfaces experimentally. Phase 0b still validates
source-dependent mappings. See [implemented contracts](phase-1-contracts.md).

Canonical timestamps use UTC RFC3339 with microseconds when the source supports
conversion. Unknown timestamps stay null; raw values, unit assumptions, ambiguity,
and conversion rules remain attributable. Report `generated_at` is a maintenance
clock only. It is never the time of a historical conversation.

## How this becomes character memory

The five layers in the plan remain separate:

1. Immutable raw vault: exact original exports, fingerprints, manifests and backups.
2. Canonical archive: revisioned conversations, graph nodes, visible message parts,
   attachment references and evidence. Reasoning is excluded from normalized content.
3. Disposable indexes: rebuildable FTS5, CPU embeddings and replaceable vector backend.
4. Beliefs: validated claims, conflicts, temporal facts, consolidation and suppression.
5. Public ABI: backend-independent typed reads/writes through the injected client.

Normalization will stream bounded windows and commit records plus resume cursors
atomically. Crosswalks preserve identity across reparse; unchanged evidence retains
its identity, and changed records append revisions. Canonical validation precedes
indexing. Extraction produces candidates with exact evidence; activation follows
validation, source grants, confidence and conflict policy. No phase 0 model generation
or automatic memory seeding occurs.

A saved-memory summary, if supplied separately, uses `chatgpt_memory_summary` and
its own evidence. Summary-only claims remain conservative until confirmed or
corroborated. A conversation mentioning a memory does not establish an exported
saved-memory record.

## Current inspection coverage and limits

Conversation candidates stream one graph at a time, even when their file exceeds
64 MiB. Defaults bound each graph to 64 MiB of string payload/read accounting,
2,000,000 JSON events, and depth 128. Read-ahead permits two 64 KiB buffers;
Python object overhead exceeds serialized byte size. At most 1024 distinct labels
per report counter are retained; excess unknown hashes are aggregated explicitly.

Other JSON over 64 MiB is hashed and deferred. ZIP/directory bounds are 100,000
members, 2 GiB per member, 8 GiB combined decompressed bytes, 1000:1 ZIP compression
ratio, and two nested levels. Nested container bytes and their children both count
against the combined budget. Guards intentionally fail or defer with visible gaps.

Nested conversation/container components use a qualified path hash plus ancestor
container hashes; equal leaf names in different containers remain distinct. The
logical fingerprint includes raw nested-container blobs as well as their leaf
members, so repacking an inner ZIP can change the overall logical fingerprint.
Per-leaf content hashes remain usable for later crosswalk reconciliation.

Directories have a deterministic logical fingerprint and no invented raw ZIP hash.
File identity/size/mtime and directory membership are checked for changes. Unsafe
paths, duplicate/case-ambiguous paths, symlinks/reparse points, encrypted members,
corrupt containers and unsupported compression fail with content-free diagnostics.
Large parsed components are spooled to temporary storage with an 8 MiB memory
threshold and removed on every exit; no archive directory tree is extracted.

HTML is inventoried rather than parsed; asset references are counted rather than
resolved. Unknown filenames remain inventoried as `other`. These are explicit
reconnaissance limits, not claims of production importer completeness.
