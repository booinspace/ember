# Phase 11 acceptance and validation

Status: implemented; the selected PC task worker is now Ubuntu WSL2/bubblewrap
under the operator-authorized harness/dashboard amendment. See the final amendment
evidence below; native Windows records in earlier sections are historical.
Original PLAN P11-AC-01–07 and the broader adoption prerequisite are unchanged.

The original Phase 8A production-adoption prerequisite remains open. An isolated
one-shot task does not qualify Hermes Desktop, messaging/app pairing, dashboard
chat, near-limit context capacity or physical microphone performance. The later amendment explicitly authorizes enabling the selected WSL worker on the
PC; broader adoption remains open. The implementation does not retire any Ember
chat/voice controls or change the live inference backend.

| Criterion | Implementation / observed proof |
| --- | --- |
| P11-AC-01 | Actual pinned native SDK reconnaissance and 1/2/4-round synthetic conversations identify the supported fields, buffered streaming, complete tool IDs and disabled auxiliary paths. The separate installed-software receipt fails closed on code/config/bytecode drift. See the native boundary report. |
| P11-AC-02 | Socket-free HTTPX transport + credential-free stdio broker; strict task-local OpenAI mapping to authenticated P2; unsupported fields/routes and changed profiles fail closed. No secondary runner. |
| P11-AC-03 | Existing native submit/status/events/result/cancel/artifact lifecycle; exact structured report/citation validation; bounded optional personal context. One-shot profile truthfully advertises input/update/resume unsupported. Purge requires worker acknowledgement and process exit. |
| P11-AC-04 | Actual native Windows AppContainer proofs pass: zero capabilities, nine filesystem/network denials, positive own-home/network controls, exact grant/profile/home removal, EOF and forced-controller-crash recovery. These are historical native-token/profile proofs, superseded for the selected PC by the WSL denial/EOF/cancel/crash qualification below. |
| P11-AC-05 | Native task reconciliation/idempotency/effect fences retained. Process-creation/cancel/terminal races tested. A context watcher and per-step/final ABI validation stop revoked copies; no uncertain replay. |
| P11-AC-06 | Actual public SearXNG research through broker + sole gateway passed on Ollama and vLLM, including structured completion, native notification, acknowledged outbox, unchanged canonical files and a distinct follow-up P0 context. |
| P11-AC-07 | Disabled-provider/native task regression plus original core/MCP suites passed. Restored primary HTTPS/UI assets, text availability, Phonon readiness and a synthetic gateway P0 turn passed; physical microphone acceptance remains open. |

## Review and corrections

Independent review found POSIX descendant cleanup, disguised localhost citation
addresses, and permissive bool/int equality in purge receipts. Regressions retain
strict assertions; implementation fixes must pass before acceptance. Native setup
also exposed an unsupported noneditable Hermes installation and a daemon-stdin
shutdown race; the supported external editable install and owned selector relay
replace those failed approaches. These are diagnostic findings, not accepted runs.
Further review caught mismatched callback ID types, unsealed new transport code,
and expensive worker-supplied JSON Schema regexes. The callback protocol uses
strict monotonic integers; all executable boundary files must be sealed; schemas
use a bounded cheap subset and reject regex/reference/combinator features before
gateway dispatch. Successful private recall also needs its own bounded-context
completion regression, in addition to revocation rejection.

Later review fixed failed-wait handling, idempotent orphan cleanup, named-job
collision rejection, missing environment paths and full no-follow source/runtime
inventory. Five private-context regressions verify positive memory-cited completion,
byte limits, search denial and revocation suppression through every delivery surface.

Actual model runs exposed prompt overflow after three searches, then invalid
citations and a fourth search attempt. The 16 KiB guard and all rejection checks
were retained. Supported one-shot SDK settings remove irrelevant guidance; a
2 KiB whole-row search-copy budget bounds new context. Host policy now advertises
remaining tool authority and exact issued citation enums. Both the original SDK
request and projected request are validated; malformed metadata cannot disappear
through pruning. Three subsequent real Ollama tasks passed all original assertions.
Their exact source hashes are retained as historical trials; the final mapper also
preserves its initial server request UUID/deadline across projection.

## Current checks

Final full quality gates on native Windows and macOS each passed **1,060 tests /
18 skipped**, including formatting, lint and strict Pyright (zero errors).
Windows used Python 3.12.15 and the installed LiveKit binary; macOS used Python
3.12.11 with `TMPDIR=/private/tmp` so canonical-storage tests avoid the `/var`
symlink. Frontend TypeScript/Vite build, 21 unit tests and three task/MCP browser
regressions passed. Optional real-model and native boundary tests are run separately
from the default skipped cases; their reports identify the exact tested source.

