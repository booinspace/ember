# ADR 0002: ember project and component names

Status: accepted on 7 October 2026 at the user's request.

The application is named `ember`. Its two independently packaged components are
`ember-agent/` and `ember-memory/`. The main console command is `ember`, also
available through the component alias `ember-agent`. Memory maintenance uses
`ember-memory`. First-party distributions use the `ember-` prefix and Python import
namespaces use `ember_`.

This renames the application without selecting a character identity or changing
component ownership. The runtime profile, canonical-data roots, source namespaces,
evidence fingerprints, and retained sources remain independent of project branding.

New preparation reports use `ember-memory.reconnaissance/0.0.1`, and readers accept
the previous report identifier so saved reports remain accessible. The original
design attachment is preserved verbatim as historical source material; PLAN.md
and current documentation use the new component/command names.

The local checkout path and configured Git origin are deployment details. Push
the renamed code to the existing origin; do not invent a replacement remote URL.

## Current implementation and related records

Naming and CLI aliases are implemented. The report identifier above records the
naming transition; phase 0a now writes version 0.0.2 while retaining legacy reads.
See [phase 0a](0003-phase-zero-reconnaissance.md) and
[compatibility](../compatibility.md). Changing the application name did not change
canonical identities, character selection or component ownership.

This is a workspace naming decision, not another delivery phase.
[The ADR index](README.md) separates it from phase implementation records.
