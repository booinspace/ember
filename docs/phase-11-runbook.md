# Phase 11 — Hermes worker

The optional provider executes read-only tasks through Ember's existing Tasks UI
and P2 gateway. It does not install a second character/memory engine or move the
voice transport. Setup is explicit; `start.ps1` never downloads Hermes.

## External setup

Pin `NousResearch/hermes-agent` at
`71736c9f32597bbca9d89cede5aa136e55946b2f` in a separate external checkout. Use its
supported editable `uv sync --frozen` installation with a separate system Python
3.14 interpreter, without provider/cloud credentials. Native Windows is the
selected desktop deployment; its AppContainer launcher passed the recorded native isolation and task proofs. The
Linux reconnaissance profile uses bubblewrap and its own Linux filesystem.

Copy the `scripts/hermes/hermes_*.py` boundary files into an explicit external bridge
directory, then seal the installed checkout, package inventory
and launcher sources with `hermes_setup.py --source <checkout> --python <venv-python>
--output <new-receipt.json>`. The output is private; creation is exclusive.
Any source, package inventory or bridge change requires a new receipt and tests.
Do not put personal data or credentials into that checkout.

Add a deployment table only after qualification:

```toml
[hermes]
command = [
  "C:/path/to/ember/.venv/Scripts/python.exe", "-B",
  "C:/Users/you/AppData/Local/ember-hermes/bridge/hermes_worker.py",
  "--profile", "C:/Users/you/AppData/Local/ember-hermes/profile.json",
]
revision = "71736c9f32597bbca9d89cede5aa136e55946b2f"
profile_sha256 = "<verified receipt SHA-256>"
search_endpoint = "http://your-installed-searxng:8095"
private_recall = false
```

On native Windows the executable is Ember's installed Python 3.12 interpreter;
the controller reuses its retained-job primitive. The receipt separately pins
the external Hermes Python 3.14 interpreter, SDK and boundary files. Seal using
the same launcher interpreter that will appear in `command`. Arguments select
the bridge file and `--profile` receipt. Core contains no
hardcoded OS or WSL path. The doctor labels configuration as unprobed. Exact
native Windows setup/deny probes must pass before enabling the provider.

The checked desktop qualification uses the isolated checkout's Ember interpreter.
Moving the launcher into another checkout requires a new receipt, even when the
Git revision is identical. Replace example paths with the explicitly installed
paths; the application does not expand environment variables in this command.

The qualified native layout uses `%LOCALAPPDATA%\ember-hermes\candidate-71736c9f\source`
for the external SDK, `%LOCALAPPDATA%\ember-hermes\bridge` for the four bridge
scripts, and a separate uv-managed Python 3.14 installation. Writable task homes
live outside those read-only installation roots. Setup/resealing is explicit;
neither startup nor a task downloads packages.

The opt-in end-to-end test is `ember-agent/tests/test_real_hermes.py`, configured
with `EMBER_TEST_HERMES_CONFIG` pointing to an isolated pinned deployment. It
requires a ready gateway with no realtime reservation, runs a real public search
task through P2 and verifies a distinct-context P0 follow-up. Optional
`EMBER_TEST_HERMES_REPORT` must point into ignored `.local` storage; it records
hashes, counts and timing, not conversation text or credentials.

## Use and supported capabilities

With a configured worker, Tasks offers **Research with Hermes**. Start with:

> Find the official SearXNG search API documentation and summarize its supported
> request formats, citing the returned official source.

Disconnect voice to permit P2 model steps under current conservative admission.
Inspect progress, cancel and read the retained receipt through existing task
controls. Operator artifact export writes only a validated finished summary.
The same capability is available through the character's explicit delegation tool.

The profile allows three public searches and at most 30 broker calls/12 model
steps within the existing 180-second task bound. Each search copies at most 2 KiB
of complete reference rows into the worker, from the unchanged native receipt.
Only copied references can be cited. Three references per search and three final
citations are upper bounds; the total 16 KiB input guard can reject an unfit task.
Search snippets are
untrusted data; no arbitrary page browsing, shell or file access is granted.

Opting into `private_recall` allows one bounded authorized recall. This does not
grant archive hydration or memory mutation. Source selection, forgetting and
revocation remain effective; changed authorization stops the worker and suppresses
its result. Search is disabled after private context is supplied.

Use `-B` for SDK interpreter diagnostics. Running that interpreter outside the
worker without it can create bytecode files and invalidate the sealed inventory;
the inventory includes code, bytecode and configuration data, including untracked
files. A fresh receipt and qualification are required after installation drift.

User-input/update/execution-resume are not advertised. Cancel and create a new
explicit request for changed constraints. A restart marks interrupted native tasks
uncertain instead of replaying them. Success requires the isolated worker to exit
and prove context purge; process IDs in diagnostics are never restart authority.

Remove `[hermes]` to disable delegation. Existing character prompts, history,
ordinary/deep recall, native/MCP tools and voice keep their owners. Hermes messaging
and Desktop chat are not automatically connected to Ember's conversation capture.

## Native Desktop chat and voice search amendment

The ordinary-chat client is separate from the isolated P2 worker above. It uses the
installed native Windows Hermes Desktop app, a dedicated Ember profile, and the
paired loopback endpoint `http://127.0.0.1:8787/hermes/v1`. This endpoint calls
CharacterCore before the sole gateway; the generic gateway model endpoint alone
cannot supply Ember's prompts or memory. Hermes has no Ollama/vLLM credentials.

