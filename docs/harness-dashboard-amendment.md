# Harness capabilities and live dashboard amendment

Scope: finish Phase 11's tool broker integration and expose the existing application
as an operator dashboard. Hermes Desktop pairing is deferred; retain its adapter
without using it as the voice search or task-provider setting. Phase 12 is evaluated
and replanned here, not declared implemented. Phase 13 scheduling stays conservative.

## Behavior and ownership

- A neutral capability catalog lists native operations, explicitly granted MCP tools,
  public search and the selected harness, including unconfigured/unavailable reasons.
- Reuse AgenticTaskProvider/TaskRouter and ToolProvider/ToolRouter. One selected
  harness is enough; Hermes-specific SDK/process compatibility stays in its adapter.
- A bounded task-time tool catalog is passed to Hermes. Its requests return through
  Ember's existing argument/grant/context/approval/receipt machinery. Credentials,
  canonical memory and inference priority never enter the worker.
- Neutral public-search configuration works without Desktop client configuration.
  Legacy Hermes configuration remains accepted. Conflicting declarations fail clearly.
- Typed, bounded, in-memory activity events represent tool calls, recall, tasks and
  inference. The dashboard renders inline transactions in conversation and a live
  activity view. No per-token event writes, durable prompt logs or extra model calls.
- Prompt inspection shows the actual visible messages, tool schemas and options at
  the model-client boundary. It is authenticated, fetched on demand, bounded and
  revalidates recalled-context authority. Hidden reasoning and credentials are excluded.
- CPU/RAM/process/GPU and gateway metrics are cached, sampled off-loop, and shown
  honestly as unavailable where unsupported. Monitoring cannot gate generation.
- Keep LiveKit, Stop, read-ahead, played-prefix receipts, prompt editing and source
  controls. Replace the personal-example composer hint with neutral product copy.

## Required changes

Callers: deployment parsing, composition/startup, foreground task registration,
Hermes adapter/worker protocol, MCP task invocation, Core request/recall observation,
browser API, task/tool UI and shared frontend contracts/styles. Existing contracts
remain compatible. Tests cover the new boundaries rather than mirroring implementation.
Fixtures: synthetic SDK worker, MCP service and browser control fixtures. Deployment:
reseal changed external worker assets, back up private TOML, enable Hermes and public
search, then restart the owned runtime. No canonical schema or memory migration.

## Acceptance

1. Legacy settings work; neutral harness/search settings work without Desktop.
2. Capability status matches live configuration, grants and runtime availability.
3. A worker can call granted native/MCP tools; denied/stale/altered requests fail.
   Writes use the same exact approval and unknown-effect fencing as direct MCP.
4. Public search and native/MCP I/O produce correlated inline activity during voice.
   P2 model steps remain visibly waiting during realtime reservations.
5. Prompt inspection includes the full actual selected input and tools, not a
   reconstructed approximation; revocation/turn replacement/teardown remove access.
6. Dashboard works on desktop/mobile, supports keyboard and reduced motion, preserves
   conversation controls and displays cached system/queue/context measurements.
7. Measure observation overhead without extra generation, run appropriate Python,
   frontend and browser gates, and qualify actual Hermes public/native/MCP tasks through the selected WSL worker.

## Phase 12 direction

Delegate skill discovery, progressive instruction/resource loading and execution
planning to the selected harness. Ember owns a small capability/requirement view,
operator enablement and transaction presentation. Skill text never grants tools,
changes memory/personality policy, bypasses the gateway, or installs executables.
Provider-local skills may be imported/translated by that provider; do not build a
second general SKILL.md runtime in CharacterCore. A future second provider must pass
the same broker, budget, cancellation and authorization tests.

The pinned Hermes worker currently disables skills. Read-only skill selection and
approved resource mounts need separate qualification before enabling them. Automatic
skill creation/installation and autonomous memory learning are not enabled here.
Official reference: https://hermes-agent.nousresearch.com/docs/guides/work-with-skills

## Selected task-worker platform

The operator superseded the native Windows launcher choice with Ubuntu WSL2.
Reuse the installed pinned SDK/Python 3.14 and Linux bubblewrap boundary, not another
runner or model server. The gateway/task broker still lives in Windows and owns
all grants, exact approvals, credentials, model admission and canonical authority.
Host-to-WSL EOF, cancellation and forced Windows-controller death must remove the
Linux task namespace/context before this deployment is accepted. The Windows
adapter remains available for separately qualified deployments; kernel/station
experiments are excluded. Hermes Desktop remains deferred.
