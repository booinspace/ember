"""Recognize explicit Build commands without giving a model admission authority."""

import re

_SOFTWARE_BUILD = re.compile(
    r"^(?:(?:please|could you|can you|would you)\s+)*"
    r"(?:build|create|implement|make|write)\s+(?:me\s+)?(?:an?|the|my)\s+"
    r"(?:[\w-]+\s+){0,5}(?:web\s*app(?:lication)?|website|"
    r"(?:desktop|mobile|android|ios)\s+app(?:lication)?|command-line tool|cli)\b",
    re.IGNORECASE,
)
_REFERENTIAL_BUILD = re.compile(
    r"^(?:(?:please|could you|can you|would you)\s+)*"
    r"(?:build|implement)\s+(?:it|this|that)\b",
    re.IGNORECASE,
)


_HISTORY_REFERENCE = re.compile(
    r"\b(?:we discussed|we talked about|we planned|from (?:before|earlier|above)|"
    r"(?:as|like) (?:discussed|before|above)|you (?:described|suggested|mentioned))\b",
    re.IGNORECASE,
)


def build_goal(text: str) -> str | None:
    """Return a standalone goal, empty for a request that needs one, or no match."""
    value = text.strip()
    if value[:6].casefold() == "build:":
        goal = value[6:].strip()
        return "" if _HISTORY_REFERENCE.search(goal) or _REFERENTIAL_BUILD.match(goal) else goal
    if _SOFTWARE_BUILD.match(value):
        return "" if _HISTORY_REFERENCE.search(value) else value
    if _REFERENTIAL_BUILD.match(value) or (
        value.lower().startswith(("build the app", "build the application"))
        and _HISTORY_REFERENCE.search(value)
    ):
        return ""
    return None
