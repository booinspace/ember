"""Deterministic routing for user-requested deep recall in conversation."""

import re

from .temporal import belief_intent

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


_SOURCE_FOLLOWUP = re.compile(
    r"(?:what(?: was| is|'s|’s) (?:the |your )?(?:original |supporting )?source"
    r"(?: (?:of|for) (?:that|this|that memory|this memory|the memory))?"
    r"|where did (?:i|we) (?:say|write|mention) (?:that|this|it)"
    r"|where did (?:you get (?:that|this|it)|(?:that|this|it) come from)"
    r"|(?:can you )?(?:show|quote) (?:me )?the (?:original|supporting) (?:message|source|passage))"
)


def source_followup(text: str) -> bool:
    """Recognize standalone source questions without browsing the archive."""
    normalized = " ".join(text.casefold().split()).rstrip("?.!").rstrip()
    return _SOURCE_FOLLOWUP.fullmatch(normalized) is not None


def deep_recall_query(text: str, *, natural: bool = False) -> str | None:
    """Return explicit command query or opt-in natural question; preserve temporal wording."""
    if belief_intent(text) is not None:
        return None
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
