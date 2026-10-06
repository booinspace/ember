# ADR 0001: prepare infrastructure before export-driven contracts

Status: accepted for phase -1.

The supplied design begins with actual export reconnaissance. The export is still
pending, and the workspace is empty. We can prepare independently packaged product
roots, offline inspection, deployment validation, privacy/safety tests, and phase 0
operations without choosing canonical schema semantics from guessed export data.

Stage `character-memory/` and `character-agent/` as the two planned product roots.
The outer workspace contains coordination docs/scripts, not a third application.
Use `0.0.0` scaffold package versions until actual contracts exist. Avoid allocating
canonical IDs, creating SQLite stores, or implementing placeholder clients that
would falsely advertise memory capabilities. Real model calls and all future
background generation use the single agent-owned gateway in its planned phases.

Read-only inspection is immediately useful and independently testable. It outputs
content-free observations and explicit unknown/deferred coverage. Synthetic tests
exercise behavior without establishing support for the pending real export.
