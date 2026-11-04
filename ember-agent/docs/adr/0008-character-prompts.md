# Agent ADR 0008: Operator prompt files and pinned conversational instructions

Status: implemented; see [design and validation](../../../docs/personality-prompts-proposal.md).
Date: 2026-11-04. Implements [Root ADR 0018](../../../docs/adr/0018-shared-character-prompts.md).

`runtime/prompt_files.py` reads two bounded files through the existing runtime worker.
`prompts.system_file` resolves relative to the deployment TOML (default
`prompts/system.md`). Personality is character-owned configuration under
`characters/<UUID>/config/personality.md`; legacy JSON remains an untouched backup.
Small migration metadata preserves display name and detects conflicting legacy profiles.

The compiler joins the application context guard, shared rules and character style
into one system message. Core snapshots it at input observation; optional investigation,
local replies and the prepared request reuse that exact snapshot. LiveKit updates its
SDK instruction view from the prepared snapshot, while the gateway request remains
owned by CharacterCore. Ordinary state polling never includes file contents.

Editor read/preview/save routes use existing operator authentication/origin checks.
Saves compare both hashes under ordered OS-held file locks and publish one complete
file, with a previous-version backup. Cancellation drains an accepted write before
releasing turn admission. Busy preparation, authorized generation and pending speech
receipts/playout reject saves; composing drafts remains available. File text is literal,
not executable Markdown or a template. Invalid configuration blocks new generation and
retains observed user input; bounded invalid text can be repaired through the editor.

Files larger than 8,192 bytes and invalid paths are rejected before hydration. Such
oversized/external path errors require filesystem repair; the UI never overwrites a
file whose complete original cannot be retained. No file watcher or automatic save queue.
