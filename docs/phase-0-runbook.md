# Phase 0 — preparation and actual export reconnaissance

Phase 0a is implemented while the personal export is pending: public-format
[research](phase-0-research.md), [provisional source/archive models](phase-0-source-model.md),
streamed inspection, synthetic tests, and [local model profiles](model-profiles.md).
The working synthetic demonstration is under ignored `.local/incoming/` and
`.local/reports/`; it cannot satisfy phase 0b actual-export acceptance.

The steps below close phase 0b once the export arrives.

## 1. Preserve input and name the source

Retain the original export verbatim. Use an explicit namespace such as
`personal-chatgpt`; do not merge accounts based on similar conversation IDs/content.
Use `.local/incoming/` for workspace staging. Retain originals separately according
to the chosen backup policy. The inspector only reads the supplied file and does
not create a source vault, canonical library, or identity crosswalk yet. An existing
directory source is supported; keep the report outside that directory.

## 2. Produce a bounded, content-free baseline

From the workspace root after setup:

```powershell
.\ember-memory\.venv\Scripts\ember-memory.exe inspect chatgpt .\.local\incoming\chatgpt-export.zip --source-namespace personal-chatgpt --output .\.local\reports\chatgpt-inspection.json
```

Default inventory includes hashed paths and exact file content hashes. If reviewing
the source locally requires raw filenames, add `--include-member-paths` and keep
that sensitive report private. Existing reports are never overwritten.

Default limits: 100,000 combined members, 8 GiB combined uncompressed bytes,
2 GiB per member, 1,000:1 ZIP compression ratio, and two nested levels. Nested
container payloads and children both count against the global byte budget.
Conversation arrays/wrappers stream one graph at a time, bounded to 64 MiB of
string payload/read accounting, depth 128 and 2,000,000 events per graph. Parser
read-ahead permits two 64 KiB buffers. Other JSON has a 64 MiB whole-file bound.
Unsupported compression, encryption, symlinks, unsafe/duplicate/case-ambiguous paths,
corruption, or exceeded safety limits cause an explicit failure. No extraction
occurs. JSON with duplicate keys/nonfinite values is reported as ambiguous/invalid.

Oversized individual conversations or metadata are hashed and marked
`deferred_size_limit`; partial conversation statistics are discarded. Deeper ZIPs
are hashed with `deferred_depth_limit`. A report's existence does not establish
complete coverage. CLI adjustments are `--max-conversation-mib`, `--max-json-mib`
(for other JSON), and `--max-nested-depth`. Review limits before increasing them.
A larger outer file does not itself require increasing the per-conversation bound.

The library also exposes validated `InspectionLimits` for total/member bytes,
member counts, compression ratio, event and depth bounds. If a real component
exceeds defaults, make a reviewed local adjustment rather than removing a guard.

Report `0.0.2` uses hashed qualified member identities and ancestor container
hashes. Directory sources have a logical fingerprint without a raw ZIP hash.
Private temporary staging spills after 8 MiB and is deleted on exit; inspection
does not extract an archive directory tree. HTML and opaque media are inventoried,
not interpreted. Attachment resolution remains a later importer responsibility.

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