The refreshed [Ollama gateway benchmark](benchmarks/2026-11-04-phase11-qualification/README.md)
passed all 210 measured warm trials and seven detach/drain/completed-next-P0 probes.
It records the exact model/configuration and measured source hashes. Short-reply
first-visible p95 was 125.3 ms, median decode 72.30 tokens/s, and voice first-PCM
p95 660.1 ms with CPU Phonon Juniper at 1.1x. Voice uses a paced SDK sink; physical
microphone/speaker performance and concurrent inference are not established.

The final native Ollama acceptance run passed in **24.37 s** (task execution
23.344 s), with two P2 model calls, one NAS SearXNG query and two verified citations.
It retains the exact final source hashes in
[the report](benchmarks/2026-11-04-phase11-qualification/hermes-ollama/final.json).
Native SDK CPU proofs retain complete 1/2/4-step tool history; host-projected prompt
costs were 5,454 / 8,459 / 10,614 / 12,411 bytes, below the unchanged 16,384-byte
limit. Oversized Unicode was rejected before dispatch, and no worker, task home,
lease or AppContainer profile folder remained after the native proof.

The refreshed final vLLM pin passed original real Phase 6/7 acceptance, all ten
conformance cases, complete brokered multi-round history, the native Hermes task
and **210 warm trials plus seven drain/recovered-P0 probes**. Its
[configuration, exact hashes and reports](benchmarks/2026-11-04-phase11-qualification/vllm/evidence.json)
are separate from the prior profile. It reuses the existing GGUF through a symlink;
no second weights copy was installed. Ollama remains the production default.

Per-request cleanup passed throughout. Separately, the isolated vLLM gateway did
not exit within 30 seconds of authenticated shutdown; its retained owned Windows
Job was terminated and drained. The runner exited normally, both listeners closed
and GPU usage returned to 239 MiB. The warning-only log does not identify the
precise graceful-exit blocker; [cleanup evidence](benchmarks/2026-11-04-phase11-qualification/vllm/cleanup.json)
records this unresolved operational limitation without calling it graceful cleanup.
Gateway/service lifecycle code is unchanged by this phase.

## Primary app restoration

After commit `3b0a274`, both Windows checkouts were synchronized to the tested
source, `uv sync --locked` passed, and the pinned frontend build passed. Ember
was restarted natively with `start.ps1 -NoBrowser` in the desktop session using
native file-handle stdout/stderr redirection. HTTPS returned 200, both referenced
UI assets loaded, text/tasks were available and CPU Phonon reported Juniper at
1.1x ready. A synthetic P0 gateway turn completed in 3.992 s (first visible text
3.702 s), then no request or queue remained. This was the first post-startup turn,
not a warm-latency comparison or a physical speech test.

At that historical restoration the optional Hermes configuration remained absent,
consistent with the open adoption prerequisite. The one-time test gateway and
speech controllers stopped normally before the primary app started. No real
messaging account or private conversation was used for qualification. See the
[content-free restoration record](benchmarks/2026-11-04-phase11-qualification/primary-restoration.json).

## Native Desktop client and speech-stop amendment — 4 November 2026

The user authorized separate native chat histories sharing Ember's character,
prompts and canonical memory, then explicitly deferred affected-chat deletion to
prioritize integration. The existing pinned Desktop build's delete API removed its
synthetic database rows, but selected renderer text and its sidebar title remained
visible after ten seconds. The [retention canary](benchmarks/2026-11-04-phase11-qualification/desktop-retention-canary.json)
records that failure. Private recall is an explicit deployment opt-in under this
amended policy; automatic removal of copies already retained by Hermes is unfinished.
This does not change the isolated worker's purge contract or Memory ABI schemas.

The new ingress verifies a separate paired loopback credential and required session,
turn and request bindings. It allocates independent bounded Core histories sharing
the existing composition owner. Core applies the actual shared Markdown prompts and
canonical recall. The native extension projects the original user text and exact
delivery echo, without allowing client system/history scaffolding to become prompt
authority. Reconnect after a Core restart returns an explicit expired-chat error;
new native chats receive fresh contexts. Four live chats and 64 turns per chat are
current explicit controller limits, not an unlimited or durable session service.

