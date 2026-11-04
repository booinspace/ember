# 0012 — Harness-owned skills, Ember-owned authority

Status: Accepted, 4 November 2026.

Adopt [root ADR 0022](../../../docs/adr/0022-shared-harness-skills.md).
`Skills` caches metadata only; the selected `SkillProvider` validates and loads
packages outside Core. Conversation delegates through the existing neutral task
operation; no SDK import, shell, dependency resolver or harness registry is added
to CharacterCore. Desktop adapters remain separate.

The dashboard and CLI use the existing authenticated owner. Enablement is an exact
package-hash/revision compare-and-set. Complete selected procedure/resource bytes
are checked at the parent broker before native SDK loading; final gateway admission
uses unchanged guards. Current package authority fences all task delivery surfaces.
The literal public-URL foreground path shares the installed reader and exposes its
partial coverage without a second model or memory-driven public request.

[Acceptance evidence](../../../docs/phase-12-validation.md) includes native SDK
procedure/resource loading, isolated-worker failure handling, gateway tasks and
portable data boundaries. OpenClaw metadata round-trip is not runtime support.
