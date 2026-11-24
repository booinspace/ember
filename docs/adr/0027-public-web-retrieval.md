# Root ADR 0027 — Public web retrieval and Web Research preview

The [Conversation planning amendment](#natural-language-conversation-tool-planning-amendment)
extends the explicit-command path without changing provider setup.

**Status:** Opt-in implementation; scripted preview evidence only. No live provider,
Miku Strata, native Linux, or physical voice qualification.

## Decision

Ember owns retrieval, scope checks, deadlines and source receipts. Conversation
uses it directly for explicit public search or one literal page URL. Tasks can
opt into **Web Research** for a cancellable, multi-step loop on a separately
configured long-lived OpenCode 2.3.2 worker. Every task gets a fresh session.
Inspect remains read-only. Build retains its isolated offline container per task.

Use Exa Search with highlights and Contents with text, Parallel Search and
Extract with excerpts, or [keyless Exa MCP](#keyless-exa-mcp-for-conversation-and-tasks).
These are page passages, not full coverage or proof of a live fetch. Ember
synthesizes through its existing gateway, not a provider's answer or agent API.
No automatic provider fallback. SearXNG and installed command
search remain independent snippet/reference paths. The inbound Memory MCP and
recall policy do not change. OpenCode's built-in websearch/webfetch are not used.
See [Agent ADR 0015](../../ember-agent/docs/adr/0015-managed-opencode-preview.md#public-web-research-extension).

## Setup and rollback

1. In the selected profile, choose one hosted provider. For keyless setup use
   [`provider = "exa_mcp"`](#keyless-exa-mcp-for-conversation-and-tasks). For
   credentialed REST, put the secret in the Ember service environment, not the
   TOML, dashboard, shell command, or OpenCode profile. Example:

   ```toml
   [public_search]
   provider = "exa"
   api_key_env = "EXA_API_KEY"
   ```

   To qualify Parallel separately use `provider = "parallel"` and
   `api_key_env = "PARALLEL_API_KEY"`. Do not enable both in one profile.
2. For Tasks only, set `research = true` in the existing `[harness]` section
   alongside the pinned managed OpenCode settings. Native Conversation does not
   require a harness. Use public, non-sensitive queries and URLs only. Ember
   rejects known private/credential-bearing forms; syntax cannot establish that
   an apparently public hostname or literal text contains no private data.
3. After an **authorized** rollout, check Tools for capability status. Try
   `Exa search: <public query>` or `Exa read: <public URL>` in Chat (use
   `Parallel` or `Exa MCP` instead when configured). The provider name authorizes the exact
   input. This remains a shortcut alongside the Conversation preview below.
   In Tasks select **Web Research** and confirm the named provider when submitting
   a public goal. Put each approved `Search: <public query>` and each approved URL
   on its own line. The task keeps its provider grant across restart and refuses
   dispatch if the provider changed; submit again to approve the new provider.
   Keep Conversation connected. The updated gateway admits Research model steps
   during idle inference. Foreground requests take priority between steps. Upgrade
   the agent and gateway together for this behavior. Cancel stays available while
   waiting and fences active tool grants before the database write.
   Each retrieval has its own receipt source IDs, even when URLs repeat. Links
   establish retrieval provenance, not claim accuracy.
4. Turn `research` off to remove that worker. Remove or change `[public_search]`
   to remove hosted retrieval or restore a snippet-only provider. No memory or
   archive migration is involved.

## Verification

Isolated macOS: 1,299 Python tests passed, including installed OpenCode 2.3.2
and the pinned Build image with scripted backends. Twelve tests requiring
configured live models, CPU BGE assets or an installed LiveKit server were
skipped. Frontend: 32 unit and 25 isolated browser tests passed; one live LAN
and two live voice/recall cases were skipped. Ruff, strict Pyright, schema export,
frontend formatting, type checks, and build passed.

Review regressions cover receipt provenance when URLs repeat, Exa Contents
status ID matching, denial of a previously issued grant during cancellation
persistence, and refusal to dispatch queued Research after a provider change.
Archive tests confirm a clipped command cannot cause hosted replies to replay
as assistant history. No live provider requests, Strata runs, Windows Research,
native Linux qualification, or service changes were part of this gate.

## Acceptance mapping

| PLAN gate | Evidence and result |
| --- | --- |
| P9-AC-01/02/04 | Scripted pass: deterministic Build admission, active task list and typed realtime wait; same durable router and outbox. |
| P9-AC-03/06 | Scripted pass for waiting cancellation, task receipt validation and isolation of operational records. Live restart and provider cancellation not qualified. |
| P9-AC-05 | Scripted pass: router waits before starting worker; gateway remains authoritative for P2, including a Conversation reconnect race. Live Strata not run. |
| P9-AC-07 | No recall-policy change; public synthesis does not receive recall or chat history. |
| P11-AC-01/02/06 | Pinned macOS OpenCode performed repeated search/read loops with one Research process and fresh sessions through scripted P2 and retrieval. Live provider, live Strata and Windows Research not run. |
| P11-AC-03/05 | Scripted task progress, exact call grants, cancellation and bounded source receipts. Full provider lifecycle and private-context delivery not qualified. |
| P11-AC-04/07 | Inspect, Build and Research have distinct grants; no memory/skills grant added. Native workers are not an OS sandbox; production isolation and live rollback remain open. |
| P12-AC-01–06 | Not run. Skills remain disabled for the managed workers. |

Dashboard tabs separate Conversation, Activity, Inference context, Memory
sources, Prompts, Tasks, Run an MCP tool, Skills, Tools & providers, and Voice and
speaking speed. Tab changes do not disconnect Conversation. Browser tests cover
tab navigation, task visibility, and voice controls; live LAN and physical-device
checks remain separate.

## Natural-language Conversation tool planning amendment

**Status:** Implemented in the isolated worktree; scripted tests passed. No
live provider qualification or rollout. See
[PLAN preview criteria](../../PLAN.md#conversation-tool-planning-preview-acceptance).

Users can ask for public search, a page read, Inspect or Build in ordinary
language. On bounded turns, the local planner sees the configured tool menu
before validation rather than needing a keyword match to see a tool. It sees
only the current turn and can propose a first action or decline. Ember validates
intent, order, scope and exact arguments before execution. Ordinary no-tool turns
continue through regular recall and reply. Further steps see only bounded
same-turn evidence or staged status. The preview supports one public lookup
followed by optional Inspect and Build. Explicit commands remain shortcuts.

Ember sends only a literal public query or one literal public URL clearly
requested in the current turn. A page request may include a descriptive label
before the URL; the label stays local. If the proposed input is rewritten, ambiguous,
or not supplied literally, show the exact provider, action and argument when
safe. Require a new provider command for retrieval alone or a fresh chain with
exact input. There is no pending approval or automatic resume. Scope checks
still reject private or credential-bearing inputs. Only the validated public
argument and provider authentication cross the boundary, never recall, prior
chat, other credentials, raw workspace or task output. Returned text cannot
authorize another call.

Native public retrieval may run while Conversation stays connected. Inspect,
Build and Web Research remain P2 and can start model steps during idle inference
with a healthy gateway that supports idle task admission. Queued foreground work
has priority before each new step; active native generation is not interrupted.
Web Research stays Tasks-only.
An Inspect-before-Build request requires durable same-turn Inspect success;
only a bounded untrusted summary can reach Build. Record public provenance on
the Build receipt when retrieval was used. Required-step failure or cancellation
blocks Build. See
[Root ADR 0026](0026-managed-opencode-build.md#conversation-planning-and-build-prerequisites-amendment)
and [Agent ADR 0015](../../ember-agent/docs/adr/0015-managed-opencode-preview.md#natural-language-conversation-tool-planning-amendment).
Read-only Inspect, offline Build and task-only Web Research keep their existing
grants. No direct MCP, skills, memory or provider fallback is added.

## Conversation-first routing

This amendment supersedes the separate first-pass planner for ordinary turns.
CharacterCore lets the same local generation answer with current authorized
context or propose a tool. It starts an effect only after it receives a
complete, validated action with no visible text. It can recover an ordinary
reply from one rejected proposal without replaying effects. The existing
workflow consumes the first valid proposal and keeps later requests isolated
from recall and prior chat. Explicit page reads use the existing public-web
adapter and local source-checked synthesis.

A page clarification can authorize one exact pasted link on the next turn
after full delivery. Core retains the link, provider revision and evidence
certificate only in the session's acknowledged history. It validates their
currency before dispatch. Ambiguous, unsupported and private inputs confer
no authority. No new provider, worker grant, task transition or ABI is needed.

| PLAN criterion | Current evidence and status |
| --- | --- |
| CTP-AC-01 | Passed scripted text and LiveKit SDK cases in `test_tool_workflow.py`. Ordinary recall gets one generation, and mistaken or late proposals have zero effects. Real-model intent and voice timing on Miku pending. |
| CTP-AC-02 | Passed scripted literal and Markdown reads, bare public URL normalization, exact mock Exa egress, provider mismatch, and no egress for unsafe or revoked inputs. Live Exa pending. |
| CTP-AC-03 | Passed scripted composed-runtime chains with a connected gateway reservation. Live Strata and physical voice timing pending. |
| CTP-AC-04 | Passed scripted dependent Build, malformed and interrupted action cases. Existing durable prerequisite and gateway release checks remain applicable. |
| CTP-AC-05 | Passed scripted source-checked synthesis, evidence-bound follow-up and delivery interruption cases. Miku browser replay pending. |

The macOS locked dependency check, Ruff formatting and lint, and strict
Pyright passed. The locked Python module test run passed **1,555 tests** with
17 skipped.

Read-only preflight found Miku at baseline `5a2804b` running the Strata
`qwen3.8-flash-next-iq3_s` profile with Exa MCP configured. Its active deployment
config has local edits. The shared GPU run, live Exa and physical voice were
not run. Production rollout remains a separate authorization.

## Keyless Exa MCP for Conversation and Tasks

**Status:** Opt-in. A live Miku probe reached initialization but sent no page-read
call. The session fix is tested offline; live retrieval with the fix is unverified.

```toml
[public_search]
provider = "exa_mcp"
```

Select this instead of another public search transport. No key, endpoint, command,
proxy, or tool options. Try `Exa MCP search: <public query>` or
`Exa MCP read: <public URL>` after authorized setup. `Exa search:` still means
credentialed REST. Natural-language planning grants only an exact standalone
public query or one literal public URL; rewritten and ambiguous proposals never
leave Ember without a fresh, explicit request. Use only non-sensitive public inputs.

Ember owns a dedicated direct IPv4 MCP transport to
`https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa`. One bounded handshake
and one fixed tool call per approved lookup (one search result or one page URL).
An optional session ID is opaque [MCP visible ASCII](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#session-management),
not a fixed token format. Accept one header of up to 1,024 bytes and echo it
unchanged only to the fixed endpoint during that lookup. Invalid or duplicate
headers stop the handshake before any public argument is sent.
No tool discovery, server-initiated actions, OAuth, credentials, cookies, prior
chat, recall, or workspace context. The adapter does not follow redirects or
retry. It parses the provider's formatted text into bounded, untrusted excerpts
and attributes page evidence only to the requested URL. Freshness and coverage
are not verified. Exa documents free rate limits and direct IPv4 for keyless access;
blocked or rate-limited requests fail without alternate providers. Natural-language
Conversation shows a fixed failure reason and a content-free Activity code:
HTTP 401/403 is `public_web_access_denied`, HTTP 429 is `public_web_rate_limited`,
and an MCP tool error is `public_web_tool_error`. A successful response with no
excerpts is `public_web_empty`; it cannot satisfy a dependent task. Activity
never records the query, URL, or upstream error text. See [Exa MCP
reference](https://exa.ai/docs/reference/exa-mcp) and the source snapshot
[`81d687e`](https://github.com/exa-labs/exa-mcp-server/tree/81d687e7669b3769007ad46b4fd6839f7c2b6414).

With `[harness]` `research = true`, Tasks uses the same Ember-owned adapter for
Web Research. Confirm **Exa MCP** at submission; consent to credentialed Exa REST
or Parallel does not transfer. The worker still gets only Ember's task-scoped
search and page broker, not direct access to Exa MCP. Each search requires an
exact approved `Search:` line. Each page requires an exact standalone goal URL or
a URL from a validated source returned in the same task. Links inside excerpts
are not grants. Existing limits of three searches and three page reads per task
remain; Exa MCP returns at most one source per call. Inspect, offline Build and
direct MCP grants are unchanged. Remove `[public_search]` or explicitly select a
different transport to roll back; no migration.

| PLAN gate | Evidence and result |
| --- | --- |
| CTP-AC-01/02/04 | Offline `test_exa_mcp.py` and `test_config.py` pass: opt-in setup, exact egress and approval, private input rejection, bounded JSON/SSE, cancellation, and no fallback. |
| P9-AC-05 / CTP-AC-03 | Runtime and gateway regressions cover idle task admission and foreground priority before native start. The pinned OpenCode 2.3.2 Research worker returned source receipts twice with one Conversation reservation held throughout, using scripted inference and retrieval. Live inference and voice were not run. |
| P11-AC-04/05 / CTP-AC-05 | Offline `test_exa_mcp.py` and `test_research_worker.py` cover shared adapter composition, exact task grants, same-task source URLs, provider-bound consent and receipts. No direct MCP or Build grant added. |
| Live provider/platform | Miku initialization returned HTTP 200 and an opaque dotted session ID. Pre-fix client stopped before tool dispatch. Post-fix retrieval not run. |

## Declarative provider bindings

Ember resolves `search_public` and `read_public_page` independently at startup.
Each gets at most one binding. Conversation and dashboard use resolved metadata.
Legacy `[public_search]` remains supported, including keyless `exa_mcp`. Exa REST
and Parallel REST remain separate. Snippet-only SearXNG and installed commands
may coexist with a custom reader. Overlapping bindings are errors, not fallbacks.

Example from the offline structured-reader fixture, not a live provider:

```toml
[public_web_mcp.read]
provider = "structured_fixture"
label = "Structured Fixture"
endpoint = "https://mcp.example.com/mcp"
tool = "web_fetch"
input_field = "urls"
input_as_list = true
fixed = { limit = 1, mode = "extract" }
result = { rows = ["data", "pages"], url = "address", title = "name", passages = "body" }
```

Use `[public_web_mcp.search]` to bind search. Input is one exact literal or its
singleton list. Fixed scalar arguments are not templates. `rows` walks up to
four object keys in `structuredContent` to a source list. `passages` selects
text or excerpts; optional `truncated` selects a boolean field. An unfamiliar
text format needs a named decoder and fixtures. Do not bind a tool that needs
a rewritten query or inferred objective. Optional `bearer_token_env` selects
only that provider's auth. Never put secrets in endpoints, fixed arguments,
queries or page URLs. Operator-reviewed endpoint configuration is the grant;
readiness is local configuration, not upstream health or proof inputs are public.
Endpoint validation is syntactic. Configure only trusted provider hostnames;
Ember does not attest DNS or the provider's own fetch policy.

Public and direct MCP share the pinned SDK session and single-call primitive,
not transport policy, grants, sessions, or credentials. The public profile uses
a POST-only transport for MCP `2025-06-18`: bounded JSON/SSE and one fixed tool
call. No GET, resumption, DELETE, callbacks, discovery, or retry. Nonterminal
results and recognizable reflections of credentials are rejected before decoding.
Short session IDs match standalone tokens, not incidental substrings inside
words or URLs. Named text formats use one decoder registry. Format-specific
input guards can only narrow accepted input. Direct MCP keeps discovery, schema checks, exact write
confirmation, and uncertain-effect receipts. Direct reads keep `ToolRouter`'s
character/task scope and receipt checks. Public retrieval instead returns
bounded `WebEvidence` to its authorizing caller; it does not inherit direct
MCP grants or require a durable task to read a page in Conversation.

Research needs two ready, excerpt-capable operations under one approved
provider ID. Tasks submits the ID and combined binding revision. Ember persists
and rechecks both before dispatch. Changes to endpoint, tool, mappings or
credential reference require fresh consent. Revisions contain no secret values.
Queued legacy Research without a revision needs resubmission; existing receipts
remain readable. The worker gets only Ember's one-use task broker.

To roll back a custom binding, remove its section or restore the previously
reviewed configuration, then restart Ember through the normal authorized
procedure. To disable hosted lookup, remove both custom bindings and any hosted
`[public_search]` selection. Do not edit queued consent or replay tasks.

See [binding acceptance gates](../../PLAN.md#shared-outbound-mcp-and-public-provider-bindings).
No Miku rollout, live provider qualification or physical voice measurement.