Explicit search is a host preflight before recall, not a second LLM planning step.
The installed SDK SearXNG provider owns search implementation. The native pre-tool
hook and final HTTP seam require one exact signed host preflight, bind it to the
paired chat/turn/query, and consume its grant before the request. Forged, cross-chat,
changed-query, repeated and malformed Unicode IDs are denied. Plain final model text
cannot create a new search grant. Search results are bounded before JSON parsing and
again when copying complete public rows into the existing input budget.

Independent reviews found and fixed stale cached-response disclosure, certificate-only
NoRecall history leakage, quadratic explicit-query parsing, unsigned native search
execution, and a malformed-ID exception in the SDK's fail-open hook path. Regression
tests cover real canonical memory/forget, late and midstream SSE disclosure, delivery
receipts, reservation draining, exact host-to-plugin grants and native transport bounds.
No test assertions were weakened. The private-memory watcher is scoped to the owning
turn, and response caches revalidate before further disclosure.

Full installed backend gates passed on macOS and native Windows: **1,143 passed /
18 skipped** per platform, Ruff formatting/lint and strict Pyright zero errors.
The final installer receipt-hash and malformed-grant fixes also passed all **54**
affected native regressions; the final local plugin/installer set passed **22**.
Frontend checks passed on both platforms: **25** unit tests, production
TypeScript/Vite build, formatting and **14** browser tests / three explicit optional
live-fixture skips. Existing skipped real-model/platform cases are not counted as
successful runtime evidence.

Actual native-provider smoke returned three public NAS results in **1.922 s**,
copying 918 UTF-8 bytes, with no model call and no temporary search home left behind.
The [native Desktop conformance report](benchmarks/2026-11-04-phase11-qualification/native-desktop-public-conformance.json)
records three visible turns and four exact request envelopes from the installed
b200f5bc Desktop/SDK with fake completions, before the final signed-grant addition.
It is historical request-shape/UI evidence; it does not itself prove the final live
model path. The [normal launch proof](benchmarks/2026-11-04-phase11-qualification/native-desktop-normal-launch.json)
verifies the official MSIX execution alias and separate home/Desktop data without
production DevTools or a debug port.

The [real LiveKit Stop proof](benchmarks/2026-11-04-phase11-qualification/native-live-stop.json)
used the rebuilt UI, installed Edge, real room RPC and CPU Phonon task announcements.
Browser playback detached in **16 ms** after clicking Stop; microphone sender IDs and
peer IDs remained unchanged. A second task notice resumed audio and natural completion
cleared Stop. The test received 30,119 audio bytes and made no model request. The mic
was synthetic and silent; 16 ms is browser detachment timing, not physical acoustic or
native model-abort latency. Ordinary P0 reply stop/drain races are separately covered
by the SDK/backend regression tests.

The primary PC was configured with a backed-up explicit `[hermes_client]` table,
`private_recall = true`, 16 KiB input and installed CPU provider command. Native
`start.ps1 -NoBrowser` restored the application; paired `/models` and HTTPS UI returned
200, and Ember's speech health contract verified `gradium/phonon-31m-en`, Juniper,
1.1x ready. Ollama remains the selected inference backend. A dedicated private native
Hermes profile and **Ember (Hermes)** Windows desktop shortcut preserve existing
Hermes profiles. No gateway credential was copied into the SDK profile.

The final fresh-profile **real native gateway test passed all three turns**: first
marker 3.411 s, same-chat history 1.540 s, and native NAS search plus Core/model
follow-up 6.245 s. It used the normal installed app's authenticated gateway API and
actual model, not fake completions. One native search started and completed; the
terminal outcome was completed, no grant-error marker remained, and the exact
synthetic chat's Core context was released. The [final report](benchmarks/2026-11-04-phase11-qualification/native-desktop-gateway.json)
records exact installed hashes. API completion is proved; physical renderer/headset
validation is left to the user.

Two earlier live search attempts failed closed even though ordinary chat/history
passed. A metadata-only probe confirmed the signed pre-tool hook accepted the call,
but an older native HTTP closure remained active after the SDK reported reload
success. The final extension anchors the shared pre/post/request callbacks and
one-shot grants on the stable SDK plugin owner, with atomic initialization. Tests
cover sequential and concurrent managers plus provider-module eviction. A fresh
native process/profile qualified the final source; hot reload alone is not claimed
as a reliable upgrade procedure.

