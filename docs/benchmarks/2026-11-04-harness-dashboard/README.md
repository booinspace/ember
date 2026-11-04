# Harness broker, dashboard and WSL qualification — 4 November 2026

Selected deployment: native Windows Ember with **only the Hermes task worker in
Ubuntu WSL2**, using the already installed pinned SDK/Python 3.14.4 and bubblewrap.
Ollama and the existing Qwen GGUF remain the sole inference backend/model. No model
server or weights were installed in WSL. Desktop is deferred. This record supersedes
the earlier native-worker adoption claim; historical Phase 11 reports remain valid
for their recorded token/profile, not the normal unelevated app.

## Results

- Full macOS quality gate: Ruff formatting/lint, strict Pyright and **1,182 passed /
  28 skipped** in 121.57 s. Full Windows gate: the same checks and **1,180 passed /
  30 skipped** in 244.07 s. Platform/installed-model tests remain explicit opt-ins.
- Frontend: formatting, strict TypeScript, production build, **29 unit tests** and
  **19 browser tests / three optional fixture skips**. Browser coverage includes
  responsive widths, keyboard/reduced-motion behavior, same-request event binding,
  stale-event isolation, lazy inspection/revocation and existing Stop/task/prompt/
  voice controls. The full browser run used macOS Chromium.
- Actual pinned WSL SDK: **four CPU-only proofs**, complete 1/2/4-step history,
  oversized Unicode rejected before generation, and a quote/backslash-heavy MCP
  receipt. Largest projected next input was **13,690 / 16,384 guard units**.
- Windows-to-WSL boundary: **three proofs**, each with 11 filesystem/network denials,
  positive own-home/host-network controls, and an independent Linux survivor check.
  EOF, cancellation and killed Windows-controller cleanup measured **78 / 78 / 63 ms**.
  These prove bounded guest namespace teardown. They do not make a Windows Job a
  Linux process handle or establish native model/GPU release latency. Normal success
  additionally requires the guest's explicit `stopped/purged` frame and launcher exit.
- Actual gateway acceptance: **three cases passed** in 33.20 s: granted native tool,
  synthetic MCP server, and public SearXNG research with verified official citations,
  acknowledged outbox, unchanged synthetic canonical files and a separate P0 follow-up.
  Public task execution was **13.703 s**; follow-up P0 was **266 ms**, first visible
  text **203 ms**. These are individual acceptance observations, not percentile claims.
- The actual normal-token application delegated a native broker task in **7.437 s**;
  its receipt, purge, task/tool events and exact P2 tool history were inspected through
  the authenticated production browser API. Voice services reported ready.

[Validation/configuration](validation.json), [public gateway proof](gateway.json),
[native](native.json), [MCP](mcp.json), [application proof](runtime.json).
Reports contain synthetic IDs, hashes, counts and timing; no personal prompt, token,
archive, deployment TOML or screenshot is committed.

The final [live HTTPS Edge check](browser-live.json) connected actual LiveKit voice,
rendered the public-search activity, inspected public references in the exact P0
input (**9,878 / 24,576 guard units**), and clicked Stop while the complete visible
reply was ahead of active speech. No page error occurred. Windows system certificate
trust was used; TLS verification was not bypassed. The microphone was not exercised.
The screenshot stays private. Synthetic qualification notifications were acknowledged
through the API; their audit records remain.

## Observation overhead

5,000 synthetic 24 KiB captures, no model generation: Windows median **5.4 µs**,
p95 **5.7 µs**; macOS median **5.333 µs**, p95 **6.25 µs**. This measures the capture
hook only. It retains immutable request references; it does not serialize a prompt.
Eight requests / five-minute expiry and 256 coalesced metadata events are fixed
bounds. System sampling runs off-loop, cached three seconds; visible browser polling
uses metadata once per second and pauses in hidden tabs. Exact request serialization
and private-authority checks run only when inspection is requested.

[Windows samples](overhead-windows.json), [macOS samples](overhead-macos.json).
The [previous full gateway benchmark](../2026-11-04-phase11-qualification/README.md)
is retained. The 210-trial suite was not rerun for these observation hooks; this
record makes no new end-to-end voice, physical microphone or concurrent-GPU claim.

## Review, failed trials and remaining limits

Independent review found and fixed observer-failure propagation, stale/expired prompt
disclosure after awaits, copied MCP authority after credential changes, private tool
output escaping to later search/MCP requests, insufficient pre-effect receipt space,
pre-model writes, late metadata moving between chat rows, cursor reset after restart,
and the old Linux seal checking dependency versions without file bytes. Regression
assertions retain the existing validation, exact approval, effect and citation rules.
The final Linux seal includes the dedicated import tree and `pyvenv.cfg`; changed,
added and redirected dependency tests fail closed. OS shared libraries remain platform
inputs. Final review found no remaining concrete source defect.

Native Windows worker startup failed under the normal desktop token; privileged/
window-station/Win32k experiments are excluded from the change. The operator selected
WSL instead. An early WSL public-research trial asked for a fourth search and failed
closed with `unrequested_tool`; the final three-case run passed. Optional model-driven
tasks can still fail validation; there is no silent retry or invented successful result.
Initial Mac sandbox/socket and `/var` temporary-path failures were rerun with appropriate
local test permissions and `TMPDIR=/private/tmp`; no storage guard was weakened.

Production has no MCP servers configured: the catalog says **Unconfigured**; the
synthetic MCP proof does not invent a production service. P2 still waits while a
realtime session reserves the gateway. Hermes Desktop startup, affected-chat deletion,
physical microphone/headset/real-device validation and Phase 13 model abort/scheduling
remain open. Phase 12 is planned as harness-owned skill loading with thin Ember grants,
budgets, manifests and receipts; skills are not enabled by this amendment.
