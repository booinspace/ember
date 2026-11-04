# 0022 — Shared harness skill catalog

Status: Accepted, 4 November 2026.

Phase 12 uses one portable operational package repository and the selected harness's
native loader. Ember owns neutral metadata/document/bundle contracts, explicit
hash-based enablement, bounded disclosure, tool grants and revocable delivery.
The factory keeps `AgenticTaskProvider` separate from `SkillProvider` and Desktop
client adapters. Hermes implements both through its sealed owned-stdio adapter;
future harnesses can implement those contracts without replacing CharacterCore.

Native Hermes parsing and procedure/resource loading run only in the installed
worker environment. Namespaced broker wrappers avoid SDK built-in dispatch bypass.
Original provider extensions round-trip; unsupported semantics remain unavailable.
No framework is vendored, no independent skill executor is added, and startup does
not install dependencies or reseal changed code.

The shared catalog exposes SDK bundled/optional and shared packages, while enabled
metadata alone enters delegation. Whole procedures/resources require current
approval and fit the existing serialized context guard. Revocation invalidates
copied results and artifacts. Tools still pass through the native/MCP broker and
existing approvals; skills cannot create authority or canonical memory.

A bounded installed public-page adapter serves both explicit foreground URL requests
and delegated broker reads. Desktop remains deferred. OpenClaw's runtime, skill
learning, installers, remote registries and Phase 13 scheduling are outside this
change. See [runbook](../phase-12-runbook.md) and [validation](../phase-12-validation.md).
