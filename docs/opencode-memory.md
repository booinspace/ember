# Use Ember memory in OpenCode

Ember serves these tools on one private MCP endpoint:

| Tool | Use it for | Returns |
| --- | --- | --- |
| `recall(query)` | Quick topic lookup | Up to eight ranked claims, with IDs, revisions, and coverage. |
| `deep_recall(query)` | Related-memory investigation, optionally scoped by date | Up to 24 claims, bounded associations, any resolved date scope, and outcome. |
| `save_memory(note)` | Optional automatic capture of a durable decision, preference, or unfinished task | A receipt with library name, memory ID, revision, and outcome. |

Both recall tools search all eligible memories for the active character, with no extra project or library filter. Recall selection, grants, and suppression still apply. Neither tool returns raw archives, evidence excerpts, or another character’s data.

## Windows: enable the listener

1. Deploy to `C:\Users\operator\repos\ember`. Add or update this section in the deployment TOML used to launch Ember:

   ```toml
   [memory_mcp]
   port = 8790
   # Optional. Default is read-only.
   write_enabled = true
   ```

2. If you don't already have a token, create one at `<paths.runtime_root>\memory-mcp.token`. Resolve `paths.runtime_root` relative to the deployment TOML. Run this from the Ember checkout, replacing the example path. It won't print the token or overwrite an existing one:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; import sys; pathlib import Path; p = Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); p.open('x', encoding='ascii').write(secrets.token_urlsafe(32))" 'C:\path\to\runtime\memory-mcp.token'
   ```

   Keep the token private. Anyone who holds it and can reach the listener can recall private memories and, if enabled, append notes.

3. Relaunch Ember using the same profile (`.\start.ps1` for Miku’s default). Look for `Memory MCP ready on http://127.0.0.1:8790/mcp`. With writes enabled, Ember also reports `OpenCode memory writes enabled for the OpenCode library.` The listener does not bind to the LAN. If setup fails, Ember prints a diagnostic and keeps normal operation available.

## OpenCode machine: connect through SSH

1. On the machine running the OpenCode backend, start and keep this tunnel open:

   ```sh
   ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8790:127.0.0.1:8790 nathalie@10.254.254.11
   ```

   If local port `8790` is occupied, change only the first port in `-L` and the port in the OpenCode URL. Do not expose the Windows MCP port directly on the LAN.

2. Copy the token over a trusted channel to `~/.config/opencode/ember-memory.token` and restrict access with `chmod 600 ~/.config/opencode/ember-memory.token`. Don't paste it into a prompt or commit it. Merge this configuration into the OpenCode project’s `opencode.jsonc` (V2):

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

3. Run `opencode mcp list` in the project and confirm `ember` is connected. After changing Ember’s tools, reconnect `ember` in OpenCode’s `/mcps` to refresh discovery. No new tunnel or token is needed.

## Recall and automatic notes

- Use `recall` for focused topic searches and `deep_recall` for investigations. Include dates in each query; calls don't share date scope. Run Ember calls sequentially. Partial or empty results do not prove absence.
- With writes enabled, Ember creates or reopens the reserved `OpenCode` library. OpenCode is instructed to save useful notes during chat without per-note approval, unless you opt out. Each call accepts one printable line, up to 512 characters. This is model-led capture, not a transcript hook or a guarantee that every decision will be saved. The server cannot tell whether submitted text contains a secret or was copied from a transcript; only allow trusted clients to write.
- Notes use the active character and fixed library. Other libraries and recall settings are untouched. If the character’s Recall Sources settings exclude `OpenCode`, notes still save but won't appear in recall until you enable that library. Exact retries for the same character are safe; paraphrases can create separate notes.
- To smoke-test, start a fresh session with verified local inference. Share a non-sensitive durable preference, confirm OpenCode got a save receipt, then ask about the preference in a second session. If your OpenCode permissions ask before tool use, allow `ember_save_memory` in that trusted project only.

**Model privacy:** The tunnel and local SQLite do not keep recalled text local if OpenCode sends it to a hosted model. Use Ember tools only with verified local inference. OpenCode may retain note inputs and tool results in session history; don't switch those sessions to hosted inference. Revoking access in Ember does not erase copies already delivered to OpenCode.

To stop saves but keep recall, set `write_enabled = false`, relaunch Ember, and reconnect in `/mcps`. Existing notes remain. To stop server-side MCP access, remove `[memory_mcp]` and relaunch Ember.