Add an explicit deployment table:

```toml
[hermes_client]
private_recall = true
input_ceiling = 16384
search_command = [
  "C:/path/to/hermes/.venv/Scripts/python.exe", "-B",
  "C:/path/to/ember/scripts/hermes/hermes_search.py",
  "--source", "C:/path/to/hermes/source",
  "--packages", "C:/path/to/hermes/.venv/Lib/site-packages",
  "--endpoint", "http://your-installed-searxng:8095",
]
```

Paths are explicit installation settings. Core remains Python 3.12; the installed
Hermes provider runs in its own Python 3.14 environment. Restart Ember after changing
this table. It creates a separate `hermes-client.token` in the private runtime root;
this is not a gateway credential. Startup downloads nothing.

Use `scripts/hermes/hermes_client_setup.py --named-profile` to publish a **new**
`Ember` profile under Hermes' regular Windows profile root:

```powershell
.venv\Scripts\python.exe scripts/hermes/hermes_client_setup.py --named-profile `
  --home "$env:LOCALAPPDATA\hermes\profiles\ember" `
  --token-file .local/runtime/non-mtp/hermes-client.token `
  --base-url http://127.0.0.1:8787/hermes/v1 `
  --context-tokens 131072 --search-endpoint http://10.254.254.10:8095
.\start-hermes.ps1
```

Use the actual configured model capacity and private runtime token. Setup publishes
only after the profile is complete, with `gateway.standalone: true` so its client
hooks never load into another profile's shared backend. Existing profiles are never
overwritten. The supported launcher is the tracked `start-hermes.ps1` at the Ember
repository root; the Windows shortcut points there. It opens the official installed
app without another Electron data directory. If Desktop is already open, use its
profile picker to select **Ember**; the native app's second-instance handler only
focuses the existing window. Hermes retains private profile/chat data in its normal
user storage. No debug browser port is needed. Legacy separate-home setup remains
available for compatibility, but is not the normal launch path.

Each native chat has its own server-allocated Core/history, sharing Ember's current
system/personality Markdown, character, canonical memory and dialogue capture.
Client system prompts and old client transcript text cannot replace that context.
The 128K model capacity is advertised truthfully, while each turn still has the
16 KiB input guard and at most 512 output tokens. A live controller allows four chat
contexts, 64 turns per context and bounded retries; restart/deletion requires a new
native chat instead of silently importing an old transcript. Conversation continuity
across app/runtime restarts is not implemented. The browser voice room has separate
conversation history.

Only the native `search` toolset is enabled. Explicit requests such as “Can you search
Google for the SearXNG API?” issue one `web_search` preflight before any private recall
or model call. Queries use only the user's literal terms. Hermes' installed SearXNG
provider calls the configured NAS, with redirects, proxy inheritance and oversized
wire responses rejected. At most three complete public rows / 2 KiB are copied into
the existing prompt budget. Ember then performs one ordinary character generation;
no second search is allowed after recalled context is disclosed. “Google” describes
the request; the actual search service is the configured SearXNG instance.

The web voice path uses that same installed Hermes search provider directly for the
explicit preflight, so it does not queue behind a P2 task blocked by its own live voice
session. The old `searx_tools.py` adapter remains the isolated worker's broker and public
URL validator; ordinary Desktop and voice search use the native Hermes provider.

Private recall is an explicit operator opt-in. On 4 November the user deferred deletion
of copied Hermes chats to prioritize the harness. The pinned Desktop build can retain
selected chat text after its delete API reports success; see the sanitized retention
canary in the validation record. Forgetting still invalidates future Ember prompts,
new cached deliveries and stream retries. It does **not** erase copies already stored
or visible in Hermes. Automatic affected-chat purge remains deferred work, not a
completed adoption criterion. Disable `private_recall` to use public-only chat.

Hermes' own memory, profile learning, compression, title generation, background review,
cloud fallback and other tools are disabled in this dedicated profile. Messaging
accounts and automatic skill learning are not configured by this amendment.

The browser's **Stop speaking** button interrupts the exact current reply or task
announcement and immediately silences its playback. It keeps the room, microphone,
visible text and later turns usable. The gateway still drains an already-running model
generation under its current cancellation contract; Phase 13 owns native abort/release.

The upstream [Desktop guide](https://hermes-agent.nousresearch.com/docs/user-guide/desktop)
explains the official native app/CLI relationship. This amendment qualifies the
installed build and its concrete request hooks separately from upstream main.

For plugin upgrades, use a fresh qualified native process. The SDK can preserve an
older registered provider after reporting a successful plugin reload. The extension
therefore shares its one-shot grants and result/request hooks through the stable SDK
plugin owner; module eviction and concurrent registration are regression tested.
The former date-stamped `desktop-ember-v2-20261104` launcher is superseded by the
project launcher and regular named profile. Existing data is preserved; it is not
silently merged with a new Core session. The [adapter inventory](../scripts/hermes/README.md)
explains each retained boundary file. In particular, voice tool requests do **not**
create Desktop chats or mirror voice transcripts. Desktop chat still routes through
Ember's character/memory ingress and sole gateway.
