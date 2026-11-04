# Phase 11 acceptance and validation

Status: implemented and qualified for the selected native Windows one-shot profile.
Original PLAN P11-AC-01–07 and the broader adoption prerequisite are unchanged.

The original Phase 8A production-adoption prerequisite remains open. An isolated
one-shot task does not qualify Hermes Desktop, messaging/app pairing, dashboard
chat, near-limit context capacity or physical microphone performance. Delegation
stays default-off; the implementation and isolated tests do not retire any Ember
chat/voice controls or change the live inference backend.

| Criterion | Implementation / observed proof |
| --- | --- |
| P11-AC-01 | Actual pinned native SDK reconnaissance and 1/2/4-round synthetic conversations identify the supported fields, buffered streaming, complete tool IDs and disabled auxiliary paths. The separate installed-software receipt fails closed on code/config/bytecode drift. See the native boundary report. |
| P11-AC-02 | Socket-free HTTPX transport + credential-free stdio broker; strict task-local OpenAI mapping to authenticated P2; unsupported fields/routes and changed profiles fail closed. No secondary runner. |
| P11-AC-03 | Existing native submit/status/events/result/cancel/artifact lifecycle; exact structured report/citation validation; bounded optional personal context. One-shot profile truthfully advertises input/update/resume unsupported. Purge requires worker acknowledgement and process exit. |
| P11-AC-04 | Actual native Windows AppContainer proofs pass: zero capabilities, nine filesystem/network denials, positive own-home/network controls, exact grant/profile/home removal, EOF and forced-controller-crash recovery. Linux reconnaissance is separate evidence. |
| P11-AC-05 | Native task reconciliation/idempotency/effect fences retained. Process-creation/cancel/terminal races tested. A context watcher and per-step/final ABI validation stop revoked copies; no uncertain replay. |
| P11-AC-06 | Actual public SearXNG research through broker + sole gateway passed on Ollama and vLLM, including structured completion, native notification, acknowledged outbox, unchanged canonical files and a distinct follow-up P0 context. |
| P11-AC-07 | Disabled-provider/native task regression plus original core/MCP suites passed. Native post-qualification normal conversation and voice readiness are checked when restoring the primary app; physical microphone acceptance remains open. |

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