The final local full suite passed **1,145 / 18 skipped** before the two additional
provider-eviction cases, and the final affected native suite passed **58** tests,
including all **12** extension cases, after that addition. Ruff and strict Pyright
remain clean. The latest staged checkouts and fresh Desktop profile have identical
plugin hash `7221f9542ed7d1af885db28cca34f99bb1b6e76e1dc63bf1d553efa1c93870a7`.
The working private home is `%LOCALAPPDATA%\ember-hermes\desktop-ember-v2-20261104`;
its desktop data is separate. The **Ember (Hermes)** shortcut selects that home.
Earlier canary homes are not the supported launch target.

## Tool-provider cleanup — 4 November 2026

The operator replaced the requested voice-session mirroring with tool delegation
only. No native voice-session bridge, protocol, runtime hook or config remains.
The previously qualified voice search uses Hermes' installed provider; ordinary
Desktop chat still goes through CharacterCore and the sole gateway. Existing
memory, bounded history, played-prefix receipts and Stop contracts are unchanged.

The eight external adapter sources are grouped in `scripts/hermes/`, with their
roles documented there. A tracked repository-root `start-hermes.ps1` selects the
regular named Ember profile through the official native command. Named setup stages
outside the live discovery directory, publishes a complete standalone profile and
never overwrites an existing profile. Tests cover final metadata/receipt, failure
cleanup and preservation of an unowned staging collision.

Final local `scripts/check.py`: formatting, lint and strict Pyright passed;
**1,151 tests passed, 18 skipped** in 90.67 seconds, using Python 3.12.11. Independent
read-only review found no remaining actionable issues after the staging/cleanup
safeguards. Frontend and inference benchmark runs were not repeated because neither
frontend nor generation behavior changed. The [sanitized report](benchmarks/2026-11-04-phase11-qualification/tools-cleanup.json)
records configuration, adapter hashes and exact validation scope.

**Historical cleanup deployment was pending, not accepted.** SSH and ping to the PC timed out, and the
provided NAS hostname stopped resolving from the Mac. This does not establish a PC
crash. No new Windows profile, shortcut, runtime path or service restart was applied.
After connectivity returns, update the ignored `search_command` to
`scripts/hermes/hermes_search.py`, install the named profile, update the shortcut to
the repository launcher, and validate Desktop/profile/search on Windows. Existing
custom profile/chat data remains preserved. The copied-chat purge deferral remains
unchanged.

## Selected WSL worker and live dashboard — 4 November 2026

This is the current deployment record. The operator authorized enabling Hermes,
deferred Desktop, and superseded the native Windows worker after its normal-token
startup failed. **Only the task worker now runs in Ubuntu WSL2/bubblewrap**; Windows
retains Core/voice, canonical memory, broker authority and the existing Ollama gateway.
No model/weights were installed in WSL and no kernel/elevation experiment ships.

The [final evidence and configuration](benchmarks/2026-11-04-harness-dashboard/README.md)
records full Windows/macOS gates, responsive browser tests, actual pinned SDK and
Windows-to-WSL denial/EOF/cancel/crash proofs, real native/MCP/public gateway tasks,
normal-token production delegation, lazy exact input inspection and capture overhead.
The final profile is `43c83dfc25a857c63b11243584f694516a60f616770347246d486f5ac301652a`.
The dedicated Linux dependency import tree is byte-sealed, with changed/added/link
regressions; OS shared libraries remain platform inputs. Windows controller cleanup
is evidence of bounded guest teardown, not a retained Linux handle or GPU release.
Success additionally requires the guest purge frame and verified launcher exit.

Neutral harness/search settings no longer depend on Desktop. Explicit native/MCP
requests return through existing grants, exact approvals, effect fences and receipts;
private copied tool context is revocable on every disclosure surface. Capabilities
separate providers from atomic tools and state configuration honestly. The dashboard
uses generic tool/memory/task/inference events, cached off-loop system sensors and
bounded, lazy, authenticated inspection of the actual visible model request. It adds
no extra generation or durable prompt logging. Original input/output guards remain.

Independent review found the Linux dependency-seal gap and confirmed its correction;
no remaining concrete source issue was reported in the final pass. Failed/stochastic
model trials remain identified in the evidence rather than counted as passes. The
broader Phase 8A gates, physical microphone/device testing, Desktop/purge work and
Phase 13 scheduling/native abort remain open. Phase 12 is replanned toward harness
skill ownership; its original acceptance outcomes remain planned, not complete.

The subsequent [Phase 12 shared-skills amendment](phase-12-runbook.md) supersedes the earlier skills-disabled worker profile. Desktop remains separate and deferred.
