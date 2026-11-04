# 0009 — Scoped MCP adapter and durable tool receipts

Status: implemented; original Phase 10 acceptance verified for the documented profile.

`mcp_contracts` owns strict deployment/discovery contracts. `mcp_tools` uses the
pinned SDK over bounded owned stdio or dedicated HTTP clients. Fresh client and
credential scopes bind character/session identity and deadlines. Discovery confers
no authority; schemas/metadata/policy/credential changes revoke old invocation IDs.
A safe bounded JSON Schema profile avoids synchronous regex/recursive CPU attacks.
No callback advertises sampling, roots or elicitation; no direct model call exists.

The adapter calls the low-level tool method once after same-connection rediscovery,
avoiding high-level automatic input-required retries. Results distinguish native
success, failure, timeout and unknown write effect. Dispatch provenance comes from
the trusted adapter, never remote data. Exact confirmations and no-replay effects
reuse TaskStore; no-dispatch evidence is durable without falsely claiming a write
occurred. Cancelled private results remain suppressed without erasing outcome.

External tools UI and Tasks share operator authentication/origin/body bounds.
Availability and denied requirements are visible, approvals identify the bound
server/tool/effect, and progress/cancellation retain the handle on submission retry.
Receipts are escaped, bounded and explicitly inspected. No result becomes memory.

Stdio executables remain operator-trusted OS processes. Minimal env/temp cwd are
not a sandbox. The atomic profile does not share persistent stdio workflow state.
Windows jobs and Unix groups own children; shutdown does not rely on a stale PID
or mistake launcher exit for all owned children exiting. HTTP rejects redirects,
proxy inheritance and compressed bodies before untrusted bytes reach the SDK.

See [Root ADR 0019](../../../docs/adr/0019-direct-mcp.md),
[runbook](../../../docs/phase-10-runbook.md) and
[validation](../../../docs/phase-10-validation.md).
