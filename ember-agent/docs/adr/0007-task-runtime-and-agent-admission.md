# 0007 — Operational dispatch and agent inference admission

Status: implemented slice, Phase 9 acceptance in progress.

TaskRouter owns dispatch and shutdown. ToolRouter accepts only installed
read-only capability names explicitly granted by the task. The provider
returns a validated receipt with character, session, turn and task identity.
The browser obtains authority from the existing operator cookie and exact
origin checks. The conversational command obtains its turn ID from the
shared CharacterCore. The memory ABI remains the only memory boundary.

WAL allows status reads while a short transaction claims or finishes a task.
SQLite work runs off the realtime loop. The operational store coalesces
progress to a state and atomically commits terminal notifications. One event
cursor wakes open task panels without buffering individual updates. Browser
notifications are visual. The existing LiveKit delivery fence remains
responsible for conversational speech. Automatic task playout is open.

The managed gateway creates a distinct agent token for P1 and P2. P1 needs
a current live reservation. P2 waits for release. The existing scheduler
weights P2, P3 and P4 at 4:1:1 and retains FIFO order within a class.
Metadata checks can park a job before generation. An active upstream is
never assumed released on disconnect. P2 content is published only after
complete output validation and journal completion. Existing extraction
credentials retain P3/P4 scope. Phase 13 owns preemption acceptance.

See [Root ADR 0017](../../../docs/adr/0017-phase-nine-operational-tasks.md)
for the benchmark scope, review findings and remaining criteria.
