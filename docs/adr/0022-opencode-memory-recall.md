# Root ADR 0022: read-only memory recall for OpenCode

**Status:** Implemented and tested on synthetic memory; Windows deployment and live OpenCode check pending.

The operator opted to share all ordinary recall-eligible memories for Ember's active character with OpenCode. Keep the existing engine and SQLite on the Ember host. The [agent runtime adapter](../../ember-agent/docs/adr/0012-memory-mcp.md) is a separate, default-off loopback listener. The [runbook](../opencode-memory.md) describes credentials, SSH forwarding, model privacy, and a fresh-session smoke test.

This is not Phase 10's outbound MCP tool client. No new canonical owner, memory ABI, migrations, generation calls, write authority, or automatic transcript ingestion. Existing recall/suppression controls still apply; access cannot revoke copies already delivered to a client.
