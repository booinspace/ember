# Root ADR 0025 — Managed OpenCode preview

**Status:** Implemented as a default-off, synthetic/non-sensitive-only preview.
Production delegation, private-data use, and Phase 11/12 acceptance are not approved.

## Decision and ownership

Replace the Hermes task and Desktop integrations with one optional, launcher-owned
OpenCode **2.3.2** task adapter. Browser text and voice remain Ember's interfaces;
no Desktop or messaging replacement is included. Reuse `AgenticTaskProvider`, the
operational Tasks store, attention delivery, and the single gateway's P2 admission.
OpenCode owns only a fresh task-local tool loop over `read`, `glob`, and `grep` in an
explicit workspace. Ember owns task admission, cancellation, result validation,
character identity, personality, continuity, and recall. Findings do not become
beliefs. See [Agent ADR 0015](../../ember-agent/docs/adr/0015-managed-opencode-preview.md)
for the process, HTTP and failure contracts.

Only the selected provider changes. Direct MCP and native tasks remain independent.
`[public_search]` may point at an installed SearXNG origin (JSON search enabled) or
an independent installed search command. Search is Ember-owned, uses explicit
public query terms, and returns bounded search references and snippets without
fetching the pages. Neither search nor MCP is granted to managed OpenCode. There is
no live skill loader or page reader; existing portable assets and UI/contract
scaffolding do not make those capabilities live.

Gateway-hosted coding memory is removed. Existing opt-in Strata P5 coding
completions, including their existing model and budget controls, are independent of
this P2 preview and expose no Ember memory tools. [Independent memory MCP](../opencode-memory.md)
is unchanged: use read-only from a separately operator-managed client with verified
local inference only. Do not attach it to the managed worker. No private data is
migrated, copied, deleted, or reactivated by this change, including existing Hermes
profiles/chats, coding notes, and MCP notes. The decision supersedes Hermes adoption
and live skills directions in [Root ADR 0016](0016-planned-hermes-and-runner-evaluation.md),
[0021](0021-hermes-delegation.md), and [0023](0023-shared-harness-skills.md); their
qualifications and runbooks are historical. [Root ADR 0022](0022-opencode-memory-recall.md)
still governs the separate listener.

## Operator setup and rollback

1. Leave `[harness]` absent for normal deployments. Remove obsolete `[hermes]`,
   Hermes `[harness]` and `[hermes_client]` settings, and `inference.coding_memory`
   and `inference.coding_memory_write` (including keys set to `false`). Configuration
   rejects them; there is no compatibility shim. If present, remove Hermes
   `[public_search]` or replace it independently, e.g. `provider = "searxng"` and
   `searxng_url = "http://your-installed-searxng:8095"` in `[public_search]`.
2. To try the preview, use a separate synthetic deployment and a dedicated,
   non-sensitive workspace outside and not containing the data or runtime roots.
   Install OpenCode 2.3.2 separately; Ember neither downloads nor upgrades it.
   On Windows, verify the installed executable is readable and runnable by Ember's
   unelevated account. Moving it out of an elevated staging directory can retain
   administrator-only permissions. Pin the exact executable bytes using their
   lowercase SHA-256 digest. An example
   declaration (replace both absolute paths and the digest):

   ```toml
   [harness]
   provider = "opencode"
   command = ["/absolute/path/to/opencode"]
   revision = "2.3.2"
   profile_sha256 = "<lowercase SHA-256 of installed executable>"
   workspace = "/absolute/path/to/synthetic-workspace"
   private_recall = false
   skills = false
   tools = []
   ```

3. Validate the config with `ember doctor --config <deployment.toml>`; this is not
   runtime readiness. Use **Tasks** when OpenCode is reported available. Use synthetic
   goals and files only. Task text and bounded results can enter Ember's operational
   task records, even though OpenCode uses fresh sessions. Do not pass secrets, private
   repositories, or personal context. To disable the preview, remove `[harness]` and
   relaunch; do not change the memory stores or copy prior task/chat data.

Miku's tracked `deployment.strata.toml` and `deployment.strata-mtp.toml` explicitly
opt into the preview for a dedicated synthetic fixture workspace. They pin the
installed Windows x64 baseline binary under `.local/tools/opencode/2.3.2/`. Use
`Read fixture.txt and return its exact contents.` to smoke-test after relaunch;
expect `EMBER_MIKU_SYNTHETIC_PREVIEW_232`. The independent memory MCP is not
attached to the worker.

## Acceptance mapping

The [installed-binary integration test](../../ember-agent/tests/test_opencode_harness.py)
uses `EMBER_TEST_OPENCODE` to opt in. From the workspace root on macOS, with the
locked development environment installed:

```sh
EMBER_TEST_OPENCODE=/absolute/path/to/opencode uv run --locked python -m pytest ember-agent/tests/test_opencode_harness.py -m integration
```

Real OpenCode 2.3.2 servers on macOS and Miku Windows ran `glob`, `grep`, and two
`read` calls per task through Ember's P2 gateway, returning both synthetic fixture
markers. The deterministic upstream supplied tool calls and checked correlated tool
results. On Miku, the test also passed under the unelevated interactive account
used by Ember, after granting that account read/execute on the pinned executable. The test also covered realtime reservation, subscriber cancellation without
runner release, denied tools and paths, and an active worker crash without disabling
native tasks. Observed P2 requests were bound to the task. This does not qualify live
Strata, production process ownership, or browser/voice operation. Linux was not tested.
There is no OS sandbox or enforced egress boundary on any platform.

| PLAN gate | Evidence and disposition for this preview |
|---|---|
| P11-AC-01 | **Full gate not run.** macOS and Miku Windows observations cover the pinned server's basic session and streamed tool-loop transport. Full compatibility, live runner budgets, and Linux are unqualified. |
| P11-AC-02 | **Full gate not run.** Task-scoped ingress and P2 routing were observed with the deterministic upstream. No live Strata or comprehensive auxiliary-call/no-egress qualification. |
| P11-AC-03 | **Blocked.** Fresh sessions and bounded final text/tool status were observed; required personal context is deliberately unavailable. Interactive input, task updates, resume and artifacts are not offered. |
| P11-AC-04 | **Blocked.** Denied capabilities, path checks, temporary state and process ownership are not OS-enforced isolation; no egress enforcement. No private data use. |
| P11-AC-05 | **Full gate not run.** Revocation of task model ingress and cleanup are implemented. Installed-platform fault and process-tree coverage is not established; there is no personal-context copy to qualify for forgetting or purge. Gateway drain is separate. |
| P11-AC-06 | **Full gate not run.** Real `read`, `glob`, and `grep` calls and bounded receipts are partial evidence only. This is a built-in OpenCode read under narrow adapter policy, not a live Ember MCP broker, actual-model, or attention-delivery qualification. |
| P11-AC-07 | **Full gate not run.** Default-off composition leaves existing core/native/MCP paths in place. No full provider-removal acceptance across character, continuity, ordinary/deep recall, and tools is claimed by the smoke. |
| P12-AC-01–06 | **Blocked for runtime qualification.** No selected live skill provider, progressive loading, or page reading. Portable contracts/assets and historical Hermes evidence do not qualify OpenCode. |

Hermes-specific Phase 8A checks are historical, not retroactively passed or reused.
Independent runner/voice checks and Phase 13 cancellation/release gates are unchanged.
