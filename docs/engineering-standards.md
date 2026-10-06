# Engineering standards implementation

`../PLAN.md`, particularly `## Engineering language and code-quality standards`, is
the source of truth. `../AGENTS.md` directs future work to that plan. The original
attachment at `design-plan.md` is historical and does not supersede it.

## Canonical tooling

The root `pyproject.toml` defines one Ruff configuration, strict Pyright settings,
pytest discovery/markers, and a locked development dependency group. The root is a
non-packaged development project; the two product roots remain independently packaged
and locked. Product pyproject files do not override the shared Ruff configuration.

Ruff enforces Python 3.12, a 100-character limit, four-space indentation, double
quotes, import ordering, modern syntax, bug-prone patterns, and function annotations.
Pyright checks all first-party production package sources and maintenance scripts in
strict mode, with no blanket ignores or disabled unknown-type diagnostics.
`py.typed` markers expose the checked types to sibling-package consumers.

## Models and boundaries

- JSON parser output is immediately narrowed to Pydantic `JsonValue`; duplicate
  object keys and nonfinite numbers produce explicit invalid-coverage outcomes.
- TOML input and CLI arguments are immediately validated into strict typed models.
- Serialized inspection/readiness reports and resolved runtime configuration use
  Pydantic v2. Conversation support is represented by a discriminated union with an
  explicit unsupported-shape variant.
- Plain dataclasses hold private per-inspection counters/read buffers and cohesive
  helper results; Pydantic is not used for generic mutable implementation state.
- Public APIs document invariants and unusual failure behavior. Wrapped errors retain
  chained causes. CLI diagnostics omit Pydantic input details, credentials, source
  content, and private paths.
- Blocking inspection/configuration APIs are maintenance operations. Their docstrings
  prohibit direct execution on a future realtime event loop. No async background work
  is present, so pytest-asyncio is not needed yet.

These preparation models do not implement or freeze the phase 1 Memory ABI/archive.

## Tests and checks

Importer unit tests and synthetic fixtures live in
`character-memory/packages/memory-importers/tests/`. CLI tests live in
`character-memory/tests/`; runtime/configuration/import-boundary tests live in
`character-agent/tests/`. Tests use pytest fixtures, assertions, and parameterization.
CLI subprocess tests are explicitly marked `end_to_end`. `integration` is registered
for future service-dependent tests; unknown markers fail collection.

After setup, run the required gate from the workspace root:

```powershell
uv sync --locked
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pyright
uv run --locked pytest
```

To execute the installed checks without resolving/installing anything:

```powershell
.\.venv\Scripts\python.exe .\scripts\check.py
```

`scripts/bootstrap.ps1` synchronizes all three environments with their existing
lockfiles and invokes that same check script. Intentional dependency changes use
`uv lock` in the affected project and must be checked before committing updated locks.

`.github/workflows/quality.yml` runs locked installation and the full gate on Windows
and Linux for pushes/PRs. This is a prepared workflow for the current workspace
layout; hosted execution and branch protection await an actual Git hosting setup.
When splitting the product repositories, preserve the single canonical tooling
configuration and wire sibling/published ABI dependencies explicitly.

Tool behavior follows the primary documentation for [Ruff configuration discovery](https://docs.astral.sh/ruff/configuration/)
and [Pydantic JSON value validation](https://docs.pydantic.dev/latest/api/types/#pydantic.types.JsonValue).
