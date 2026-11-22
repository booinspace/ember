"""Deterministic routing for user-requested deep recall in conversation."""

import re

# ponytail: recognize leading, specific recall questions; expand with tested transcript examples.
_NATURAL_V1 = re.compile(
    r"(?:(?:hey[, ]+)?ember[, ]+)?"
    r"(?:"
    r"(?:(?:do|can|could) you )?(?:remember|recall) (?:when|what|how|where|why|who|which) "
    r"|(?:do|can|could) you (?:remember|recall) (?:(?:anything )?about|the|our|my|your) "
    r"|what do you (?:remember|recall) about "
    r")(?P<topic>.+)",
    re.IGNORECASE,
)


def deep_recall_query(text: str, *, natural: bool = False) -> str | None:
    """Return explicit command query or opt-in natural question; preserve temporal wording."""
    stripped = text.strip()
    for prefix in ("/recall ", "recall deeply "):
        if stripped.casefold().startswith(prefix):
            return stripped[len(prefix) :].strip()
    if not natural or len(stripped) > 8192:
        return None
    normalized = " ".join(stripped.split())
    match = _NATURAL_V1.fullmatch(normalized)
    if match is None:
        return None
    topic = match["topic"].rstrip("?.!,;:").strip().casefold()
    if len(topic) < 2 or topic in {"it", "that", "this", "anything", "something", "everything"}:
        return None
    return stripped
