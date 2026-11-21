# Agent ADR 0012: private memory MCP listener

See [Root ADR 0022](../../../docs/adr/0022-opencode-memory-recall.md) for scope and deployment status.

`ember start` optionally starts a separate IPv4 loopback listener using the pinned MCP SDK and the existing character-bound client. Startup requires a bearer secret; allowed Host headers are loopback, and browser origins are rejected. Missing credentials or a busy port leave normal Ember operation available. The runtime closes the listener before closing its memory client and engine.

`recall(query)` accepts only a search string. It uses `retrieve_context` for bounded ranked beliefs, validates the engine's dependency certificate before disclosure, and projects claims with revision IDs. No archive query, implicit pin injection, or mutation. One call at a time, a four-second deadline, and bounds on query, request body, and response limit its impact on dialogue. Synthetic wire tests cover authentication, host/origin checks, tool discovery, read scope, recall policy changes, and shutdown.

`deep_recall(query)` uses the same `DeepRecallService` as Ember’s browser and voice paths, with archive and excerpt budgets set to zero. It shares MCP admission with ordinary recall and the existing deep worker with other investigations. A one-shot result is not retained for browser inspection. MCP revalidates dependencies before returning bounded claims, applicable date scope, and associations between returned revisions.

`save_memory(note)` is absent by default. Enabling writes binds a note-only callable to the active character and a reserved `OpenCode` library through the runtime worker. Saves share MCP admission with both recall tools and return content-free receipts. The listener does not attest inference locality; OpenCode's initialization guidance restricts use to verified local inference.
