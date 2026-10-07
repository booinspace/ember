# Phase 0a — public-format research

Research date: 2026-10-07. This records public observations and implementation
hypotheses, not findings about the pending personal export. Phase 0b remains open.

## Evidence and confidence

| Source | What it supports | Limit |
|---|---|---|
| [OpenAI export help](https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data) | The download is a ZIP containing chat history and account data; delivery can take up to seven days and links expire after 24 hours. | It does not publish a versioned JSON schema or promise saved-memory inclusion. |
| [Original inferred schema by dmarx](https://gist.github.com/dmarx/08afeb669cdc2f974d6aca61dcce360d) | Conversations use mapping nodes with parent/children, nullable messages, a current-node hint, author/content/metadata, and heterogeneous timestamps and parts. | Community inference from observed data; optional fields and their semantics can change. |
| [Parser author's format support](https://github.com/temnoon/openai_export_parser#-supported-export-formats) and [implementation](https://github.com/temnoon/openai_export_parser/blob/master/openai_export_parser/parser.py) | Reports nested conversation/file ZIPs, numbered conversation JSON shards, and opaque `.dat` assets; code searches recursively for conversation candidates. | Third-party firsthand observations, not an OpenAI compatibility guarantee. Its extraction/flattening behavior is not Ember's archive semantics. |
| [Exporter author's format notes](https://github.com/willificent/chatgpt-lcm-migration/blob/main/docs/chatgpt-export-format.md) | Describes mapping graphs, current-node path selection, numbered shards, and source timestamps. | Another observed format; a selected path does not establish branch completeness or chronology. |
| [ijson maintainer documentation](https://github.com/ICRAR/ijson) | Event parsing permits processing a JSON stream incrementally. | Ember must add duplicate-key detection, graph bounds, and sanitized observations itself. |

The models and synthetic fixtures are original implementations of these hypotheses.
No personal public export or third-party parser code has been copied into the repo.

## Implemented hypotheses

- Recognize `conversations.json`, `conversations-NNN.json`, and
  `conversations_NNN.json`, including files inside bounded nested ZIP containers.
- Inspect array roots, `{ "conversations": [...] }` wrappers, and a single object
  with a mapping graph. Different shapes receive an explicit unsupported result.
- Keep null-message nodes, parents, children, and selection hints. Observe branches,
  cycles, missing/asymmetric references, and unknown metadata without choosing a path.
- Recognize text carried in `parts` or a `text` field, and count structured asset
  references. `.dat` means an opaque asset candidate, not an inferred MIME type.
- Stream each conversation separately. Corruption, duplicates, unsupported numeric
  values, or a record limit invalidate/defer the entire component's structural counts.
- Preserve exact raw/member hashes, including reasoning in raw input. Reports contain
  only structural labels, counts, and hashes; no message text or source identifiers.

## Evidence still required from phase 0b

We have no account-specific evidence for component completeness, saved memories,
attachment matching, project/gizmo fields, edits/regenerations, actor attribution,
selected-path semantics, timestamp units/precision/timezones, or deletion markers.
The inspector observes structures but cannot resolve those questions automatically.

Unknown fields remain in the transient source models. Source time is never filled
from the report's generation time. Inspection does not create canonical history,
seeds, grants, beliefs, normalized reasoning, or retrieval indexes.

## What can advance while waiting

Phase 0a delivers working reconnaissance tools, provisional adapter shapes, synthetic
variant/safety tests, archive-contract design, and pinned local model profiles.
The following design is ready for phase 1 review:
[provisional source and archive model](phase-0-source-model.md).

Phase 0b begins with the unmodified export and completes the existing
[capability template](phase-0-capability-template.md). A successful synthetic test
suite cannot close that acceptance gate.
