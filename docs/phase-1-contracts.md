# Phase 1 — Memory ABI / Normalized Archive 0.1

Status: **phase 1 core implemented**, experimental `0.1.0`. Actual-export adapter
acceptance remains open in phase 0b/1b. There is no SQLite engine, production
normalizer, automatic activation, live client implementation, or ABI 1.0 freeze.

## Working contracts

The independently packaged `ember-memory-abi` now provides:

- Strict Pydantic contracts and UUID4 canonical IDs with positive revisions. Derived
  chunks use UUID5; provider IDs and physical database/file/vector keys are excluded.
- Portable `SourceRef` identity, exact half-open codepoint/UTF-8 evidence, source and
  quote hashes, source-revision validation, and browser UTF-16 boundary conversions.
- Generic conversation/message/document/asset records and graph nodes separate from
  messages. Null-message nodes, duplicate/asymmetric/missing links and ambiguity are
  preserved for importer/engine review rather than repaired or flattened.
- Source clock observations plus separate valid/observed/recorded time axes. Unknown
  historical times remain null. Known wire times use UTC microsecond precision.
- Memory/candidate/approved-snapshot, fact/episode/goal/plan, entity, provenance,
  chunk/source-map, session-summary, pin, query/result, and retrieval-context models.
- Independently versioned predicates/modalities with conservative write checks.
  Unknown kinds/modalities/extensions can be read and round-tripped; they do not
  acquire supported write or activation semantics.
- Matching `MemoryStore` / `AsyncMemoryClient` protocols bound to trusted character
  identity. Queries and mutation arguments cannot choose a different character.
- Typed errors, explicit idempotency keys for mutations, expected revisions for
  changes to existing beliefs, and source records/cursor in one maintenance batch.
- Backend-free schema generation and committed reproducible JSON schema artifacts.

The ABI depends on Pydantic and Python's standard library. It does not import the
engine, SQLite, LiveKit, Ollama, MCP, or a harness. Phase 2 supplies implementation
and transactional/access-policy enforcement behind these interfaces.

## Serialization specification

`ember.canonical-json@0.1.0` hashes UTF-8 JSON with Unicode-codepoint key ordering,
array order preserved, literal Unicode, no whitespace, finite numbers, and Python
3.12 JSON numeric formatting. Numeric forms such as `1`/`1.0` and negative zero
remain distinct. This is an explicitly versioned algorithm, not RFC 8785.
Raw text hashes use exact UTF-8, preserving line endings and combining marks.
No search or Unicode normalization may alter evidence coordinates/hashes.

Known timestamps serialize as `YYYY-MM-DDTHH:MM:SS.ffffffZ`. Calendar validity is
checked in Python, and schemas also declare date-time format plus the exact pattern.
Integer Unix microseconds round-trip without floating point, including pre-epoch
instants. Naive datetimes are rejected rather than assigned a guessed timezone.

Unknown same-version namespaced extensions remain data during reads. Supported
writes must explicitly register their extension names. Arbitrary JSON within a
registered extension is not recursively interpreted as additional contract fields.
Contract field freezing is shallow; the engine must snapshot mutable nested JSON
and enforce revision/content immutability at persistence boundaries.

## Predicate and modality boundaries

The six initial definitions cover primary versus owned GPUs, music preference,
knowing a person, names, and aliases. One additional GPU purchase does not end an
ownership claim. A primary-GPU replacement still needs explicit transition evidence.
Definition/value/modality compatibility is validated; an intention/hypothetical
cannot be stored as a factual primary GPU. These checks do not establish authority,
corroboration, confidence calibration, activation, or temporal supersession.

Engine/policy phases additionally validate original evidence, source grants,
suppression, predicate semantics, policy versions, conflicts, and decisions. Creating
an `ApprovedKnowledge` object does not itself approve source claims or create a seed.

## Contract execution in phase 2

The sync engine owns atomic transactions, crosswalks, immutable revisions,
compare-and-swap, scoped idempotency, source grants, suppression, and current-source
verification. Reusing a mutation key with different content fails. Source records
and the import cursor commit together. Deadline/cancellation does not prove rollback
or permit replay under a new key.

The async implementation runs blocking work on bounded owned workers, propagates
cancellation/deadlines, and reports unresolved writes explicitly. Derived-index
failures leave canonical records available with truthful coverage. Reads always
recheck the trusted character binding and current grants/suppression. The maintenance
`ArchiveWriter` is separate from character and read-only harness capabilities.

## Reproduce the artifacts and checks

From the workspace root after locked setup:

```powershell
uv run --locked python scripts/export_contract_schemas.py
uv run --locked python scripts/export_contract_schemas.py --check
uv run --locked python scripts/check.py
```

Artifacts in `ember-memory/schemas/`:

- `memory-abi-0.1.0.schema.json`: definitions catalog; choose the endpoint's `$defs`.
- `normalized-archive-0.1.0.schema.json`: atomic maintenance batch root.
- `predicate-definitions-0.1.0.schema.json`: predicate definition array.

Schema validation is structural. Cross-record evidence hashes, Unicode coordinates,
source resolution, temporal/policy semantics and authorization still require the
runtime validators and canonical engine. The conformance tests contain synthetic
records only, and schema generation never generates source/character identities.

## How far we can advance without the export

| Work | Can proceed now | Acceptance that remains pending |
|---|---|---|
| Phase 1 core contracts | Implemented and tested | Actual-source compatibility; ABI 1.0 integration/restore gate |
| Phase 2 canonical engine | Yes, using generic records and synthetic transactional/recovery scenarios | Source-dependent adapter findings may require additive changes or migrations |
| Phase 3 gateway/model baseline and LiveKit foundations | Can be built independently against the existing pinned models | End-to-end historical recall and real-import provenance require real sources |
| ChatGPT production normalizer | Public-format hypotheses can guide preparation | Actual variants, timestamps, graph selection and asset resolution need the archive |
| Historical extraction/seeding | Policy/contracts and synthetic scenarios can be built | Personal facts, approved knowledge and evidence cannot be invented or accepted from synthetic data |

Continue with phase 2 as the next delivery step. Keep the raw export acceptance
and source-dependent contract review open rather than declaring phase 0b complete.
