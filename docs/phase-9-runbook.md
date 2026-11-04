# Phase 9 task runtime trial

Use a synthetic deployment with its own data and runtime roots. Build the
existing browser frontend and start Ember with that deployment. The Tasks
panel appears when the operational store can start. Normal voice and text
remain available if task storage fails during startup.

Count words and characters in Tasks, or send `/task <text>` or
`Count the words in <text>` in a connected conversation. Ember acknowledges
the queued operation and shows the result in Tasks. Summarization needs an
agent-enabled gateway and an unreserved runner. Disconnect conversation
before queuing a summary. Deep Recall in Tasks uses the existing bounded
service. Open its result from Tasks before its in-memory receipt expires.

Dismiss acknowledges the terminal notification. Save result explicitly
publishes a non-recall result as a private text artifact under the runtime
root. Cancel records a request for active work. Wait for the terminal state
to see the outcome. An interrupted active task becomes uncertain after a
restart and is not replayed automatically.

The managed gateway provisions a separate agent token under the runtime
root. Keep all gateway credentials private. Results and tool receipts stay
in the operational database. The canonical memory root is independent.

See [Root ADR 0017](adr/0017-phase-nine-operational-tasks.md) for the
acceptance matrix and the scope of hardware checks.

In conversation, the model can also select an installed task route for a
request to count, summarize, or investigate. It queues one operation using
text from the current message. Summary requires the agent gateway.
