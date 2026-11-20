# Phase 12 acceptance record

Qualification date: 4 November 2026. Full gates: macOS **1,214 passed / 29 skipped**;
Windows **1,212 passed / 31 skipped**; Ruff and strict Pyright passed on both.
Frontend: **31 unit tests**, TypeScript/Vite build and **20 browser checks** passed
(3 opt-in speech checks skipped). Native pinned-SDK/WSL opt-in suite: **8 passed**.
Real P2 text/page tasks and the live HTTPS voice/dashboard proof also passed. This implements portable shared skills through
the selected harness. It does not install OpenClaw or repair Hermes Desktop.
The [runbook](phase-12-runbook.md) describes the operator workflow; sanitized machine
and latency evidence belongs in [the qualification record](benchmarks/2026-11-04-phase12/README.md).

| Criterion | Implementation and meaningful evidence |
| --- | --- |
| P12-AC-01 | Hermes' native frontmatter parser, YAML lexer and `skill_view(preprocess=False)` run in its installed environment. Original Markdown, metadata/extensions, resource paths and hashes survive export/import. Portable package fixture preserves unknown OpenClaw fields without that runtime. |
| P12-AC-02 | Exact source/package IDs resolve duplicate names. CAS binds operator decisions to package bytes and catalog revision. Regression fixtures reject existing import identities, traversal/Windows paths, case collisions, symlinks, missing resources, invalid frontmatter and package changes. Errors are explicit; existing data is preserved. |
| P12-AC-03 | Cached metadata-only catalog; at most eight enabled descriptions enter delegation. Real pinned-SDK proof loads the complete chosen procedure before inference and a reference file only when requested. A repeated load returns a reference to the already present complete text. Compression is disabled. Final serialization remains subject to the same P2 16 KiB guard. |
| P12-AC-04 | Installed capabilities and current grants are distinct from requirements. Worker exposes only parent-authorized wrappers, not native SDK dispatch, shells, installers, credentials or independent memory. Real WSL denial/EOF/cancel/controller-crash tests retain isolation and purge. Current authority is checked before model calls and all copied-result delivery surfaces. |
| P12-AC-05 | Mandatory procedures/resources above the bound are unavailable, never chopped. Serialized history and worst-case broker receipts reserve capacity before invocation. Public page excerpts are explicitly partial data; their smaller bound does not truncate skill instructions. Actual gateway schema rules are regression-tested for advertised skill tools. |
| P12-AC-06 | Native installed SDK tests and actual gateway tasks complement offline, unavailable-provider, raw Markdown/XSS, current-grant and revocation tests. Existing ordinary chat, prompt, recall, memory ABI and MCP checks remain in the full platform gates. The live browser proof exercises catalog inspection, conversation use, literal URL reading and speech stop. |

The automated checks cover parser/adapter behavior rather than treating bundled
package availability as proof that every upstream skill is useful or safe. Only two
small reviewed shared examples are enabled on the desktop. Other skills must be
reviewed and their requirements explicitly granted; incompatible packages stay visible
with reasons. No skill automatically changes personality, canonical memory or scheduler
policy, and no startup installation or download was added.

## Failures found and corrected

- Native SDK availability used its own `hermes_yaml`/ruamel dependency; assuming
  PyYAML would have introduced an unnecessary dependency and failed discovery.
- Naming a broker wrapper `skill_view` let Hermes' built-in dispatcher bypass the
  resource callback. Namespaced wrappers now preserve the native loader behind the
  parent authorization boundary; the real SDK test asserts both callbacks.
- Import collision prevalidation now rejects case-equivalent members before writing.
- Live qualification found missing `no-store` headers and the old 32 KiB operator
  body limit on portable imports. Skills have their own bounded import envelope;
  unrelated controls keep the original bound. API regression tests cover both.
- Windows exposed a terminal-status/activity race. Completion now publishes the
  fixed safe event code before status can disclose the committed terminal state.
- Real page tasks exposed redundant procedure copies consuming receipt headroom.
  Complete content is retained once; page excerpts/receipts have explicit bounds
  within the unchanged gateway guard. Unbounded MCP receipt reservations were not
  weakened to accommodate the page reader.
- A nullable optional-path schema was rejected by the actual gateway. It was removed;
  both projected host schemas and real SDK-advertised schemas are now checked with
  the gateway-owned validator.

Private deployment logs, prompts, screenshots, credentials and package enablement
state remain outside Git. The qualification record reports counts, hashes and timing
only. Test-created task notifications may be acknowledged; audit records remain.

## Limits retained

OpenClaw runtime translation is future work; preserved metadata and a neutral
provider seam are not runtime support. Hermes Desktop and physical microphone testing
are deferred. Public reading supports bounded public HTML/text excerpts, without
login/browser/PDF execution or paid fallback. P2 waits during realtime voice; physical
model cancellation, throughput scheduling and capacity-release guarantees remain
Phase 13. A disabled or changed skill invalidates earlier copied results/artifacts.


## arXiv page-read follow-up

The reported foreground failures retained no underlying reason, so their exact
network cause cannot be reconstructed. The supplied plain arXiv URL succeeded
through the installed provider during investigation. A separate deterministic bug
was reproduced: a Markdown link whose label was itself a URL was combined with its
destination into an invalid request path. Literal destination parsing now preserves
Markdown targets and balanced URL parentheses, while rejecting multiple URLs and
negative/non-request prefixes. HTTP status, timeout and transport failures now become
fixed content-free diagnostics through the worker, Core callback and activity card.

The follow-up full local gate passed **1,226 / 29 skipped**. Targeted Windows/WSL
qualification passed **67 / 6 platform skips**, including the eight native SDK/WSL
proofs; formatting, lint and strict types passed. The actual HTTPS UI read
`https://arxiv.org/abs/1706.03762` successfully in plain and Markdown form. Exact P0
inspection verified the normalized requested URL, paper title, excerpt and partial
coverage marker, within the unchanged 24,576-unit Core guard. See
[recorded samples](benchmarks/2026-11-04-phase12/arxiv-page-reader.json).
This validates the abstract/HTML excerpt, not a full-PDF read or the original
transient failure's cause. No automatic network retries, extra model or PDF dependency
was added.

## Manual startup investigation — 15 November 2026

The reported reply trace stopped at `gateway_rejected`, which discarded the gateway's
HTTP status and reason. Inspection found no running backend and a clean recovery
journal. The deployment still uses external Ollama attachment. After starting the
installed Ollama app and restarting the test Ember instance, short replies succeeded
through the actual voice UI with Thinking both off and on. The original rejection
response was not retained, so its precise code cannot be recovered retrospectively.

The transport now preserves known bounded rejection codes and notifies the dashboard;
turn cleanup keeps the failure diagnostic instead of replacing it with cancellation.
Unknown response bodies are never echoed, and generation retries remain disabled.
Synthetic regression tests cover both reply modes, safe/unknown/oversized/slow error
responses and observer failures. The full local gate passed 1,241 tests (29 opt-in
skips); actual Windows voice-UI replies passed in both modes. Physical microphone
input was not exercised. The test Ember instance was stopped for operator startup;
the independent Ollama app was left running.
