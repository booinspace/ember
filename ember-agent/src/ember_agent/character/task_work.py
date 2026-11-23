"""Small, read-only projection of committed conversational work."""

import re
from dataclasses import dataclass
from uuid import UUID


class WorkUnavailable(Exception):
    """Operational receipts could not be read or validated within the lookup budget."""


class AmbiguousWork(Exception):
    """More than one task matches this short ID."""


@dataclass(frozen=True)
class WorkDigest:
    sequence: int
    task_id: UUID
    operation: str
    state: str
    summary: str


def lookup_prefix(text: str) -> str | None:
    """Empty prefix selects recent work; explicit short or full IDs select a task."""
    query = text.strip().casefold().rstrip("?.!")
    if query in {
        "recent work",
        "last task",
        "what did you just do",
        "what did you build",
        "what did you just build",
        "what was the result",
    }:
        return ""
    match = re.fullmatch(
        r"(?:task|work) ([a-f0-9]{8}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})(?:\s*:\s*.+)?",
        query,
    )
    return match[1] if match else None
