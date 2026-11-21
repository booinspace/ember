# Root ADR 0022: private Ember memory for OpenCode

**Status:** Recall deployed on Miku. Delegated notes implemented and tested on synthetic memory; live write deployment and OpenCode validation pending.

The operator opted to share all ordinary recall-eligible memories for Ember's active character with OpenCode. Keep the existing engine and SQLite on the Ember host. The [agent runtime adapter](../../ember-agent/docs/adr/0012-memory-mcp.md) is a separate, default-off loopback listener. The [runbook](../opencode-memory.md) describes credentials, SSH forwarding, model privacy, and a fresh-session smoke test.

`deep_recall` reuses Ember’s existing investigation service on the same listener. Its MCP projection stays claims-only; browser archive and evidence inspection remain separate. No change to deployment credentials or the OpenCode server entry.

This is not Phase 10's outbound MCP tool client. No new canonical owner, memory ABI,
migrations, generation calls, or automatic transcript ingestion. Existing recall/suppression
controls still apply; access cannot revoke copies already delivered to a client.

## Delegated note writes

With `memory_mcp.write_enabled = true`, the operator delegates append-only note capture to
holders of the existing MCP bearer. OpenCode can submit one short, single-line note at a time.
The server binds the destination to the `OpenCode` library and the active character; MCP callers
cannot choose an identity, change existing notes, alter source settings, or grant archive access.
Broad recall is unchanged. Workspaces with hosted or unverified inference must not use the tool;
the MCP bearer does not attest model routing.

The existing engine owns the note source, exact evidence, library acceptance and character
adoption. Audit and provenance identify delegated OpenCode notes, not human-reviewed imports.
Only exact submitted notes are kept, not prompts or transcripts. Exact retries are idempotent;
separate library and character transactions can be retried if adoption fails after storage.
Recall visibility follows the existing library selection and suppression rules.

Check default-off discovery, destination isolation, audit provenance, retry and restart,
character recall policy, and rejection of raw or oversize payloads using synthetic notes.
