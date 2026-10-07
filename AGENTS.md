# Project instructions

This workspace stages two independently packaged first-party projects:
`ember-memory/` and `ember-agent/`. Do not introduce a third product
repository or vendor an external agent framework into either core.

`PLAN.md` is the authoritative design for all future tasks. Read it, especially
`## Engineering language and code-quality standards`, before changing code.
`docs/design-plan.md` is a historical copy of the original attachment, not the
current plan. Use `docs/phase-minus-one.md` for scaffold status only.
Phase 0a can proceed with public-format research, provisional source models, and
synthetic test coverage while the archive is pending. Phase 0b requires the actual
ChatGPT export; synthetic fixtures cannot establish account-specific capabilities.
Phase 1 generic ABI/archive 0.1 core is implemented and experimental. Engine work
can use its contracts while source-dependent acceptance remains open. Read
`docs/phase-1-contracts.md` and the current compatibility matrix before extending it.
Phase 2 engine 0.1.0 is implemented and awaiting user architecture review; read
`docs/phase-2-canonical-engine.md` and ADR 0003 before extending it. Phase 3 has
not started. Deliver one phase at a time: define behavior, ownership, existing
abstractions, necessary primitives, invariants, failures and acceptance tests before
implementation. Keep ADR/current PLAN status updated, explain introduced coupling,
validate, then stop for user review before the next phase.

## Boundaries

- Memory ABI has no SQLite, LiveKit, Ollama, MCP, or harness dependencies.
- Character business logic depends on public ABI/client contracts. Concrete
  memory construction belongs in composition, maintenance, or backend adapters.
- All future primary-model generation, including warmup/extraction, goes through
  the single ember gateway. No direct Ollama generation fallback.
- Personality and durable memory are independent of optional providers.
- Canonical IDs, models, schemas, migrations, and importer normalization are
  provisional until their planned phases; do not mistake scaffold versions for
  published ABI/archive versions.

## Sources and privacy

- Retain raw exports verbatim. Inspection does not extract or mutate them.
- Do not commit real archives, personal messages, credentials, runtime state,
  or unsanitized reports. Use ignored `.local/` roots.
- Never persist hidden reasoning in normalized records, fixtures, diagnostic
  reports, logs, indexes, or prompts. Raw original sources remain verbatim.
- Never fetch archive attachments remotely during inspection/import.
- Do not infer saved memories are present from conversation text.
- Preserve graph ambiguity, unknown metadata, source time, and evidence in the
  canonical importer when implemented; do not flatten the graph prematurely.

## Development

Python 3.12 with uv; dependencies are installed during setup, never startup.
Root `pyproject.toml` is the canonical Ruff/Pyright/pytest configuration and contains
development tooling only; it is not a third product package. Ruff uses Python 3.12,
100-character lines, four-space indentation, and double quotes. Pyright runs in
strict mode on all first-party production code and scripts. Public APIs must be
typed and documented. Narrow untrusted input immediately; avoid `Any`. Serialized
and runtime contracts use Pydantic v2, while internal state may use dataclasses.
Use pytest, with explicit integration/end_to_end markers where applicable.

The quality gate from the workspace root is:
`uv sync --locked`, `uv run --locked ruff format --check .`,
`uv run --locked ruff check .`, `uv run --locked pyright`, and `uv run --locked pytest`.
`python scripts/check.py` runs the same installed checks without downloading packages.
After setup, use the installed `ember-memory` and `ember-agent` CLI entry points.
The main application command is `ember`; `ember-agent` is a component alias.
Keep tests meaningful: archive safety/privacy and architectural boundaries need
coverage; empty placeholder directories do not need tests.
