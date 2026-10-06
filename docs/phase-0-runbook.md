# Phase 0 — actual export reconnaissance

This runbook is ready; the real archive is not yet supplied. The synthetic fixture
cannot satisfy this phase's acceptance gate.

## 1. Preserve input and name the source

Retain the original export verbatim. Use an explicit namespace such as
`personal-chatgpt`; do not merge accounts based on similar conversation IDs/content.
Use `.local/incoming/` for workspace staging. Retain originals separately according
to the chosen backup policy. The inspector only reads the supplied file and does
not create a source vault, canonical library, or identity crosswalk yet.

## 2. Produce a bounded, content-free baseline

From the workspace root after setup:

```powershell
.\character-memory\.venv\Scripts\character-memory.exe inspect chatgpt .\.local\incoming\chatgpt-export.zip --source-namespace personal-chatgpt --output .\.local\reports\chatgpt-inspection.json
```

Default inventory includes hashed paths and exact file content hashes. If reviewing
the source locally requires raw filenames, add `--include-member-paths` and keep
that sensitive report private. Existing reports are never overwritten.

Default limits: 100,000 members, 8 GiB total uncompressed bytes, 2 GiB per member,
1,000:1 compression ratio, and 64 MiB for individual JSON structural parsing.
Unsupported compression, encryption, symlinks, unsafe/duplicate/case-ambiguous paths,
corruption, or exceeded safety limits cause an explicit failure. No extraction
occurs. JSON with duplicate keys/nonfinite values is reported as ambiguous/invalid.

Oversized JSON is hashed and marked `deferred_size_limit`. A report's existence is
not proof of complete coverage. Prefer a bounded streaming extension for a large
real conversation component; `--max-json-mib` is available for a deliberate local
limit adjustment. Do not hide or remove the deferred-coverage finding.

## 3. Review actual capabilities and ambiguities

Complete `phase-0-capability-template.md` from source-backed observations:

- Component formats/wrappers, JSON variants, HTML and asset coverage.
- Graph roots, missing nodes, branching, selected-path semantics, evidence of edits
  and regenerations, malformed/asymmetric edges, cycles, ambiguous branch selection.
- Original source IDs and account scope; re-export equivalence assumptions.
- Timestamps, nulls, units/precision/timezones, archive/deletion markers.
- Visible content types, multimodal references, channel handling, unknown structures.
- Actor/tool/model attribution and tool output semantics.
- Attachment manifests, hashes/references, local availability and unresolved links.
- Memory-related components actually present; saved memories remain unknown until
  evidenced. Memory-summary import stays a separate adapter.

Never put raw hidden reasoning into the capability report. Raw exports retain it
verbatim; normalized recall, extraction, logs, fixtures, indexes, and prompts exclude it.

## 4. Create sanitized structural fixtures

Derive small fixtures only after observing real shapes. Replace titles, personal
content, source IDs, author names, attachment pointers/filenames, tool inputs/results,
and sensitive metadata values. Replace reasoning with a synthetic sentinel or omit
its content while retaining only the channel/type structure needed for exclusion
tests. Maintain referential consistency and relevant null/unknown fields.

Keep full originals and working drafts under `.local/fixtures/`. Review privacy
before promoting any real-derived sanitized fixtures into tracked tests. Do not
reuse an unsanitized original as a test fixture.

## 5. Acceptance gate before phase 1

- Actual raw archive fingerprint and logical inventory recorded.
- Every component is supported, deferred, unsupported, or intentionally out of scope.
- Saved-memory inclusion is established or explicitly unestablished.
- Graph/content/time/identity variants and ambiguities have sanitized test coverage.
- Unsupported content and attachment resolution gaps are recorded explicitly.
- No archive mutation/extraction side effects, model processing, canonical writes,
  automatic seeding, activation, or grant expansion occurred during reconnaissance.
- Phase 1 contract requirements trace to observed data and the supplied design.

Phase 0 does not implement the production importer. Canonical normalization starts
after ABI/archive contracts and the canonical engine in phases 1–3.
