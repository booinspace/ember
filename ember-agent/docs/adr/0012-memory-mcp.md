# Agent ADR 0012: private memory MCP listener

See [Root ADR 0022](../../../docs/adr/0022-opencode-memory-recall.md) for scope and deployment status.

`ember start` optionally starts a separate IPv4 loopback listener using the pinned MCP SDK and the existing character-bound client. Startup requires a bearer secret; allowed Host headers are loopback, and browser origins are rejected. Missing credentials or a busy port leave normal Ember operation available. The runtime closes the listener before closing its memory client and engine.

`recall(query)` accepts only a search string. It uses `retrieve_context` for bounded ranked beliefs, validates the engine's dependency certificate before disclosure, and projects claims with revision IDs. No archive query, implicit pin injection, or mutation. One call at a time, a four-second deadline, and bounds on query, request body, and response limit its impact on dialogue. Synthetic wire tests cover authentication, host/origin checks, tool discovery, read scope, recall policy changes, and shutdown.
