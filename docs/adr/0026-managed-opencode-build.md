# Root ADR 0026 — Isolated OpenCode Build preview

**Status:** Opt-in synthetic preview; production and private data not approved.
Extends [0025](0025-managed-opencode-preview.md); Inspect remains available.

## Decision

Build is a separate task operation, not an escalation from Inspect. An operator pins
an installed local Linux container image. Each task gets a fresh empty writable
workspace, a fresh pinned OpenCode 2.3.2 server and a fresh model credential. The
container has no host mounts and no network except its own loopback. Ember bridges
only its private OpenCode API and authenticated P2 ingress over owned pipes. OpenCode
chooses edit, shell and test steps; Ember owns admission, deadlines, progress,
cancellation, bounded results and artifact validation. No memory, MCP, skills or
search grant changes. See [Agent ADR 0015](../../ember-agent/docs/adr/0015-managed-opencode-preview.md).

The authenticated Tasks form may grant one explicit service port. Ember probes
HTTP on that port, returns a host-local preview URL only on success, and owns its
stop/expiry. No port grant means no retained process. In Conversation with Thinking
off, start the message with `Build: ` to opt in. Ember forwards that exact current
message, never generated arguments; service exposure requires the Tasks grant.
Build does not import source trees or install dependencies at runtime.

## Setup and rollback

1. Set up Inspect using ADR 0025. Install a trusted local Docker engine in Linux
   container mode (Docker Desktop on Windows or macOS). Build the image using the
   [pinned Dockerfile](../../ember-agent/tools/opencode-build/Dockerfile). Use the
   x64 baseline archive for Miku and arm64 archive for Apple Silicon. Inspect the
   **local** image ID; a tag, registry URL, or remote Docker context is not accepted.
2. In a separate synthetic deployment, add this to the existing configuration:

   ```toml
   [harness.build]
   docker = "/absolute/path/to/docker"
   image = "sha256:<64 lowercase hex digits from docker image inspect>"
   ```

   On Windows use an absolute path to `docker.exe` (TOML single quotes preserve
   backslashes). Restart only an authorized deployment. Check `/api/capabilities`
   for `harness.build`. An unavailable Build does not elevate or disable Inspect.
3. In Tasks select **Build with OpenCode**. For a retained service, grant its
   container port, e.g. `12345`, and ask it to listen on `127.0.0.1:12345`.
   Ember checks `GET /` and returns an ephemeral URL usable on the Ember host only.
   Preview is read-only HTTP, not a production server: no scripts, forms, redirects,
   WebSocket, or external publication. One service at a time, up to 30 minutes.
   Stop it from Tasks. Without a grant, download the validated ZIP and run it yourself.
4. Remove `[harness.build]` and relaunch to roll back Build without disabling Inspect.
   Both task records and ZIPs live under the operational runtime root, not memory.

## Verification and limits

On Apple Silicon Docker Desktop, the real OpenCode 2.3.2 test passed a synthetic
write/run/check loop, ZIP validation, HTTP preview and Stop through a scripted P2
upstream. Run it with `EMBER_TEST_BUILD_IMAGE=<local image ID>` and
`pytest ember-agent/tests/test_opencode_build.py`. The amd64 image passed an offline
binary smoke test under emulation, not a Windows test.

| PLAN gate | Preview evidence | Production status |
| --- | --- | --- |
| P11-AC-01 | Pinned session and tool loop tested with real OpenCode. | Full compatibility matrix not run. |
| P11-AC-02 | Task-bound P2 requests and no external container route verified. | Live Strata selection and no-cloud run not done. |
| P11-AC-03 | Submit, progress, results, ZIP, and Stop tested; browser mocks cover controls. | Full provider contract and personal context not qualified. |
| P11-AC-04 | Nonroot, read-only root, absent Docker socket, and denied egress tested on Mac. | Windows and native Linux isolation not run. |
| P11-AC-05 | Relay shutdown, detached child cleanup, and cancellation-at-commit tested. | Crash recovery is limited; see below. |
| P11-AC-06 | Real write, shell, check, validated ZIP, and verified preview with scripted P2. | Live inference and physical voice not run. |
| P11-AC-07 | Build is default-off and unavailable independently of Inspect. Native and Inspect regressions passed. | Live rollback and character continuity not qualified. |
| P12-AC-01–06 | Skills remain disabled. | Not run for this provider. |

The scoped model bearer grants **task-wide** authority inside the container, not
process identity. After a hard host-process crash, the runtime marks interrupted
tasks uncertain rather than replaying them. The container's independent 35-minute
lifetime and auto-removal backstop cleanup; startup does not immediately reap its
container or any uncommitted ZIP. Such ZIPs cannot be downloaded without a successful
task and matching receipt. No production Phase 11/12 gate is waived.
