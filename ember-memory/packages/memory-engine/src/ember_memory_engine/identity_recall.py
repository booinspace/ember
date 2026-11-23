"""Compile request-scoped, exact identity matching over accepted canonical entities."""

import json
import re
from itertools import islice
from uuid import UUID

from ember_memory_abi.interfaces import MemoryQuery

from .errors import UnsupportedQueryError

IDENTITY_RECALL = "ember.identity_recall_v1"
_WORD = re.compile(r"[^\W_]+")
_MAX_WORDS = 256


def _names(text: str) -> tuple[set[str], bool]:
    # ponytail: bound contiguous runs and reuse the ASCII-folded alias index.
    # Unicode case-insensitive aliases beyond exact spelling need a normalized index.
    words = list(islice(_WORD.finditer(text), _MAX_WORDS + 1))
    names: set[str] = set()
    for i, start in enumerate(words[:_MAX_WORDS]):
        for j in range(i, min(i + 4, len(words), _MAX_WORDS)):
            surface = text[start.start() : words[j].end()]
            if len(surface) > 256:
                break
            tokens = [word.group() for word in words[i : j + 1]]
            for form in (surface, " ".join(tokens), "_".join(tokens)):
                names.add(form)
                names.add(form.lower())
    return names, len(words) > _MAX_WORDS


def _origins(query: MemoryQuery) -> tuple[str, ...] | None:
    value = query.options.extensions[IDENTITY_RECALL]
    if not isinstance(value, dict) or value.keys() - {"origin_store_ids"}:
        raise UnsupportedQueryError()
    if "origin_store_ids" not in value:
        return None
    items = value["origin_store_ids"]
    if not isinstance(items, list) or len(items) > 128:
        raise UnsupportedQueryError()
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise UnsupportedQueryError()
        try:
            identity = UUID(item)
        except ValueError as error:
            raise UnsupportedQueryError() from error
        if identity.version != 4 or item != str(identity):
            raise UnsupportedQueryError()
        result.append(item)
    return tuple(dict.fromkeys(result))


# Start from indexed aliases and dependencies. A fact merely mentioning an entity
# as its object is not a claim about that entity; episodes can name participants.
_NAMED_SUBJECTS = """
    AND (r.memory_id,r.revision) IN (
        WITH named AS MATERIALIZED (
            SELECT DISTINCT entity_id FROM entity_aliases
            WHERE lower(alias) IN (SELECT value FROM json_each(?))
        )
        SELECT d.memory_id,d.revision FROM named n
        CROSS JOIN memory_dependencies d INDEXED BY suppression_dependencies
        ON d.target_kind='entity' AND d.target_key=n.entity_id
        JOIN claim_projection subject ON subject.memory_id=d.memory_id
        AND subject.revision=d.revision
        WHERE subject.subject_id='' OR subject.subject_id=n.entity_id
    )
"""


def narrow(query: MemoryQuery) -> tuple[str, tuple[str, ...], bool]:
    """Narrow canonical eligibility before limits, with no authority from alias or origin."""
    if query.mode != "structured":
        raise UnsupportedQueryError()
    origins = _origins(query)
    scope = ""
    bindings: tuple[str, ...] = ()
    if origins is not None:
        scope = " AND p.origin_key IN (SELECT value FROM json_each(?))"
        bindings = (json.dumps(origins),)
    if not query.text:
        return scope, bindings, False
    names, truncated = _names(query.text)
    if not names or origins == ():
        return " AND 0", (), truncated
    return (
        _NAMED_SUBJECTS + scope,
        (json.dumps(sorted(names), ensure_ascii=False), *bindings),
        truncated,
    )
