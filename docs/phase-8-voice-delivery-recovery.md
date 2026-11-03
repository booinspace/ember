# Phase 8 voice delivery recovery

LiveKit's voice turn hook can run after an interrupted or failed speech handle
has finished without emitting an assistant conversation item. Ember's pending
delivery then remains unresolved and blocks subsequent user turns. A delivery
task snapshot alone is not proof that a speech handle has finished or that no
receipt will arrive.

The existing HostedRoom owns reconciliation. It retains the speech handle when
generation starts. Before accepting another turn, it waits for that handle and its
linked SDK speech tasks to finish and for the active gateway stream to stop, then
drains already scheduled receipt tasks. If the same active turn
still has a pending delivery, the room acknowledges an empty incomplete delivery
and releases it. Actual delivered prefixes continue to enter the existing receipt
path; a missing receipt never invents heard speech or a complete history pair.
No source, memory, gateway scheduling or archive ownership changes. Failures in
the receipt or completion fence remain errors, not permission to discard active
playback. Existing native input capture and delivery validation stay in force.

LiveKit Agents 1.8.5 can mark an interrupted handle done before its cancelled
tasks finish. Its public handle completion is therefore insufficient for this
fence. The adapter explicitly reads the handle's private `_tasks` list and retains
the handle after receipt acknowledgement; upgrading LiveKit requires rechecking
this coupling and the forced-interruption regressions. If no handle is available,
the runtime fails closed rather than assuming playout completed.

Acceptance covers a missing receipt followed by a successful new voice turn,
delayed and interrupted receipt ordering, preservation of verified prefixes,
no cleanup before playout and generation end, and existing permission-fencing
and text paths. Real microphone and interruption tests remain separate.
