# Contract and source schemas

Phase 1 supplies experimental generic Memory ABI / Normalized Archive 0.1.0:

- `memory-abi-0.1.0.schema.json`: endpoint definitions catalog under `$defs`.
- `normalized-archive-0.1.0.schema.json`: atomic maintenance batch root.
- `predicate-definitions-0.1.0.schema.json`: independently versioned predicate array.

Generate from the typed models with `python scripts/export_contract_schemas.py`
from the workspace root; `--check` fails on artifact drift. The package conformance
suite checks these artifacts along with semantic validators and API boundaries.
No schema or version declaration establishes a running engine or ABI 1.0 acceptance.
See `../../docs/phase-1-contracts.md` and the compatibility matrix.

`chatgpt-source.provisional.schema.json` records phase 0a provider-format hypotheses.
It is separate from the generic canonical contracts. Actual-export review still
confirms those source shapes/mappings, and the original raw archive remains authoritative.
Bundle, activation-policy and engine-storage schemas await their planned phases.
