"""Generate or check the versioned schemas from the typed ABI contract registry."""

import argparse
import json
from pathlib import Path

from ember_memory_abi.schemas import contract_schemas

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Write deterministic UTF-8 schema artifacts, or fail on stale artifacts with --check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "ember-memory/schemas")
    arguments = parser.parse_args()
    output: Path = arguments.output_dir
    check: bool = arguments.check
    if not check:
        output.mkdir(parents=True, exist_ok=True)
    stale = False
    for name, schema in contract_schemas().items():
        payload = (json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        target = output / name
        if check:
            if not target.is_file() or target.read_bytes() != payload:
                print(f"Schema artifact needs regeneration: {name}")
                stale = True
        else:
            target.write_bytes(payload)
    return int(stale)


if __name__ == "__main__":
    raise SystemExit(main())
