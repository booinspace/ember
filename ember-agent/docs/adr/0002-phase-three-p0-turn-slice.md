# Agent ADR 0002: shared character turn core and the initial P0 gateway

Status: phase 3 P0/turn implementation covered by the current full gate; the
source-backed claim/adoption path and full voice-room delivery remain open.
Date: 2026-10-07. Extends Agent ADR 0001.

[PLAN.md](../../../PLAN.md) remains authoritative.
[Root ADR 0006](../../../docs/adr/0006-phase-three-source-and-runtime.md) records
phase 3 delivery and its remaining review gate; [Memory ADR 0004](../../../ember-memory/docs/adr/0004-source-import-and-authoring.md)
owns source parsing/replay and human authoring.

## Ownership and existing implementation

Local composition constructs the canonical engine, binds a stable character and
independent personality, and injects AsyncMemoryClient and ModelClient. The text
path and LiveKit completed-user-turn hook share CharacterCore. Recalled payloads
are untrusted observations in prompt context; source files and model output do not
provide mutation authority. Explicit in-session human entry stays in composition.

The local foreground gateway owns one active native Ollama request, one pinned
tag/digest and an explicit context. HTTP clients use local credentials and versioned
request/context IDs with ordered visible events. Only P0 is accepted. Complete
JSON/tools are validated before publication; tool results are not execution.
Reasoning fields/tags are excluded. There is no direct generation fallback.

Prompt composition uses a conservative serialized UTF-8/framing guard with a 7,000
input ceiling and at least 1,192 output reserve in the 8,192 context. Preserve the
trusted instructions/current user input; trim lower-ranked memories and whole older
turns. Latest revision/lifecycle checks prevent forgotten or changed dependencies
from entering subsequent prompt/history delivery. Already handed-off output cannot
be retracted and full upstream/context purge remains an application-integration gate.

The current fallback asks for a bounded canonical set and ranks keyword overlap,
because phase 2 search is literal. It is limited recall, not phase 5 lexical/vector
retrieval. Keep the capability branch through the ABI so available indexed retrieval
can replace that fallback without introducing importer or engine coupling.

## Failure and lifecycle

Caller disconnection detaches delivery while the gateway drains its owned native
request. HTTP cancellation is not runner-release proof. Failed/incomplete completion
leaves admission blocked; a content-free durable running/uncertain marker survives
restart and requires verified operator quiescence. The gateway has a local OS lock,
bounded subscriber/journal work and no authority to stop independent Ollama.

LiveKit consumes a single-use authorization prepared by the completed-user-turn
hook and disables automatic generation retry/auxiliary reply. The SDK's stream/task
lifetime is owned and prompt/output content capture is disabled by the adapter.
Native history currently describes visible downstream text handoff; it does not
prove voice playback or the full phase 8 delivery lifecycle.

## Validation and remaining gates

The current repository gate passes: formatting, Ruff, strict Pyright, 294 tests
passed and one opt-in real-model test skipped. Earlier short actual MTP/non-MTP
text/JSON/tool probes and a synthetic remembered fact after restart through the
LiveKit adapter remain smoke evidence, not sustained speed, peak VRAM/KV,
full voice-room, real-export-fact or preemption acceptance. Full LiveKit Server
voice delivery was not exercised. The official ZIP import is owned by Memory ADR
0004; no personal claim has yet been adopted into this runtime.

After operator review of exact source evidence, finish the narrow real-claim
confirmation/restart/recall path through the injected client and P0 runtime. Retain future
P3/P4, P1/P2 and measured preemption in this same gateway at their planned phases.
`ember start`, complete browser/audio startup and runtime control remain phase 8.

Extended on 2026-10-08 by [Agent ADR 0003](0003-managed-livekit-text-runtime.md):
real text-room hosting, managed LiveKit/gateway startup and terminal participant
are implemented. The former reservation of all `ember start` behavior is superseded
for this Phase 3 text slice; browser/audio/full deployment integration stays Phase 8.
