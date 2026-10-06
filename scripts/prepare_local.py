"""Create empty local staging roots only. Does not allocate store IDs or databases."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Create empty ignored staging roots without stores, identities, or runtime state."""
    local = ROOT / ".local"
    for relative in (
        "incoming",
        "reports",
        "fixtures",
        "data/libraries",
        "data/characters",
        "runtime/tasks",
        "runtime/artifacts",
        "backups",
    ):
        (local / relative).mkdir(parents=True, exist_ok=True)
    print("Local staging directories prepared. No canonical stores or character IDs created.")


if __name__ == "__main__":
    main()
