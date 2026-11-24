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


# ponytail: broad cues only decide whether to spend one model call; they don't select a route.
_EVIDENCE_CUE = re.compile(
    r"\b(?:sources?|provenance|cit(?:e|ation)|quot(?:e|ation)|verbatim|original(?:ly)?|"
    r"(?:exact|actual) (?:line|words|wording|message|passage))\b",
    re.I,
)
_REFERENCE_CUE = re.compile(
    r"\b(?:it|that|this|previous|last|earlier|above|memor(?:y|ies)|recall(?:ed)?|"
    r"message|passage|said|wrote|mentioned|told)\b",
    re.I,
)


def source_intent_candidate(text: str) -> bool:
    """Gate one local proposal on short, source-like input, not particular sentence shapes."""
    text = text.strip()
    return (
        0 < len(text.encode()) <= 512
        and len(text.splitlines()) == 1
        and not text.startswith("/")
        and not text.casefold().startswith("recall deeply ")
        and "://" not in text
        and _EVIDENCE_CUE.search(text) is not None
        and _REFERENCE_CUE.search(text) is not None
    )


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
