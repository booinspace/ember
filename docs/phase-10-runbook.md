# Direct MCP tools

Phase 10 uses the official pinned `mcp==2.3.0` SDK. Install with `uv sync --locked`
during setup, then start Ember normally. Startup never installs a service or
fetches a tool package. No servers or grants are enabled by default.

Configure a trusted, already-installed atomic service in deployment TOML:

```toml
[[mcp_servers]]
id = "items"
transport = "http"
url = "http://127.0.0.1:8000/mcp"
bearer_token_environment = "EMBER_ITEMS_TOKEN"
timeout_seconds = 5.0
grants = [
  {name = "get_item", effect = "read"},
  {name = "update_item", effect = "write"},
]
```

Alternatively use a separate stdio process for each operation:

```toml
[[mcp_servers]]
id = "local_items"
transport = "stdio"
command = 'C:\services\items\.venv\Scripts\python.exe'
arguments = ['C:\services\items\server.py']
environment = {SERVICE_TOKEN = "EMBER_ITEMS_TOKEN"}
grants = [{name = "get_item", effect = "read"}]
```

Use the installed absolute executable and argument paths for the host OS; the
HTTP adapter, tool contracts and task engine are shared across Windows, Linux and
macOS. Stdio defaults to an empty temporary working directory. Explicit cwd is a
service configuration choice. Environment entries map child variable names to
parent variable **references**, never literal secrets. HTTP has its own bearer
reference; redirects, proxy inheritance and compressed responses are refused.
Use HTTPS for remote services; plaintext HTTP is restricted to loopback.

A configured executable/service is part of the operator's trusted computing base.
Its OS filesystem/network access is not sandboxed by cwd or environment cleanup.
Do not configure unrestricted shell/filesystem, canonical memory/policy/credential
administration or alternative inference tools under this profile. Ember passes
no canonical root, memory client, inference credential or conversation history.
Tool output cannot create authority, execute code or promote memories.

Open **External tools**, refresh discovery, select a granted tool, enter its JSON
arguments and queue it. The panel retains progress and cancellation. **Tasks**
shows exact write arguments and the bound server/tool/effect before approval;
unresolved identities cannot be approved. Inspect the terminal receipt there.
Descriptions and results are plain escaped text. Discovered annotations never
grant permissions. Only the explicit `grants` classification determines read/write
authority. Optional `input_schema_sha256` on a grant pins its exact schema.

The request/approval identity binds server configuration, metadata, policy,
schemas, credential scope and exact arguments. Changes invalidate the old identity.
A known failure before RPC reports failed or timed out with `dispatched=false`.
A write cancelled/timed out/disconnected after dispatch reports uncertain; its
fence survives restart and cannot automatically replay. Request retries retain
the original handle, including failure/uncertainty; changed arguments need a new
operator request. Cancellation may suppress result data while retaining truthful
terminal state. Dismissing a result only acknowledges its UI notification.

Supported profile: at most eight services, 64 tools per service, 16 discovery
pages, 64 KiB transport frames/bodies, bounded schema/metadata and 3 KiB results.
The safe schema profile supports bounded object/array/scalar constraints and enums;
regex, references, combinators, format execution and expensive uniqueness checks
are rejected. Unsupported schemas disable that server's discovery with a safe
status; other services and native conversation remain independent. The SDK gets
no sampling, roots or elicitation callbacks. Unsupported execution/input-required
extensions fail without model calls or retrying effects.

This is an operator-invoked atomic tool profile. Fresh clients/processes isolate
actors and credentials; in-memory stdio sessions do not persist between calls.
Conversational routing remains the existing count/summary/recall task paths.
Persistent browser workflows, external harness loops and arbitrary downloaded
programs require their later scoped adapters rather than hidden MCP permissions.

## Gateway benchmark suite

The existing gateway remains the sole model owner. Run the complete matched suite
against an idle, already provisioned gateway, with a new output directory:

```powershell
.venv\Scripts\python.exe scripts\benchmark_gateway_suite.py `
  --config .local/config/deployment.non-mtp.toml `
  --output-directory .local/benchmarks/phase10-ollama `
  --samples 30 --voice-samples 30 --allow-reserved
```

The explicit reserved-session override schedules operator testing on the primary
model and can compete with a new user turn. Use an exclusive test window. Cases
are short/medium/long bounded synthetic prompts, P2/P3/P4 queued separately behind
reservation, and streamed CPU speech. Each records an excluded warmup, raw warm
samples, a consumer-cancellation/drain probe, next-P0 latency and sampled VRAM.
Failed cases stop the suite and retain their reports. Previous reports are never
overwritten. No private prompts, messages, memory or audio enter the report.

The seven cases are a gateway comparison suite, not complete hardware/voice
acceptance: voice uses a paced SDK PCM sink, excluding physical mic/STT/network/
speaker buffering. Repeated prompts favor warm prefix reuse. They do not certify
near-128K context, native GPU abort, true concurrent execution, extraction quality
or cold startup. Run the original correctness/conformance tests alongside it.
See the [validation record](phase-10-validation.md) for the exact configuration,
independent review findings, limits and committed measurements.

Primary references: [official SDK](https://py.sdk.modelcontextprotocol.io/),
[MCP tools and untrusted annotations](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).
