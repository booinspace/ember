# Independent Ember memory MCP for operator-managed clients

The existing `[memory_mcp]` listener and its settings are unchanged. Use it
**read-only, only with verified local inference**, from an independently managed
client such as the operator's own OpenCode installation. Do not configure it in,
or pass its bearer, recalled claims, or notes to, Ember's [managed OpenCode preview](adr/0025-managed-opencode-preview.md).
That preview is synthetic/non-sensitive only and has no memory connection.

| Read-only tool | Use | Returns |
| --- | --- | --- |
| `recall(query)` | Focused topic lookup | Up to eight ranked claims, IDs, revisions, and coverage. |
| `deep_recall(query)` | Related-memory investigation, optionally date-scoped | Up to 24 claims, bounded associations, date scope when resolved, and outcome. |

Both tools search eligible memories of the active character under current recall
selection, grants, and suppression. They do not return raw archives, evidence
excerpts, or another character's data. Use focused search terms, include dates in
individual queries, and run calls sequentially. An empty or partial result does
not prove absence.

## Existing listener

For a new read-only setup, or to verify an existing one, check the deployment TOML:

```toml
[memory_mcp]
port = 8790
write_enabled = false
```

The listener is opt-in and defaults to loopback when enabled. It uses the private
bearer in `<paths.runtime_root>/memory-mcp.token`; resolve `paths.runtime_root`
relative to the deployment TOML. Retain any existing token and do not print or
commit it. If creating a listener and no token exists, create one at the resolved
path with exclusive file creation and restrict its permissions. For example, from
Windows PowerShell with the root environment installed (replace the path):

```powershell
.\.venv\Scripts\python.exe -c "import secrets, sys; from pathlib import Path; p = Path(sys.argv[1]); p.parent.mkdir(parents=True, exist_ok=True); p.open('x', encoding='ascii').write(secrets.token_urlsafe(32))" 'C:\path\to\runtime\memory-mcp.token'
```

After an authorized Ember relaunch, check for `Memory MCP listening` at the
expected loopback address. If the listener cannot start, Ember reports it without
blocking normal operation. Existing operator-managed SSH forwarding or explicitly
configured [trusted-proxy LAN HTTPS](phase-8-lan-https-design.md) can still be used;
neither makes inference local. Keep the endpoint private. A bearer holder who can
reach it can read eligible character memories.

## Separately managed OpenCode V2

Leave an existing read-only connection unchanged if inference locality is verified.
For a new connection, configure it **in the operator's own OpenCode configuration**,
not Ember's managed `[harness]` profile. Example for direct loopback or a trusted SSH
forward to Ember's loopback listener (the bearer is supplied outside the config):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "ember": {
        "type": "remote",
        "url": "http://127.0.0.1:8790/mcp",
        "oauth": false,
        "headers": { "Authorization": "Bearer {env:EMBER_MEMORY_MCP_TOKEN}" }
      }
    }
  }
}
```

Make `EMBER_MEMORY_MCP_TOKEN` available securely to the operator-managed OpenCode
backend. Check `opencode mcp list` for connection state and use `/mcps` to reconnect
after changing listener settings. Confirm only the recall tools are exposed before
use. A localhost URL, tunnel, TLS, or connected status proves neither model locality
nor where session history is retained. If inference or fallback locality is unknown,
disconnect and do not recall. Never move a session containing recalled text to hosted
inference; revocation does not erase copies already returned to the client.

The existing listener's optional `save_memory` implementation and `OpenCode` notes
are unchanged, but automatic saves are not recommended here: keep `write_enabled`
false. Setting it false leaves existing notes in place. To disable listener access
entirely, remove `[memory_mcp]` and relaunch Ember. Neither change migrates private data.

## Removed: gateway-hosted coding memory

Delete `inference.coding_memory` and `inference.coding_memory_write` from old
deployments; the keys are rejected even if set to `false`. Strata's optional
coding completions route remains separate and no longer supplies Ember memory
tools. Existing `Coding` notes, MCP notes, and Hermes private profiles or chats
are not migrated, copied, or deleted by this change.
