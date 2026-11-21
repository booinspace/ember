# Use Ember memory in OpenCode

Ember serves one opt-in, read-only MCP tool: `recall(query)`. It searches all memories eligible for ordinary recall in the active character, with no extra project filter. Existing recall selection, grants, and suppression still apply. Results contain up to eight compact claims with memory IDs, revisions, and coverage status. Raw transcripts, source quotes, other characters, and writes are not exposed. Ember uses its existing memory engine and does not call a generation model for this tool.

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

3. Run `opencode mcp list` from that project and confirm `ember` is connected. In a fresh session, using a local model, ask OpenCode to recall a known, non-sensitive note from Ember. The MCP server tells OpenCode when recall is appropriate; there is no automatic session ingestion or plugin hook.

**Model privacy:** A loopback tunnel and local SQLite do not keep recalled text local if OpenCode sends it to a hosted model. Enable this connection only in projects using trusted local inference if memories must stay on your machines. OpenCode may retain tool results in its own session history; revoking a memory in Ember doesn't erase copies already delivered to OpenCode.

To disconnect, remove the `ember` entry and stop the SSH tunnel. To disable server-side access, remove `[memory_mcp]` and relaunch Ember.
