"""Run the PLAN.md quality gate using installed tools, without package downloads."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Run formatting, lint, strict types, and pytest; stop at the first failed check.

    Invoke with the root development environment's Python after uv sync --locked.
    Subprocesses are owned, synchronous, and run against the same project root.
    """
    if sys.version_info[:2] != (3, 12):
        print("Use Python 3.12 for the supported stack.", file=sys.stderr)
        return 2
    checks = (
        ("ruff", "format", "--check", "."),
        ("ruff", "check", "."),
        ("pyright",),
        ("pytest",),
    )
    for arguments in checks:
        print("Running: " + " ".join(arguments), flush=True)
        result = subprocess.run([sys.executable, "-m", *arguments], cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
