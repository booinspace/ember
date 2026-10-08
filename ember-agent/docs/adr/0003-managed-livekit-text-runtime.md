# Agent ADR 0003: managed LiveKit Server and a single text-room runtime

Status: implemented for the Phase 3 text slice; real-source claim adoption remains open.
Date: 2026-10-08. Extends [Agent ADR 0002](0002-phase-three-p0-turn-slice.md).
[Root ADR 0006](../../../docs/adr/0006-phase-three-source-and-runtime.md) owns phase status.
[PLAN.md](../../../PLAN.md) governs the implementation and language standards.

## 1. Desired behavior

`ember start --config <deployment>` starts a configured, installed LiveKit Server
and the existing P0 gateway when managed and absent, verifies service credentials
and the gateway model/context, constructs one character, and hosts a text room.
`ember livekit-chat --config <same-deployment>` joins it, streams replies and offers
explicit `/remember KEY TEXT` authoring. Ctrl+C drains the runtime; `/quit` leaves
the participant. Startup never downloads or installs a dependency.

## 2. Ownership and coupling

Agent runtime composition owns service leases, original child-process handles,
private credentials, room creation/dispatch and discovery. It injects the existing
CharacterCore into HostedRoom, which owns an RTC Room and an actual AgentSession.
The participant constructs no memory engine and acquires no SQLite ownership.
The existing engine remains the sole canonical owner; offline import cannot run
alongside this owner. Gateway and independently managed Ollama retain their prior
boundaries. No memory ABI or storage schema change was needed.

Dispatch is explicit to one application-created room, not a general LiveKit
AgentServer fleet. SDK per-job processes would each try to acquire the same
canonical root. This slice intentionally admits one local participant and one
foreground turn. General session routing/concurrency belongs to later integration.

## 3. Existing abstractions

Reuse LocalRuntime, the engine's root lock, AsyncMemoryClient, CharacterCore,
EmberAgent/GatewayLLM, authenticated HttpGatewayClient and the existing P0 service.
SDK text generation is programmatic, so composition explicitly invokes the existing
completed-user-turn RAG hook before calling generate_reply with that same message.
Auxiliary/unprepared generation stays rejected.

## 4. Necessary new primitives

LiveKit service settings add executable/credential paths, RTC ports and bounded
startup/shutdown timeouts. Private room contracts carry binding identities,
explicit input and content-free acknowledgements. API credentials are generated
and persisted locally; short-lived tokens authorize text/data in one room with no
media publication or room administration. An explicit PowerShell setup script
installs release 1.13.9 after checking the published archive SHA-256.

RuntimeLease prevents duplicate launchers. OwnedProcess retains Popen handles,
records component/instance/PID/start/executable identity, and uses a Windows
kill-on-close job or a POSIX process group. Saved process metadata is diagnostic,
not permission to terminate a PID. The gateway adds authenticated graceful shutdown
so an owned child can drain its native request before exit.

## 5. Contracts and invariants

- Managed services start only when their endpoint is unoccupied. Occupied endpoints
  must pass authenticated protocol checks; gateway tag/digest/context must match.
- Attached services and Ollama are never stopped by the launcher.
- Only retained handles from this instance authorize child cleanup; no process-name
  lookup, persisted-PID adoption or shared development API key.
- Signalling/RTC bind locally; managed startup uses IPv4 loopback and plain local ws.
  IPv6/TLS deployments use attach mode.
- One room, fixed agent/user identities and explicit RPC admission. Busy turns and
  the most recent 128 duplicate request IDs are rejected; the client does not retry.
- Typed room inputs are limited to 1,600 characters to fit the SDK RPC payload bound;
  CharacterCore still enforces the conservative model prompt budget.
- The participant receives standard lk.transcription text streams. Readiness waits
  for SDK output routing before accepting prompts; SDK playout completion and
  participant text EOF are checked separately.
- CharacterCore owns bounded dialogue history; redundant SDK chat context is cleared
  after each completed/failed turn. SDK recording and remote session hosting are off.
- Only explicit human remember RPC invokes composition authoring. Prompt text,
  model output and source archives cannot confer mutation authority.

## 6. Failure behavior

Missing executables/credentials, authentication/pin/context mismatch, occupied
incorrect services and readiness timeouts produce safe diagnostics. Partial startup
unwinds owned room/session/memory/services. A detached caller does not release the
owned generation; shutdown drains it. An uncertain forced gateway exit preserves
the existing journal gate and never authorizes stopping Ollama. Invalid/overlapping/
duplicate RPCs fail without another generation. Partial delivery is not acknowledged
as a complete turn. Optional direct `ember chat` remains available independently
of LiveKit. Browser/voice readiness is never advertised.

## 7. Acceptance

Tests exercise generated credentials and limited tokens, duplicate launcher locks,
real Windows child ownership, missing-binary failure, real LiveKit Server/RTC/
AgentSession streaming with synthetic canonical memory reopened after restart,
original provenance, explicit authoring, unauthorized RPC/replay rejection, and
managed gateway/server startup plus graceful owned shutdown. A separate real-room
test uses the pinned actual non-MTP Qwen model through P0 with isolated synthetic
memory; it recalled the remembered color and resolved the original source quote.
Tests never activate imported personal claims. Full quality-gate results are recorded
in PLAN's current Phase 3 runtime record.

## 8. Remaining scope

Complete the operator-reviewed real imported claim extraction/adoption/restart/
recall/provenance gate before declaring all Phase 3 acceptance complete. Saved-memory
inclusion remains unknown. Current recall is the bounded canonical fallback; FTS/
embeddings/vector recall is Phase 5, general extraction Phase 6, and browser/audio/
full deployment composition Phase 8. Sustained MTP/GPU load and live preemption are
not established by this text-room test. Stop for architecture review before Phase 4.

## Missing room discovery diagnostic (2026-10-08)

A participant invoked while only the gateway was running received a generic invalid
local-state error: no runtime room discovery had been published. The participant now
reports that the operator must run ember start with the same configuration and wait
for room readiness. Unreadable/malformed discovery receives distinct safe diagnostics.
The participant still opens no canonical memory, starts no service and never logs
discovery contents or credentials. Missing/malformed discovery regression tests pass:
nine local runtime tests passed, six optional real-service tests skipped; lint,
formatting and strict Pyright passed.
