# Phase 11 — Hermes worker

The optional provider executes read-only tasks through Ember's existing Tasks UI
and P2 gateway. It does not install a second character/memory engine or move the
voice transport. Setup is explicit; `start.ps1` never downloads Hermes.

## External setup

Pin `NousResearch/hermes-agent` at
`71736c9f32597bbca9d89cede5aa136e55946b2f` in a separate external checkout. Use its
supported editable `uv sync --frozen` installation with a separate system Python
3.14 interpreter, without provider/cloud credentials. Native Windows is the
selected desktop deployment; its AppContainer launcher is being qualified. The
Linux reconnaissance profile uses bubblewrap and its own Linux filesystem.

Copy the `scripts/hermes_*.py` boundary files into an explicit external bridge
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
