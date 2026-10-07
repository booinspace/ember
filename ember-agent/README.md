# ember-agent

Owns personality, realtime interaction, prompt composition, audio/vision/browser,
tools/skills/tasks/events/attention, process composition, and the one inference
gateway. Canonical memory stays in ember-memory; optional external providers
own only delegated execution.

Phase -1 implements an offline configuration doctor. The runtime, gateway, audio,
UI, and provider bridge are reserved directories; no services are started.

```powershell
uv sync --locked --python 3.12 --extra local-deployment
uv run --locked ember doctor --config config/deployment.example.toml
```

The ABI dependency and optional engine extra use editable sibling paths for local
development. Core character modules must not import engine internals. Construction
belongs in `runtime/composition.py` once its phase is implemented.

The actual Ollama tag/digest must be set and verified before generation. There is
no assumed runtime tag for the descriptive model name in the user request.

`../PLAN.md` governs current design and engineering standards. Shared Ruff,
strict Pyright, and pytest configuration lives in `../pyproject.toml`; run the
quality gate from the workspace root. Runtime/configuration/readiness reports use
strict Pydantic v2 models; mutable internal state remains ordinary Python state.

## Architecture documentation

[Agent ADRs](docs/adr/README.md) own runtime/composition/gateway decisions and link to
[root ADRs](../docs/adr/README.md) for delivery outcomes. Memory detail belongs to
[memory ADRs](../ember-memory/docs/adr/README.md). Numbers are local to each tree.
Existing configuration/client dependencies do not imply a shipped runtime; PLAN
remains authoritative.
