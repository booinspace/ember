"""Bounded identity matching over accepted canonical entities."""

import json
import re
import sqlite3
from itertools import islice
from time import monotonic
from uuid import UUID

from ember_memory_abi.interfaces import MemoryQuery, TemporalQuery

from .database import row_text
from .errors import UnsupportedQueryError

IDENTITY_RECALL = "ember.identity_recall_v1"
CURRENT_BELIEF = "ember.current_belief_v1"
CURRENT_MODALITIES = ("FACTUAL_ASSERTION", "PREFERENCE", "BELIEF")
_WORD = re.compile(r"[^\W_]+")
_MAX_WORDS = 256
_FUZZY_WORD = re.compile(r"[a-z]{2}[a-z0-9]{2,62}", re.ASCII)
_MAX_ALIASES = 256


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


def origin_scope(query: MemoryQuery, extension: str) -> tuple[str, tuple[str, ...]]:
    """Narrow eligible origins without granting access to them."""
    value = query.options.extensions[extension]
    if (
        query.mode != "structured"
        or not isinstance(value, dict)
        or value.keys() - {"origin_store_ids"}
    ):
        raise UnsupportedQueryError()
    if "origin_store_ids" not in value:
        return "", ()
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
    return " AND p.origin_key IN (SELECT value FROM json_each(?))", (
        json.dumps(tuple(dict.fromkeys(result))),
    )


def current_query(query: MemoryQuery) -> MemoryQuery:
    """Reject historical/current ambiguity rather than silently rewriting selectors."""
    if (
        IDENTITY_RECALL in query.options.extensions
        or query.temporal != TemporalQuery()
        or query.lifecycles != ("active",)
        or any(modality not in CURRENT_MODALITIES for modality in query.modalities)
    ):
        raise UnsupportedQueryError()
    if query.modalities:
        return query
    return query.model_copy(update={"modalities": CURRENT_MODALITIES})


# Facts match their subjects, not arbitrary objects. Episodes match linked entities.
_SUBJECT = "({claim}.subject_id='' OR {claim}.subject_id={entity})"
_EXACT = (
    "SELECT DISTINCT entity_id FROM entity_aliases WHERE lower(alias) IN "
    "(SELECT value FROM json_each(?))"
)
_RESOLVED = "SELECT value AS entity_id FROM json_each(?)"


def _match(keys: str, names: str) -> tuple[str, tuple[str, ...]]:
    subject = _SUBJECT.format(claim="subject", entity="n.entity_id")
    return (
        f""" AND (r.memory_id,r.revision) IN (
            WITH named AS MATERIALIZED ({names})
            SELECT d.memory_id,d.revision FROM named n
            CROSS JOIN memory_dependencies d INDEXED BY suppression_dependencies
                ON d.target_kind='entity' AND d.target_key=n.entity_id
            JOIN claim_projection subject ON subject.memory_id=d.memory_id
                AND subject.revision=d.revision
            WHERE {subject}
        )""",
        (keys,),
    )


def exact(text: str, *, current: bool) -> tuple[str, tuple[str, ...], bool]:
    """Compile phrase matches; only legacy structured lookup allows blank-text listing."""
    if not text and not current:
        return "", (), False
    names, truncated = _names(text)
    if not names:
        return " AND 0", (), truncated
    clause, values = _match(json.dumps(sorted(names), ensure_ascii=False), _EXACT)
    return clause, values, truncated


def resolved(ids: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Filter selected claims by canonical IDs, not approximate text."""
    return _match(json.dumps(ids), _RESOLVED)


def _one_edit(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1 or left == right:
        return False
    if len(left) > len(right):
        left, right = right, left
    i = next((i for i, (a, b) in enumerate(zip(left, right, strict=False)) if a != b), len(left))
    if len(left) < len(right):
        return left[i:] == right[i + 1 :]
    return left[i + 1 :] == right[i + 1 :] or (
        left[i : i + 2] == right[i : i + 2][::-1] and left[i + 2 :] == right[i + 2 :]
    )


def fuzzy_ids(
    connection: sqlite3.Connection,
    text: str,
    eligible_sql: str,
    eligible_values: tuple[str | int, ...],
    deadline: float,
) -> tuple[tuple[str, ...], str | None]:
    """Resolve unique typo matches in eligible vocabulary, or abstain on limits."""
    by_prefix: dict[str, set[str]] = {}
    for match in islice(_WORD.finditer(text), _MAX_WORDS):
        word = match.group().lower()
        if _FUZZY_WORD.fullmatch(word):
            by_prefix.setdefault(word[:2], set()).add(word)
    if not by_prefix:
        return (), None
    # ponytail: one edit on ASCII single words with an unchanged two-letter prefix.
    # Use a normalized alias index only if measured multilingual recall warrants it.
    # The eligible subquery has no top-k truncation. Only eligible aliases spend the cap.
    sql = f"""
        WITH eligible AS NOT MATERIALIZED ({eligible_sql})
        SELECT DISTINCT lower(a.alias) AS alias,a.entity_id
        FROM json_each(?) prefix CROSS JOIN entity_aliases a INDEXED BY entity_alias_fold
            ON lower(a.alias)>=prefix.value AND lower(a.alias)<prefix.value || '{{'
        WHERE length(a.alias) BETWEEN 4 AND 64
        AND EXISTS (
            SELECT 1 FROM memory_dependencies d INDEXED BY suppression_dependencies
            CROSS JOIN eligible e ON e.memory_id=d.memory_id AND e.revision=d.revision
            WHERE d.target_kind='entity' AND d.target_key=a.entity_id
            AND {_SUBJECT.format(claim="e", entity="a.entity_id")}
        )
        LIMIT ?
    """
    rows = connection.execute(
        sql, (*eligible_values, json.dumps(list(by_prefix)), _MAX_ALIASES + 1)
    ).fetchall()
    if len(rows) > _MAX_ALIASES:
        return (), "identity_alias_candidate_limit"
    matches: dict[str, set[str]] = {}
    for row in rows:
        if monotonic() >= deadline:
            return (), "identity_soft_deadline_miss"
        alias = row_text(row, "alias")
        if not _FUZZY_WORD.fullmatch(alias):
            continue
        for word in by_prefix[alias[:2]]:
            if _one_edit(word, alias):
                matches.setdefault(word, set()).add(row_text(row, "entity_id"))
    return tuple(sorted({next(iter(ids)) for ids in matches.values() if len(ids) == 1})), None
