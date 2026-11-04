# Hermes integration boundary

These are deployment adapters, not another copy of Hermes. Ember remains on Python
3.12; Hermes runs in its separately installed Python 3.14 environment. Keeping the
SDK outside Core avoids its dependencies and configuration becoming Ember defaults.
Start Ember with [`start.ps1`](../../start.ps1). On Windows, open the paired native
text client with [`start-hermes.ps1`](../../start-hermes.ps1).

| Files | Purpose | Runs when |
| --- | --- | --- |
| `hermes_client_setup.py` | Explicitly creates the paired Desktop profile and installs its extension; records version/file hashes. | Setup only. |
| `hermes_client_plugin.py`, `hermes_client_transport.py` | Native Desktop hooks send original chat input to Ember, apply bounded history and token limits, and constrain the installed search provider. | Hermes Desktop's Ember profile. |
| `hermes_search.py` | Calls Hermes' installed SearXNG provider for a literal public voice-search request; creates no Hermes chat or extra model generation. | An explicit search in Ember's web voice UI. |
| `hermes_setup.py` | Verifies/seals the installed SDK and isolation assets. | Explicit delegated-worker setup/verification. |
| `hermes_worker.py`, `hermes_transport.py`, `hermes_windows.py` | One-shot delegated tasks, model messages over owned stdio, and native Windows AppContainer/process cleanup. Linux uses its existing separate adapter. | An asynchronous delegated task. |

The source is grouped here; the worker's sealed installed copies may remain in its
external installation. Editing those copies invalidates its qualification receipt.
Do not copy the SDK into either Ember product or remove isolation to reduce file count.

The supported client profile is named **Ember** in the regular Hermes profile list.
Hermes stores its private config, token and chats in its normal user profile storage;
those are runtime data, not tracked project files. The project launcher uses the
installed app's official `hermes.exe --profile ember desktop` command. If Desktop is
already running, select Ember in its profile picker; a second launch only focuses it.

Ember voice and Hermes Desktop retain separate conversation histories. Both use
Ember's character/memory and sole inference gateway for replies. Voice search uses
Hermes as a tool provider; it does not mirror voice messages into Desktop. The
asynchronous worker remains P2 and may wait while realtime voice owns the gateway.

See the [runbook](../../docs/phase-11-runbook.md) for configuration, setup and limits.
