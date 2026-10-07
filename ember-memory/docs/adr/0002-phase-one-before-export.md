# ADR: experimental ABI core before the personal export

Status: accepted, 2026-10-07, following the user's authorization to continue phase 1.

The export is pending, but canonical identity, exact evidence, temporal axes,
serialization, predicates, ownership and typed client boundaries are specified in
PLAN.md independently of provider format. Implement the generic Memory ABI and
Normalized Archive as experimental `0.1.0` contracts now, with synthetic conformance
coverage and reproducible schemas.

Provider-specific source models/crosswalk mappings remain separate. Phase 0b must
still inspect the real export and reconcile source-dependent assumptions. Unknown
readable values/extensions do not acquire automatic write or activation semantics.
Phase 2 may implement the canonical engine against these generic contracts while
preserving migration/version discipline. No historical data is replaced or invented.

ABI 1.0 requires the real-source, character/gateway and backup/restore acceptance
in PLAN.md. Neither the 0.1 package version nor schema validation establishes that
acceptance, a running backend, approved knowledge, or correct model behavior.
