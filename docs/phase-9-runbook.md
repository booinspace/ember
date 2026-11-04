# Phase 9 task runtime trial

Use a synthetic deployment with its own data and runtime roots. Build the
existing browser frontend and start Ember with that deployment. The Tasks
panel appears when the operational store can start. Normal voice and text
remain available if task storage fails during startup.

Count words and characters in Tasks, or send `/task <text>` or
`Count the words in <text>` in a connected conversation. Ember acknowledges
the queued operation and shows the result in Tasks. Summarization needs an
agent-enabled gateway and an unreserved runner. Disconnect conversation
before queuing a summary. Deep Recall in Tasks uses the existing bounded
service. Open its result from Tasks before its in-memory receipt expires.

Dismiss acknowledges the terminal notification. Save result explicitly
publishes a non-recall result as a private text artifact under the runtime
root. Cancel records a request for active work. Wait for the terminal state
to see the outcome. An interrupted active task becomes uncertain after a
restart and is not replayed automatically.

The managed gateway provisions a separate agent token under the runtime
root. Keep all gateway credentials private. Results and tool receipts stay
in the operational database. The canonical memory root is independent.

See [Root ADR 0017](adr/0017-phase-nine-operational-tasks.md) for the
acceptance matrix and the scope of hardware checks.

In conversation, the model can also select an installed task route for a
request to count, summarize, or investigate. It queues one operation using
text from the current message. Summary requires the agent gateway.

Choose Review a draft to exercise confirmation. Tasks shows the exact draft;
approval binds its action hash and publishes only a private managed text artifact.
Reject, cancel or let the action expire to prevent publication. Uncertain effects
are not retried. Confirmations must be decided in the authenticated Tasks panel.

A completed task may announce a generic notice during an idle turn in its original
voice session. Pending confirmations, another turn, preparation/generation and
playback defer it to the UI. Interrupted or crash-uncertain notices do not replay.
Dismiss acknowledges the UI item independently. Task results never enter memory
automatically; revocation invalidates dependent result and artifact disclosure.

## Gateway inference benchmark

Use the installed development environment. Run against one explicitly selected,
idle gateway with synthetic data. Startup/downloads and direct generation against
the runner are not part of this command. Each output filename must be new.

```powershell
.venv\Scripts\python.exe -m ember_agent.inference.benchmark `
  --config .local/config/benchmark.toml `
  --output .local/benchmarks/warm.json --samples 30 `
  --fixture-repetitions 32 --output-tokens 128
```

Add `--queued-workload P2`, `P3` or `P4` for a matched reservation/contention run.
It uses that class's existing credential, proves the request queues, and removes
its subscription before releasing its own reservation. It never executes a second
upstream alongside voice. Run each class separately, compare identical fixtures,
and retain failed trials. Queued admission that expires fails the benchmark.

Add `--voice` to send streamed gateway text through configured TTS into a paced
SDK PCM sink. This measures first decoded speech, software sink interruption and
next-P0 latency after observed gateway drain. It excludes microphone/STT, LiveKit
network transport, browser/device buffering and physical speaker silence. Physical
barge-in and native GPU release remain separate Phase 13 qualification. No speech,
prompts, replies or hidden reasoning are saved in the report.

The default refuses existing realtime reservations. `--allow-reserved` is only for
an explicitly scheduled operator test on an idle primary gateway: it can compete
with a new user turn, so do not use it during conversation. The tool adds and
removes only its own reservation; it never clears other sessions or uncertain
journals. A failed/unknown release is not silently recovered by restarting a server.

Reports include raw samples/failures, first visible text from client submission,
provider decode tokens/s, queue/prefill/load usage when supplied, total request or
paced playout time, warmup separately, environment/model/context identity and
sampled dedicated GPU memory. Missing metrics stay null; chunks are never counted
as tokens. The first speech warmup is excluded from voice aggregates. GPU polling
runs at approximately 250 ms plus query overhead and may miss short peaks; shared
VRAM/spill and physical audio require additional platform observations. Do not
interpret a ready gateway slot as independent proof that CUDA kernels aborted.

Start with 30 warm samples; use at least 100 per finalist case for p95 acceptance.
Separate fresh-load trials, cache hits/misses, contexts, prompt lengths, MTP and
runner versions. The command does not evict/load models to manufacture cold starts.
Publish no private runtime reports without sanitizing operational identifiers.
See [the evaluation methodology](inference-backend-evaluation.md) for future
multi-runner qualification and [Phase 9 validation](phase-9-validation.md) for
recorded results. vLLM remains disabled until Phase 13 acceptance.
