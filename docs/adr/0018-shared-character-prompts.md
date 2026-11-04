# Root ADR 0018: Shared system and character Markdown prompts

Status: implemented; validation and deployment evidence in the prompt proposal/runbook.
Date: 2026-11-04.

The operator approved [the eight-check design](../personality-prompts-proposal.md).
Agent composition owns bounded UTF-8 files, migration and atomic saves; CharacterCore
owns a single immutable instruction snapshot per observed input. One shared Markdown
file supplies common rules; canonical character UUIDs bind personality Markdown.
The authenticated browser exposes only these two documents and an effective preview.

Default JSON instructions migrate without overwriting operator Markdown. Character
configuration remains portable: linked exports copy it; selective exports now include
`personality.md` and its small display/migration metadata. Shared deployment rules do
not travel with imported characters. This is opaque file copying in ember-memory,
not a memory belief, schema, ABI or prompt-composition dependency.

The combined instruction guard is at most 4,096 conservative units and a quarter of
usable input; the existing 24,576 maximum total guard and response reserve remain.
No instruction truncation, model reload, auxiliary inference or new dependency.
Text/voice/HTTP share snapshots; current dialogue and permission validation survive edits.

Phase 9 task prompts, extraction and interpretation retain their existing constrained
instructions and scheduling. This amendment does not change their contracts or gates.
See [Agent ADR 0008](../../ember-agent/docs/adr/0008-character-prompts.md).

Installed Windows validation subsequently passed both saved synthetic styles through
the authenticated editor API, the real gateway, LiveKit and CPU speech, preserving
session/history and read-ahead. Primary HTTPS prompt controls are deployed. The owning
runbook records timing-independent results, provider recovery and teardown limitations.
