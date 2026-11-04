# Phase 10 acceptance and validation

Configuration: official `mcp==2.3.0`, Python 3.12, native/direct MCP with no harness.
No external service/grant is enabled by default. The supported profile is explicit
operator invocation of installed trusted atomic/stateless services. Services retain
their own OS authority; this phase does not claim an OS sandbox or conversational
MCP planning. See [runbook](phase-10-runbook.md) and [Root ADR 0019](adr/0019-direct-mcp.md).

## Original acceptance criteria

| Criterion | Implementation and meaningful regression evidence |
| --- | --- |
| P10-AC-01 discovery | Actual SDK stdio and streamable HTTP fixtures; normalized server-bound identities, schemas, annotations and requirements; pagination, duplicate identity, invalid metadata, unsupported schemas, server failures and refresh. Explicit grants alone authorize calls. |
| P10-AC-02 scope/effects | Bound character/session, deadlines, argument/schema checks, separate credential references, metadata/credential identity and exact approval fence. Real calls cover success, failure, timeout, pre-RPC credential/metadata rotation and dispatched uncertain writes. |
| P10-AC-03 native isolation | Actual fresh SDK clients and owned stdio processes without a harness; explicit environment and temporary cwd; separate actor IDs and credentials. Memory ABI has no SDK dependency. |
| P10-AC-04 sampling | Real malicious fixture attempts sampling with no callback; fails without model fallback. Unsupported extensions fail closed; server output never becomes runtime authority. |
| P10-AC-05 availability/recovery | Actual hung discovery/invocation cancellation, bounded wire/output/schema validation, owned process shutdown, exact argument approvals and uncertain fence across restart. Independent tools/native conversation retain their existing owners. |
| P10-AC-06 operator UI | Authenticated origin-checked discovery/submission/receipt APIs; plain text descriptions/results; availability, requirements, bound readable approval, retained progress/cancel and terminal receipts. Browser regression covers malicious markup, retry idempotency, cancellation and uncertain outcome. Cross-character injection is rejected by real API/task tests. |

No acceptance criterion was rewritten to excuse a failed test. The constraints
above define the shipped service/interaction profile, not arbitrary MCP programs.
Future harness adapters still need their separately specified isolation.

## Independent review and corrections

A separate agent read the owning runtime, original PLAN criteria and SDK fixtures.
The review found and the implementation corrected:

- Lost UI task handles prevented progress/cancellation; the tool panel now retains
  the handle and polls the existing task owner.
- Deployment and adapter server limits differed; both now enforce eight.
- Refresh/discovery was not owned by shutdown; shutdown now cancels and drains it,
  prevents later ready snapshots and checks actual child termination.
- Server regex schemas could block the voice loop; a bounded cheap schema profile
  rejects regex/references/combinators before argument validation.
- Operation/payload mismatch could produce an internal error; input validation
  returns a sanitized client error before submission.
- Known pre-RPC failure could claim an unknown write; trusted dispatch evidence
  now records `not_dispatched`, while dispatched cancellation stays uncertain.
- Approval IDs were opaque; readable server/tool/effect and exact arguments are
  displayed, stale identity approval is disabled and revalidated server-side.
- Compressed HTTP responses could expand beyond the wire limit; compression and
  redirects are rejected and covered by an actual HTTP fixture.
- Native Windows lifetime testing exposed incomplete cancellation cleanup. The
  complete finalizer is owned through repeated cancellation, pipes close before
  reaping, and Windows retains job-member handles through signalled teardown.
  The unchanged immediate child-PID regression passed five consecutive native
  runs; real stdout-flood, spawn-cancel and double-cancel regressions also remain.

The reviewer also checked native handle ownership: thread identity must be checked
on the retained handle before resuming a suspended owned process. This prevents
thread-ID reuse from granting authority over an unrelated process.

## Quality gates and measurements

