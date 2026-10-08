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
`docs/phase-2-canonical-engine.md` and
`ember-memory/docs/adr/0003-canonical-engine-and-workers.md` before extending it.
Phase 3 importer/text-runtime implementation is verified and the official ZIP is in a
private library. Read PLAN section 11 and
`docs/adr/0006-phase-three-source-and-runtime.md` before changing it. The ZIP importer,
P0/runtime slice and managed single-room LiveKit text startup are implemented; preserve the private source
scope and do not activate an imported personal claim without operator review of its
exact evidence. The existing narrow GPU extractor matched zero archive messages.
Read `ember-agent/docs/adr/0003-managed-livekit-text-runtime.md` for service/room
ownership. Startup never downloads dependencies; `scripts/setup_livekit.ps1` is
explicit setup. The remaining phase 3 gate needs a source-backed extraction rule with sanitized tests
or a specific supported claim, then the reviewed adoption/restart path. Deliver one
phase at a time: define behavior, ownership, existing abstractions,
necessary primitives, invariants, failures and acceptance tests before implementation.
Keep ADR/current PLAN status updated, explain introduced coupling, validate, then stop
for user review before the next phase.
Phase 4 libraries/characters/bundles are implemented. Read
`docs/adr/0007-phase-four-libraries-characters-bundles.md`,
`ember-memory/docs/adr/0005-libraries-seeds-and-bundles.md`, and
`docs/phase-4-runbook.md` before extending them. Engine/schema 0.2.0 adds backed-up
schema-1 migration; ABI/archive remain 0.1.0. Bundle maintenance requires an
exclusive root owner, and restore publishes a new root with providers disabled.
Evidence-only libraries are partial provenance stores, not rewritten source
messages. Phase 5 retrieval/indexing is implemented. Read
`docs/adr/0008-phase-five-retrieval-and-indexes.md`,
`ember-memory/docs/adr/0006-derived-retrieval-and-generations.md` and
`docs/phase-5-runbook.md`. Phase 5 introduced engine/schema 0.3.0 (user_version 3), ABI
0.2.0 and archive/bundle 0.1.0; Phase 6 versions are recorded below. Canonical covering indexes are backed-up additive
migrations; immutable derived generations are disposable and scope-checked.
Explicit setup installs CPU embeddings; startup never downloads/rebuilds. Updates
reuse vectors but copy the derived database. The user authorized Phase 6; Phase 5's larger capacity/platform and measured
performance gates remain explicit and open. Phase 6 structured extraction, summary
import/review and conservative P3/P4 admission are implemented. Read
`docs/adr/0009-phase-six-extraction-and-background-inference.md`,
`ember-memory/docs/adr/0007-extraction-jobs-and-summary-sources.md`,
`ember-agent/docs/adr/0004-conservative-background-gateway.md` and
`docs/phase-6-runbook.md` before extending them. Current engine/schema are 0.4.0
(user_version 4), ABI 0.3.0 and gateway wire 0.2.0; archive/bundle remain 0.1.0.
Jobs pin source/model/prompt/schema/policy hashes; completed windows commit atomically
and only explicit operator review/approval/seed enters character recall. The user has
authorized a bounded sample from the existing private archive; do not interpret that as
approval of any proposal. Stop for architecture review before Phase 7; no automatic
personal claim adoption, consolidation or preemption is implied.

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
- Preserve graph ambiguity, source time, and evidence in the canonical importer;
  unknown metadata remains in the verbatim raw archive. Do not flatten the graph.

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

## ADR ownership and navigation

`docs/adr/README.md` indexes every implemented phase and cross-component decision.
Root phase ADRs record delivery, integration/coupling and open gates; component
ADRs own detail and link back. Each tree has independent numbering: qualify by
component/path. Update root index/phase record and affected component ADR together.
Do not imply a runtime feature is implemented because its interface/dependency
exists. PLAN remains authoritative.
