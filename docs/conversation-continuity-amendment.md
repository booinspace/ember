# Conversation continuity amendment

This amendment can ship while Phase 9 remains in progress. It changes native
conversation handling, not task scheduling, extraction policy or inference contracts.

## Delivery and storage

CharacterCore retains finalized user input before generation. Speech interruption,
failed generation and absent receipts cannot erase that input. Assistant history
contains only the transport-confirmed prefix; partial delivery is labelled. LiveKit
and HTTP retain acknowledgement ownership and the existing read-ahead UI is unchanged.

The existing native archive owns exact message bodies. Engine 0.7.0 / SQLite schema 7
add session/turn references with monotonically assigned sequence numbers. Input/output
metadata commit atomically with the existing source batches. Retry IDs remain
idempotent, including equal timestamps. The migration uses the existing consistent
pre-migration SQLite backup. No duplicate transcript, hidden reasoning, generated draft,
new memory ABI or auxiliary inference is introduced.

One running character core owns one session. Browser/transport reconnects retain it;
restarting Ember starts a new session, as before. Old native and imported conversations
are not implicitly adopted. Explicit selection/resumption across process restarts is
outside this same-session fix. Full-store backups retain the reference metadata;
selected evidence bundles do not promise resumable native sessions.

## Hard bounds

The serialized prompt guard is **24,576 UTF-8/framing units total**, further limited by
the deployment input allowance after its output reserve. It never grows automatically
with the model's 128K window. These are conservative byte/framing units, not tokenizer
claims. All instructions, current input, memories, pins, history, deep recall, session
passages and role framing count against the final guard. Mandatory input is never
silently truncated; an over-budget request fails explicitly.

Recent history receives at most **8,192 units**, reduced to half the input allowance
on smaller deployments. Memory retains its separate bounded allocation (at most 6,144
units and a quarter of the input allowance). Older selected user passages receive at
most **4,096 units**. Category allocations do not bypass the final total check.

The RAM cache keeps at most 64 turns. Archive reads select at most 64 recent turn
references and 16,384 text bytes. Individual selected passages clip user text at 4,096
bytes and assistant text at 2,048 bytes, with explicit markers; originals remain intact.
Assistant memory/source dependencies are revalidated with the existing certificate
path. Invalid dependent assistant text is removed while original user input remains.

Questions mentioning the first message or the start/beginning of this chat select the
first native user message directly. Literal questions such as “What did I say about
aquarium?” select at most four matching user passages. Topic lookup scans at most 256
recent user messages and 65,536 text bytes. Coverage is explicitly bounded; a missed
match must not be represented as proof that nothing was said. Other paraphrases use
recent context and approved memory as before. No generated summary or whole-transcript
prompt is needed. Broader semantic session search can be added separately if needed.

## Validation

Regression coverage includes 110 long synthetic exchanges, exact first-message/topic
lookup, equal timestamps, retry conflicts, interrupted/missing delivery, Unicode/JSON
cost, fixed prompt size as history grows to 1,000 turns, small-context overflow,
character isolation, forgotten-memory invalidation and schema-6 backup/migration.
Existing text, voice, deep recall and Phase 9 tests remain part of the full quality gate.
Runtime validation uses synthetic content; private conversations are not printed.

Validation on 2026-11-04:

- Local: 706 Python tests passed, 12 optional tests skipped; formatting, lint and
  Windows-target type checking passed. Root `uv lock --check --offline` passed;
  only the local engine version changed in the lock, with no external upgrades.
- Desktop Windows: the full quality gate passed (712 tests, 6 optional skips).
  Frontend type/build checks, 19 unit tests and 9 browser tests passed (3 opt-in
  live browser scenarios skipped). The browser read-ahead flow remains covered.
- Installed-model smoke check: after 110 long synthetic exchanges, the model
  identified the exact first message. The prompt used 8,298 total guard units,
  including 7,087 recent/current units and 297 excerpt units. Preparation took
  10.1 ms and the visible answer completed in 4.60 s in this single check.
- The installed CPU `gradium/phonon-31m-en` service produced 142,080 PCM bytes in
  319 ms for a short synthetic utterance. This is a service check, not a physical
  microphone or end-to-end user acceptance result.
- The desktop runtime was started with `start.ps1`, independently of SSH through
  a one-time desktop launch task with no recurring triggers. Gateway/browser
  health passed. Six pre-migration schema-6 backups were created. Primary private
  dialogue was not used for inference tests or printed.

Rollback to engine 0.6 requires stopping Ember and restoring the corresponding
schema-6 backup; old binaries cannot open schema 7. Restart/new-session behavior
and broader semantic transcript lookup remain the documented limits above.
