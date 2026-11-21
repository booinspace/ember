# Use Ember memory in OpenCode

Ember serves two read-only tools on the same opt-in MCP endpoint:

| Tool | Use it for | Returns |
| --- | --- | --- |
| `recall(query)` | A quick topic lookup | Up to eight ranked claims, with memory IDs, revisions, and coverage. |
| `deep_recall(query)` | An explicit investigation across related memories, optionally scoped by date | Up to 24 claims, bounded links between returned revisions, any resolved date scope, and an outcome with reasons for limits. |

Both use the active character’s eligible memories with no extra project filter. Recall selection, grants, and suppression still apply. Neither tool returns raw transcripts, source quotes, or other characters’ data. Neither writes memory or calls a generation model. Deep recall reuses Ember’s existing investigation service, but the browser’s source inspection remains separate.

## Windows: enable the listener

1. Deploy this code to `C:\Users\operator\repos\ember`. Add this section to the deployment TOML you already use to launch Ember:

   ```toml
   [memory_mcp]
   port = 8790
   ```

2. Create a random bearer token at `<paths.runtime_root>\memory-mcp.token`. Resolve `paths.runtime_root` relative to your deployment TOML. With the repo's virtual environment installed, run this from the Ember checkout, replacing the path with your actual runtime root. It won't print the token or overwrite an existing one:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; import sys; from pathlib import Path; p = Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); p.open('x', encoding='ascii').write(secrets.token_urlsafe(32))" 'C:\path\to\runtime\memory-mcp.token'
   ```

   Keep the token in a private directory accessible only to your account and the Ember runtime. Anyone who has it and can reach the listener can recall private memories.

3. Relaunch your existing Ember runtime with the same config. Look for `Memory MCP ready on http://127.0.0.1:8790/mcp`. The listener has no LAN bind and does not use the browser's LAN settings. If it can't start, Ember prints a diagnostic and continues without MCP.

## OpenCode machine: connect through SSH

1. If Windows OpenSSH is enabled, run this on the machine where the OpenCode backend runs and keep it open. Use your Windows SSH username if it differs:

   ```sh
   ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8790:127.0.0.1:8790 nathalie@10.254.254.11
   ```

   If local port `8790` is occupied, change only the first port in `-L` and the port in the OpenCode URL below. Don't expose the Windows MCP port directly on the LAN.

2. Copy the token over a trusted channel to `~/.config/opencode/ember-memory.token` and restrict access on the OpenCode machine with `chmod 600 ~/.config/opencode/ember-memory.token`. Don't paste the token into a prompt or commit it. Merge this into the OpenCode project's `opencode.jsonc` (OpenCode V2):

   ```jsonc
   {
     "$schema": "https://opencode.ai/config.json",
     "mcp": {
       "servers": {
         "ember": {
           "type": "remote",
           "url": "http://127.0.0.1:8790/mcp",
           "oauth": false,
           "headers": {
             "Authorization": "Bearer {file:~/.config/opencode/ember-memory.token}"
           }
         }
       }
     }
   }
   ```

3. Run `opencode mcp list` from that project and confirm `ember` is connected. After upgrading Ember on Windows, restart it and reconnect `ember` in OpenCode’s `/mcps` to refresh the tool list. No new tunnel, token, or OpenCode configuration is needed.

## Use deep recall

In a fresh session with a verified local model, ask: “Use Ember deep recall to investigate a known, non-sensitive topic during summer 2025. Separate recalled claims from inference.” OpenCode can call `tools.ember.deep_recall({ query: "…" })` in Code Mode. Include any dates in the query; separate calls do not inherit Ember’s conversation date scope.

Run recall calls sequentially: the MCP listener accepts one at a time. Deep recall also shares Ember’s investigation worker and may return partial or deferred results. `complete_within_budget` is not exhaustive, and a missing result does not prove no memory exists. There is no automatic session ingestion or plugin hook.

**Model privacy:** A loopback tunnel and local SQLite do not keep recalled text local if OpenCode sends it to a hosted model. Enable this connection only in projects using trusted local inference if memories must stay on your machines. OpenCode may retain tool results in its own session history; revoking a memory in Ember doesn't erase copies already delivered to OpenCode.

To disconnect, remove the `ember` entry and stop the SSH tunnel. To disable server-side access, remove `[memory_mcp]` and relaunch Ember.
