# Root ADR 0022: private Ember memory for OpenCode

**Current scope:** This record applies only to the unchanged operator-managed
listener. Use read-only with verified local inference; do not attach it to
[managed OpenCode](0025-managed-opencode-preview.md). Gateway-hosted coding memory
was a separate integration and has been removed. No private data migration.

**Status:** Recall deployed on Miku. Delegated notes implemented and tested on synthetic memory; live write deployment and OpenCode validation pending.

The operator opted to share all ordinary recall-eligible memories for Ember's active character with OpenCode. Keep the existing engine and SQLite on the Ember host. The [agent runtime adapter](../../ember-agent/docs/adr/0012-memory-mcp.md) is a separate listener that defaults to loopback when enabled. The LAN option requires the configured HTTPS reverse proxy and the same bearer. The [runbook](../opencode-memory.md) covers both transports and model privacy.

`deep_recall` reuses Ember’s existing investigation service on the same listener. Its MCP projection stays claims-only; browser archive and evidence inspection remain separate. No change to deployment credentials or the OpenCode server entry.

This is not Phase 10's outbound MCP tool client. No new canonical owner, memory ABI,
migrations, generation calls, or automatic transcript ingestion. Existing recall/suppression
controls still apply; access cannot revoke copies already delivered to a client.

## Coding completions

The same character-bound recall policy is available through opt-in Ember coding
completions. The two transports are mutually exclusive in a deployed profile.
Hosted query results contain eligible claims with relevance and confidence
metadata, ranked with bounded confidence and recency boosts. Projection uses
available Strata context measured in the prior generation, a conservative byte
guard, and the gateway's existing wire bounds. The final prompt is validated by
Strata. The gateway schedules each model round. Character memory and note
authority remain in the runtime and engine.

A separate opt-in delegates coding notes to the `Coding` library. Exact notes
are idempotent within this authority and auditable as coding notes.

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
