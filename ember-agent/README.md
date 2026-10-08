# ember-agent

Owns personality, realtime interaction, prompt composition and the one inference
gateway. Canonical memory remains in ember-memory.

Phase 3 includes direct terminal chat and a runnable LiveKit text room.
`ember start` verifies/starts the configured LiveKit Server and P0 gateway, binds
one canonical character owner and starts its SDK session. Browser/audio integration
and general worker-fleet dispatch remain later phases.

## Setup and running from Windows PowerShell

Run from the workspace root. Install Python packages during setup:

```powershell
uv sync --locked
.\scripts\setup_livekit.ps1
```

Copy `config/deployment.example.toml` to a private local configuration, set the
actual Ollama tag and full digest from `/api/tags`, and set LiveKit mode to
`managed` with the installed executable path. Relative paths resolve against
the configuration file. Startup performs no downloads.

The existing ignored machine configs `.local/config/deployment.non-mtp.toml`
and `.local/config/deployment.mtp.toml` contain verified model pins and the
installed server path. Start one variant at a time:

```powershell
.\ember-agent\.venv\Scripts\ember.exe start --config .\.local\config\deployment.non-mtp.toml
```

In a second terminal:

```powershell
.\ember-agent\.venv\Scripts\ember.exe livekit-chat --config .\.local\config\deployment.non-mtp.toml
```

Use the same config in both terminals. The current MTP/non-MTP profiles bind
separate characters. Ollama must already be available; startup attaches to it.
A matching running gateway/LiveKit service is authenticated and left running on
shutdown. Services started by this launcher are stopped with its owned handles.

Inside chat:

```text
/remember favorite-color-test My favorite color is cobalt blue.
What is my favorite color?
/quit
```

Stop the runtime with Ctrl+C, start it again, reconnect and ask again to verify
durable recall independently of in-memory conversation history. The terminal reports
the number of recalled memories. Ctrl+C in the runtime terminal drains and stops
owned services. `livekit-chat --prompt "What is my favorite color?"` sends one
prompt and exits. Room input is limited to 1,600 characters. API keys and tokens
are kept in ignored runtime files and never printed. The room supports one local
participant and one turn at a time.

The official ZIP has been imported as source history; it has not activated personal
beliefs. Only manually authored or explicitly reviewed/adopted memories enter
character recall. The reviewed real-source acceptance remains open.

`ember gateway` plus `ember chat` remain the direct terminal path without LiveKit.
`ember doctor` validates configuration offline and does not claim service readiness.

## Testing

From the workspace root:

```powershell
.\.venv\Scripts\python.exe .\scripts\check.py
$env:EMBER_LIVEKIT_EXE = (Resolve-Path '.\.local\tools\livekit\1.13.9\livekit-server.exe').Path
.\.venv\Scripts\python.exe -m pytest .\ember-agent\tests\test_livekit_runtime.py -q
```

Set `EMBER_REAL_CONFIG` to an existing pinned local config to additionally run the
isolated actual-Qwen room test. That test starts a missing managed gateway or
authenticates a matching existing one. It uses temporary synthetic memory.

## Architecture documentation

[Agent ADR 0003](docs/adr/0003-managed-livekit-text-runtime.md) records the runtime
ownership, room protocol and remaining limits. [Root ADRs](../docs/adr/README.md)
record delivery across both components; [memory ADRs](../ember-memory/docs/adr/README.md)
own canonical memory. IDs are local to each ADR tree. `../PLAN.md` remains authoritative.
