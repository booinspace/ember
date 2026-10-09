# Phase 7 runbook

Phase 7 adds native policy acceptance, consolidation/time and an accepted-fact user profile
in terminal/LiveKit text. It does not continuously extract each chat turn; Phase 8 owns that
queue, browser/audio and source controls. See [architecture](adr/0013-phase-seven-native-consolidation-and-profile.md)
and [acceptance evidence](phase-7-validation.md).

## Install safely after the current owner finishes

Implementation is in `C:/Users/operator/repos/ember-phase-seven`; the primary checkout/data
were protected during ingest. Review/apply the change before using its code with production
state. Do not replace a live editable install, remove locks or open a second writer.
Wait for ingest/character ownership to finish. The first new-engine open upgrades each
supported canonical store to schema 5 under its lock, preserving a consistent pre-upgrade
`.schema-4.backup.db` (or corresponding older version). Backups are retained; old code
cannot open schema 5, so rollback requires restoring the complete prior snapshots offline.

After applying the reviewed change in the primary checkout, use one explicitly updated env:

```powershell
Set-Location C:\Users\operator\repos\ember
uv sync --locked
$memory = ".\.venv\Scripts\ember-memory.exe"
$ember = ".\.venv\Scripts\ember.exe"
$memoryRoot = ".local/data"
```

If retaining separate component environments, update those too before using their launchers
(`uv sync --project ember-agent --extra local-deployment`; `uv sync --project ember-memory`).
Do not mix stale component environments with new contracts. Setup installs dependencies;
startup never downloads models/indexes. Existing archive jobs retain their original hashes.

## Inspect and control the profile

Use the character UUID from your deployment binding. Library UUIDs and extraction-job UUIDs
are different scopes; a source library is not the runtime character.

```powershell
$character = "YOUR-CHARACTER-UUID"
& $memory profile inspect --character $character --root $memoryRoot
& $memory profile inspect --character $character --evidence --root $memoryRoot
& $memory profile exclude --character $character --field user.communication_style --root $memoryRoot
& $memory profile include --character $character --field user.communication_style --root $memoryRoot
& $memory profile disable --character $character --root $memoryRoot
& $memory profile enable --character $character --root $memoryRoot
```

Inspection exposes policy, binding, selected/omitted fields, revisions, dependencies and
optionally original quotes. It needs the same exclusive maintenance owner. Unknown fields
are normal; enabling the profile never approves or seeds a candidate. Manual pins remain
separate from automatic selection. Values stay untrusted observations rather than personality.

For imported profile facts, approve and seed the new explicitly typed candidates through
[Phase 6 ingestion](phase-6-runbook.md), then bind the intended **character-owned seeded
user entity**, not the original library entity. Discover it from an accepted fact's payload:

```powershell
$recalled = & $memory recall --character $character --root $memoryRoot | ConvertFrom-Json
# Inspect results[].memory.content.payload.subject and choose the intended user.
$userEntity = "CHOSEN-CHARACTER-USER-ENTITY-UUID"
& $memory profile bind --character $character --subject $userEntity --root $memoryRoot
```

Multiple speakers are never silently treated as the same person. Existing older episode
memories are still ordinary recall; they are not guessed into profile fields. New profile
facts use the explicit preferred_name/pronouns/timezone/communication_style predicates.
For summaries/archives choose extractor 0.2.0 on a **new** extraction job and review it;
leave existing 0.1.2 jobs unchanged.

## Process already archived native dialogue

A normal text session archives bound native messages. End the character session to release
its memory owner; keep a gateway-only process available for extraction. Choose the actual
model tag/digest from your deployment. These example pins match the existing non-MTP model:

```powershell
$model = "qwen3.8-heretic-ara:27b-q4_k_m"
$digest = "c7612209c4ffbc7e80c96abbfd9d2bcc2dcc82ee12b4fa39e8c4937ef6e0c1b9"
$created = & $memory extract create --store $character --source-kind native `
  --extractor ember.atomic-claims@0.2.0 --key "native-maintenance-unique-key" `
  --model $model --digest $digest --root $memoryRoot | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Job creation failed." }
$job = $created.definition.job_id
& $memory extract run --store $character --job $job `
  --gateway http://127.0.0.1:11435 `
  --token-file .local/runtime/non-mtp/gateway-background.token `
  --window-attempts 3 --continue-on-error --root $memoryRoot
if ($LASTEXITCODE -ne 0) { throw "Extraction did not finish; inspect status." }
& $memory native accept --character $character --job $job --limit 100 --offset 0 --root $memoryRoot
```

Acceptance is paged; advance `--offset` for jobs with more than 100 candidates. It invokes
host policy, not approve-all. Deferred/rejected proposals retain reasons and can be inspected
with the existing extract review workflow. Native accepts need no library seed. Imported
claims still use explicit review/apply/seed. Old unbound native records cannot gain speaker
authority retrospectively; conservative rejection is intentional.

Policy requires supported explicit first-party statements and sufficient importance/support.
Repeated same-scope statements add provenance rather than another active belief or embedding.
Explicit corrections preserve the previous assertion; unknown old dates stay unknown.
Existing exact duplicates can be merged only by confirmed maintenance:

```powershell
& $memory consolidate apply --character $character `
  --survivor SURVIVOR-UUID --survivor-revision 1 `
  --duplicate DUPLICATE-UUID --duplicate-revision 1 `
  --key "reviewed-duplicate-merge-unique-key" --root $memoryRoot
```

It rejects non-equivalent claims/origins/events; it is not a similarity-based merge command.
Related inspection takes a bounded JSON request (`intent`, exact `anchors`, limits/deadline):
`ember-memory related inspect --character ... --input request.json --root ...`.
Ordinary chat never invokes expansion. Historical temporal search is available through the
optional ABI; a natural-language deep-search planner is Phase 8.

## Index and chat

Old disposable layout 0.4 indexes must be explicitly upgraded; compatible vectors are reused.
The profile itself remains available without FTS/vectors/encoder.

```powershell
& $memory index update --store $character --root $memoryRoot
if ($LASTEXITCODE -ne 0) { throw "Index update failed; check installed BGE assets." }
& $ember start --config .local/config/deployment.non-mtp.toml
# From a second terminal:
& $ember livekit-chat --config .local/config/deployment.non-mtp.toml
```

Ask an unrelated NAS question, then a relevant identity/style question, restart and repeat.
Only eligible accepted/seeded memories enter context. Profile fields need not appear by
name in every reply; model weights are not trained on your import. Different model tags,
including MTP, use the same configured character state and explicit profile controls.

A crash may leave gateway health `quiescence_required`. Confirm the old inference has
actually drained/unloaded before using `gateway --acknowledge-quiescence`; an HTTP cancel
alone is not proof. The restarted gateway accepts legacy extraction requests unchanged.