Settled checks: macOS full suite **785 passed / 12 skipped** (797 collected);
Windows full gate **788 passed / 7 skipped** (795 collected), including Ruff
format/check and strict Pyright. The final two fail-closed native error cases were
then added; the final complete MCP/task/ownership subset passed **24/24** on
Windows, with Ruff and strict Pyright clean again. Frontend
Prettier, TypeScript/Vite build, **21 unit tests** and both MCP/native-approval
browser regressions pass. Platform/live-model skips are not claimed as passes.
The full synthetic gateway suite below supplies separate real inference/voice
evidence. The independent reviewer reran **22 SDK/task + 2 ownership-error** tests
and found no remaining actionable Phase 10 blocker. It did not run Windows itself.

Earlier diagnostic run: macOS 778 passed / 12 skipped; Windows 782
passed / 7 skipped / one process-cleanup failure, which required an implementation
fix before acceptance. Those intermediate counts are not final acceptance.

The full seven-case gateway suite uses 30 warm measured samples per case, an
excluded warmup and a cancellation/drain/next-P0 probe. Short/medium/long input and
P2/P3/P4 queued cases use 64 output tokens; voice uses CPU Phonon Juniper at 1.1x
and a paced SDK PCM sink. No private messages, memory, generated audio or hidden
reasoning are recorded. Physical audio/network/STT, true concurrent model execution
and native abort latency are outside these synthetic measurements. Results retain
failed trials and provenance; they do not establish near-128K throughput.

## Recorded gateway configuration and results (2026-11-04)

[Raw reports](benchmarks/2026-11-04-phase10-ollama/) contain 30 successful measured
trials plus one successful consumer-detach/drain probe in each of seven cases:
210/210 warm trials and 7/7 cancellation probes completed. The excluded warmup
is retained. Session identifiers are replaced with counts; each report includes
its unsanitized synthetic source SHA-256 for provenance.

Windows 11 build 26200, Python 3.12.15, RTX 3090 24,576 MiB, driver 616.92,
Ollama 0.40.14; pinned `qwen3.8-rvn:27b-iq3_s-mtp` manifest digest
`5b869f160df08fa13860487d8c341d3db22a9b3aca48e67dced4b53370a3cfd2`,
131,072 context tokens, 64 output-token cap, gateway revision `a122be4`.
This is the existing sole production gateway; Phase 10 leaves model generation
unchanged. The suite driver ran from the Phase 10 test checkout. MCP services
were not active during these isolated inference measurements.

| Case | First visible p95 ms | Turn p95 ms | Decode median tokens/s | Peak used MiB | Detach to released ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| p0-long | 116.6 | 806.2 | 81.57 | 23925 | 664.1 |
| p0-medium | 109.1 | 794.3 | 78.28 | 23925 | 680.6 |
| p0-short | 106.8 | 785.3 | 73.19 | 23903 | 570.6 |
| p2-queued | 112.9 | 788.0 | 71.36 | 23925 | 761.6 |
| p3-queued | 106.7 | 848.2 | 71.45 | 23925 | 544.8 |
| p4-queued | 106.7 | 816.1 | 71.46 | 23925 | 635.3 |
| voice-short | 417.4 | 16589.5 | 70.40 | 23925 | 410.5 |

Voice turn completion includes paced PCM playback, not just text decode. Its first
visible timing also includes the speech-path setup; compare like paths only.
Peak free VRAM is very small (approximately 398 MiB reported during the steady
loaded profile), so a different runner needs an actual allocation/load test.
Cancellation measurements include draining native generation; they do not imply
GPU preemption. The sink-clear measurement is not physical speaker silence.
These reports establish the matched Ollama baseline for the subsequent vLLM task,
not a speed guarantee for another model/context/runner.


The deployment label `primary-non-mtp` does not establish MTP inactivity. Ollama
metadata includes `draft_num_predict=2`, built-in renderer `qwen3.8`, parser
`qwen3.5` and stop strings `<|im_start|>` / `<|im_end|>`. Actual draft acceptance
was not instrumented. A vLLM run using the verified embedded/HF template may
still differ from Ollama's built-in renderer; such a comparison must name that
limitation rather than claim identical prompt bytes or MTP settings.
