"""Literal word relevance for extracted values, separate from transport and index labels."""

import re
import sqlite3
import unicodedata

from ember_memory_abi.memory import EpisodePayload, FactPayload, GoalPayload, Memory, PlanPayload

# Both queries use the same canonical revision alias. FTS still supplies candidates;
# this expression only ranks their extracted values, never evidence or source text.
EXTRACTED_TEXT_SQL = (
    "CASE WHEN json_extract(r.payload_json,'$.content.payload.value.type')='boolean' "
    "THEN CASE WHEN json_extract(r.payload_json,'$.content.payload.value.value') "
    "THEN 'true' ELSE 'false' END ELSE "
    "COALESCE(json_extract(r.payload_json,'$.content.payload.value.value'),"
    "json_extract(r.payload_json,'$.content.payload.event'),"
    "COALESCE(json_extract(r.payload_json,'$.content.payload.description'),'') || ' ' || "
    "COALESCE(json_extract(r.payload_json,'$.content.payload.steps'),'')) END"
)
WORD_RANKER = "ember.rrf-extracted-words@0.2.0"
_FILLER = frozenset(
    "a about an and are can could do does for from have i in is it me memories memory "
    "my of on please recall remember tell that the to was were what whats when where "
    "which who why with would you your s".split()
)


def words(text: str) -> tuple[str, ...]:
    """Match case and diacritics like Unicode FTS, with literal word boundaries."""
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFD", text.casefold())
        if not unicodedata.combining(char)
    )
    return tuple(re.findall(r"[^\W_]+", normalized))


def query_words(text: str, *, allow_filler_only: bool = True) -> tuple[str, ...]:
    """Remove question filler; permit literal filler-only searches by default."""
    terms = tuple(dict.fromkeys(words(text)))[:32]
    meaningful = tuple(term for term in terms if term not in _FILLER)
    return meaningful or (terms if allow_filler_only else ())


def register_word_match(connection: sqlite3.Connection, text: str) -> tuple[str, ...]:
    """Register a query-local deterministic SQLite word scorer on its owned connection."""
    terms = query_words(text)
    wanted = frozenset(terms)

    def hits(value: object) -> int:
        if not isinstance(value, (str, int, float)):
            return 0
        return len(wanted.intersection(words(str(value))))

    connection.create_function("ember_word_hits", 1, hits, deterministic=True)
    return terms


def value_word_hits(memory: Memory, terms: tuple[str, ...]) -> int:
    """Count direct value matches without interpreting or rewriting a stored claim."""
    payload = memory.content.payload
    if isinstance(payload, FactPayload):
        text = str(payload.value.value)
    elif isinstance(payload, EpisodePayload):
        text = payload.event
    elif isinstance(payload, PlanPayload):
        text = " ".join((payload.description, *payload.steps))
    elif isinstance(payload, GoalPayload):
        text = payload.description
    else:
        return 0
    return len(set(terms).intersection(words(text)))
