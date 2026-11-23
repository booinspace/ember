"""Scope-first FTS/vector fusion with final canonical revision/evidence/policy checks."""

import re
import sqlite3
from concurrent.futures import Future, TimeoutError
from contextlib import ExitStack, closing
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from time import monotonic
from uuid import UUID

from pydantic import ValidationError

from ember_memory_abi.archive import Document, Message
from ember_memory_abi.common import (
    CanonicalId,
    canonical_object_sha256,
    timestamp_from_datetime,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.errors import (
    AccessDeniedError,
    InvalidEvidenceError,
    NotFoundError,
    SourceUnavailableError,
)
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    ArchiveResult,
    ArchiveSearchResponse,
    MemoryQuery,
    MemoryResult,
    RankingExplanation,
    RetrievalCoverage,
    SearchResponse,
)
from ember_memory_abi.memory import ArchiveChunk, Entity, FactPayload, Memory

from ._state import CharacterState
from .database import load_model, row_int, row_text
from .embedding import EncodingPool
from .errors import CorruptStoreError, EmbeddingError, EngineBusyError, IndexError
from .index_storage import IndexStore, connect
from .read_budget import ReadBudget, bounded_connection
from .retrieval import memory_scope
from .search_contracts import IndexGeneration
from .selection import source_scope, unrestricted
from .vector_search import VectorPool, VectorRequest
from .word_recall import (
    EXTRACTED_TEXT_SQL,
    WORD_RANKER,
    query_words,
    register_word_match,
)

_ARCHIVE_RANKING_KEY = "ember.archive_ranking"
_ARCHIVE_PRECISION_PROFILE = "precision_v1"
# Similarity floor for unit-normalized embeddings, not a calibrated probability.
_ARCHIVE_MIN_COSINE = 0.75
_ARCHIVE_CERTIFY_BYTES = 4 * 1024 * 1024
_ARCHIVE_FILLER = frozenset(
    "a an are at for from how it the to was were what when who with".split()
)
_ARCHIVE_HEADER_COLUMNS = (
    "d.id,d.kind,d.target_id,d.source_store_id,d.record_kind,d.record_id,"
    "d.revision,d.part_id,d.branch"
)

# Chunk IDs are global. Message/Document projections from different parts of the
# same exact source revision compete for one result, not one result per part.
type _ArchiveKey = str | tuple[str, str, str, int]


@dataclass(frozen=True, slots=True)
class _ArchiveSnapshot:
    generation: IndexGeneration
    path: Path
    certified: bool


@dataclass(frozen=True, slots=True)
class _ArchiveHeader:
    snapshot: _ArchiveSnapshot
    document_id: int
    key: _ArchiveKey
    part_id: str | None
    branch: str
    score: float
    ranking: tuple[RankingExplanation, ...]


def _archive_header(
    snapshot: _ArchiveSnapshot,
    row: sqlite3.Row,
    score: float,
    ranking: tuple[RankingExplanation, ...],
) -> _ArchiveHeader:
    try:
        store = row_text(row, "source_store_id")
        if store != str(snapshot.generation.store_id):
            raise IndexError()
        kind = row_text(row, "kind")
        if kind == "chunk":
            key: _ArchiveKey = row_text(row, "target_id")
            part_id = None
        elif kind == "message":
            key = (
                store,
                row_text(row, "record_kind"),
                row_text(row, "record_id"),
                row_int(row, "revision"),
            )
            part_id = row_text(row, "part_id")
        else:
            raise IndexError()
        return _ArchiveHeader(
            snapshot, row_int(row, "id"), key, part_id, row_text(row, "branch"), score, ranking
        )
    except CorruptStoreError as error:
        raise IndexError() from error


def _whole_source_key(reference: SourceRef) -> tuple[str, str, str, int]:
    return (
        str(reference.source_store_id),
        reference.record_kind,
        str(reference.record_id),
        reference.record_revision,
    )


def _hydrate_archive(
    state: CharacterState,
    canonical: sqlite3.Connection,
    query: ArchiveQuery,
    header: _ArchiveHeader,
    row: sqlite3.Row | None,
) -> ArchiveResult | None:
    payload: object = row["payload_json"] if row is not None else None
    if not isinstance(payload, str):
        raise IndexError()
    if isinstance(header.key, str):
        chunk = ArchiveChunk.model_validate_json(payload)
        if str(chunk.chunk_id) != header.key or not chunk.source_spans:
            raise IndexError()
        for span in chunk.source_spans:
            reference = span.evidence.source
            if reference.source_store_id != header.snapshot.generation.store_id:
                raise IndexError()
            if not state.source_permitted(canonical, reference, "archive_search"):
                return None
            quote = state.resolver.validate(span.evidence)
            if quote != chunk.text[span.start_codepoint : span.end_codepoint]:
                raise IndexError()
        return ArchiveResult(
            record=chunk, score=header.score, ranking=header.ranking, branch_status=header.branch
        )

    reference = SourceRef.model_validate_json(payload)
    if (
        _whole_source_key(reference) != header.key
        or reference.part_id is None
        or str(reference.part_id) != header.part_id
    ):
        raise IndexError()
    if not state.source_permitted(canonical, reference, "archive_search"):
        return None
    record = state.resolver.resolve(reference)
    if not isinstance(record, (Message, Document)):
        return None
    if any(
        state.source_suppressed(
            canonical, record.source.model_copy(update={"part_id": part.part_id})
        )
        for part in record.parts
    ):
        return None
    if query.mode == "literal" and not any(
        part.text and query.text.casefold() in part.text.casefold() for part in record.parts
    ):
        return None
    return ArchiveResult(
        record=record, score=header.score, ranking=header.ranking, branch_status=header.branch
    )


def _archive_permitted(
    state: CharacterState, canonical: sqlite3.Connection, item: ArchiveResult
) -> bool:
    if isinstance(item.record, ArchiveChunk):
        return all(
            state.source_permitted(canonical, span.evidence.source, "archive_search")
            for span in item.record.source_spans
        )
    return state.source_permitted(canonical, item.record.source, "archive_search")


def _archive_results(
    state: CharacterState,
    canonical: sqlite3.Connection,
    query: ArchiveQuery,
    ranked: list[tuple[_ArchiveHeader, ArchiveResult | None]],
) -> tuple[ArchiveResult, ...]:
    results: list[ArchiveResult] = []
    seen: set[_ArchiveKey] = set()
    with ExitStack() as reader:
        path: Path | None = None
        connection: sqlite3.Connection | None = None
        for header, item in ranked:
            if header.key in seen:
                continue
            if item is None:
                # ponytail: keep one payload reader even with many granted libraries.
                # Reuse more connections only if measured store switching warrants it.
                if connection is None or path != header.snapshot.path:
                    reader.close()
                    path = header.snapshot.path
                    connection = reader.enter_context(
                        closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True))
                    )
                    connection.row_factory = sqlite3.Row
                    reader.enter_context(bounded_connection(connection, state.read_budget))
                row = connection.execute(
                    "SELECT payload_json FROM documents WHERE id=?", (header.document_id,)
                ).fetchone()
                item = _hydrate_archive(state, canonical, query, header, row)
                if item is None:
                    continue
            seen.add(header.key)
            results.append(item)
            if len(results) == query.limit:
                # Expiry does not bump the policy epoch. Refill from the same window;
                # duplicates of an expired result depend on the same exact grants.
                results = [item for item in results if _archive_permitted(state, canonical, item)]
                if len(results) == query.limit:
                    return tuple(results)
        return tuple(item for item in results if _archive_permitted(state, canonical, item))


def fts_expression(text: str) -> str:
    """Compile bounded Unicode terms as literal FTS expressions, never user SQL/operators."""
    return " OR ".join(
        '"' + term + '"' for term in dict.fromkeys(re.findall(r"[^\W_]\w*", text.casefold())[:32])
    )


def _attach(connection: sqlite3.Connection, state: CharacterState) -> None:
    connection.execute(
        "ATTACH DATABASE ? AS canonical", (state.database.path.as_uri() + "?mode=ro",)
    )


def _canonical_scope(
    state: CharacterState, query: MemoryQuery, *, lookup: bool
) -> tuple[str, tuple[str | int, ...]]:
    sql, values = memory_scope(state, query, metadata_only=True, index_lookup=lookup)
    sql = sql.rsplit(" ORDER BY ", 1)[0]
    for table in (
        "memory_revisions",
        "memories",
        "suppression_targets",
        "memory_dependencies",
        "claim_projection",
        "memory_evidence",
        "source_grants",
        "recall_policy",
        "recall_libraries",
    ):
        sql = re.sub(r"\b" + table + r"\b", "canonical." + table, sql)
    return sql, values[:-1]


def _memory_eligibility(
    state: CharacterState, query: MemoryQuery
) -> tuple[str, tuple[str | int, ...]]:
    sql, values = _canonical_scope(state, query, lookup=True)
    return (
        "d.kind='memory' AND EXISTS(SELECT 1 FROM (" + sql + ") eligible "
        "WHERE eligible.memory_id=d.target_id AND eligible.revision=d.revision)",
        values,
    )


def _memory_vector_ids(state: CharacterState, query: MemoryQuery) -> str:
    """Stream canonical eligibility once; avoid a correlated lookup per projected vector."""
    sql, _ = _canonical_scope(state, query, lookup=False)
    return (
        "SELECT e.id FROM (" + sql + ") eligible "
        "CROSS JOIN documents d INDEXED BY doc_target ON d.kind='memory' "
        "AND d.target_id=eligible.memory_id AND d.revision=eligible.revision "
        "CROSS JOIN embedding_records e INDEXED BY vector_target ON e.document_id=d.id"
    )


def projected_memory_scope(
    state: CharacterState, query: MemoryQuery
) -> tuple[str, tuple[str | int, ...]]:
    """Use certified immutable headers only while their canonical outbox remains current."""
    clauses = ["d.kind='memory'", "d.recall_eligible=1", "d.is_current=1"]
    values: list[str | int] = []
    clauses.append("(d.scope_until_us IS NULL OR d.scope_until_us>?)")
    values.append(
        timestamp_to_unix_microseconds(timestamp_from_datetime(state.database.clock.now()))
    )
    for column, items in (
        ("d.memory_kind", query.kinds),
        ("d.modality", query.modalities),
        ("d.predicate", query.predicates),
    ):
        if items:
            clauses.append(column + " IN (" + ",".join("?" for _ in items) + ")")
            values.extend(items)
    lifecycle = (
        (*query.lifecycles, "superseded")
        if (query.temporal.valid_at and "active" in query.lifecycles)
        else query.lifecycles
    )
    if lifecycle:
        clauses.append("d.lifecycle IN (" + ",".join("?" for _ in lifecycle) + ")")
        values.extend(lifecycle)
    at = timestamp_to_unix_microseconds(
        query.temporal.valid_at or timestamp_from_datetime(state.database.clock.now())
    )
    if query.temporal.valid_at:
        clauses.append(
            "d.valid_from_us IS NOT NULL AND d.valid_from_us<=? AND "
            "((d.valid_until_us IS NOT NULL AND ?<d.valid_until_us) OR "
            "(d.valid_until_us IS NULL AND d.open_end_known=1))"
        )
    else:
        clauses.append(
            "(d.valid_from_us IS NULL OR d.valid_from_us<=?) AND "
            "(d.memory_kind='episode' OR d.valid_until_us IS NULL OR ?<d.valid_until_us)"
        )
    values.extend((at, at))
    if query.entity_ids:
        clauses.append(
            "EXISTS(SELECT 1 FROM json_each(d.entity_keys) entity "
            "WHERE entity.value IN (" + ",".join("?" for _ in query.entity_ids) + "))"
        )
        values.extend(str(item) for item in query.entity_ids)
    return " AND ".join(clauses), tuple(values)


def _archive_eligibility(
    state: CharacterState, query: ArchiveQuery
) -> tuple[str, tuple[str | int, ...]]:
    now = timestamp_from_datetime(state.database.clock.now())
    values: list[str | int] = [str(state.character_id), now]
    # Every chunk dependency must be in current whole-record archive scope. The
    # same-message part suppression check matches canonical conservative forgetting.
    denied = """
        NOT (p.source_store_id=? OR EXISTS(
            SELECT 1 FROM canonical.source_grants g WHERE g.source_store_id=p.source_store_id
            AND g.record_kind=p.record_kind AND g.record_id=p.record_id
            AND g.record_revision=p.revision AND g.part_id='' AND g.capability='archive_search'
            AND (g.expires_at IS NULL OR g.expires_at>?)))
        OR EXISTS(SELECT 1 FROM documents sibling JOIN canonical.suppression_targets s
            ON s.target_kind='source'
            AND s.target_key IN (sibling.source_key,sibling.whole_source_key)
            WHERE sibling.kind='message' AND sibling.source_store_id=p.source_store_id
            AND sibling.record_kind=p.record_kind AND sibling.record_id=p.record_id)
    """
    selection_sql, selection_values = source_scope(state.character_id, "p.source_store_id")
    for table in ("recall_policy", "recall_libraries"):
        selection_sql = re.sub(r"\b" + table + r"\b", "canonical." + table, selection_sql)
    filters: list[str] = ["NOT " + selection_sql]
    values.extend(selection_values)
    for column, items in (("p.role", query.roles), ("p.record_kind", query.record_kinds)):
        if items:
            filters.append(column + " NOT IN (" + ",".join("?" for _ in items) + ")")
            values.extend(items)
    if query.conversation_ids:
        filters.append(
            "substr(p.conversation_id,1,36) NOT IN ("
            + ",".join("?" for _ in query.conversation_ids)
            + ")"
        )
        values.extend(str(item) for item in query.conversation_ids)
    for boundary, comparator in ((query.source_from, "<"), (query.source_until, ">=")):
        if boundary:
            filters.append("(p.source_at_us IS NULL OR p.source_at_us" + comparator + "?)")
            values.append(timestamp_to_unix_microseconds(boundary))
    if query.temporal.valid_at:
        filters.append("(p.source_at_us IS NULL OR p.source_at_us<>?)")
        values.append(timestamp_to_unix_microseconds(query.temporal.valid_at))
    if query.temporal.recorded_before:
        filters.append("p.recorded_at_us>=?")
        values.append(timestamp_to_unix_microseconds(query.temporal.recorded_before))
    if filters:
        denied = "(" + denied + ") OR " + " OR ".join(filters)
    # A synthetic dependency for message projections lets the same scope predicate
    # serve both full-source FTS and exact chunk vectors.
    base = (
        "d.kind IN ('message','chunk') "
        "AND EXISTS(SELECT 1 FROM chunk_dependencies present WHERE present.document_id=d.id) "
        "AND NOT EXISTS(SELECT 1 "
        "FROM chunk_dependencies c JOIN documents p ON p.kind='message' "
        "AND p.source_store_id=c.source_store_id AND p.record_kind=c.record_kind "
        "AND p.record_id=c.record_id AND p.revision=c.revision AND p.part_id=c.part_id "
        "WHERE c.document_id=d.id AND (" + denied + "))"
    )
    if not query.include_alternative_branches:
        base += " AND d.branch IN ('selected','not_applicable')"
    return base, tuple(values)


def _adjustments(
    canonical: sqlite3.Connection, memory: Memory, score: float, query: MemoryQuery, now_us: int
) -> tuple[RankingExplanation, ...]:
    """Bound non-vector contributions: importance 15%, entity 10%, recency 10%."""
    adjustments = [
        RankingExplanation(
            method="bounded_importance_v1",
            score=score * 0.15 * memory.content.importance,
            reasons=("IMPORTANT",),
        )
    ]
    payload = memory.content.payload
    if isinstance(payload, FactPayload):
        row = canonical.execute(
            "SELECT * FROM entities WHERE entity_id=? ORDER BY revision DESC LIMIT 1",
            (str(payload.subject.entity_id),),
        ).fetchone()
        if row is not None:
            entity = load_model(row, Entity)
            if payload.subject.entity_id in query.entity_ids or any(
                name.casefold() in query.text.casefold() for name in (entity.name, *entity.aliases)
            ):
                adjustments.append(
                    RankingExplanation(
                        method="bounded_entity_v1",
                        score=score * 0.10,
                        reasons=("ENTITY_MATCH",),
                    )
                )
        if payload.predicate in query.predicates:
            adjustments.append(
                RankingExplanation(
                    method="structured_scope_v1",
                    score=0.0,
                    reasons=("STRUCTURED_FACT_MATCH",),
                )
            )
    age = max(0, now_us - timestamp_to_unix_microseconds(memory.temporal.recorded_at))
    recency = max(0.0, 1.0 - age / (30 * 86400 * 1_000_000))
    adjustments.append(
        RankingExplanation(
            method="bounded_recency_v1",
            score=score * 0.10 * recency,
            reasons=("TEMPORAL_MATCH",),
        )
    )
    return tuple(adjustments)


class IndexedRetriever:
    """A character-bound backend using library-shared generations and an owned CPU pool."""

    def __init__(
        self,
        identity: CanonicalId,
        indexes: dict[CanonicalId, IndexStore],
        pool: EncodingPool,
        fast_vectors: VectorPool,
        deep_vectors: VectorPool,
    ) -> None:
        self.identity = identity
        self.indexes = indexes
        self.pool = pool
        self.fast_vectors = fast_vectors
        self.deep_vectors = deep_vectors
        self.last_failure: str | None = None
        self._epoch: int | None = None
        self.read_budget: ReadBudget | None = None

    def available(self) -> bool:
        """Report only a readable character generation; no filesystem/model repair or download."""
        try:
            _, path = self.indexes[self.identity].active(self.read_budget)
            with closing(connect(path, immutable=True, require_extension=False)) as connection:
                with bounded_connection(connection, self.read_budget):
                    connection.execute("SELECT count(*) FROM metadata").fetchone()
            return True
        except (IndexError, sqlite3.Error):
            return False

    def _candidates(
        self,
        state: CharacterState,
        index: IndexStore,
        scope: str,
        values: tuple[str | int, ...],
        text: str,
        mode: str,
        deadline: float,
        vector_ids: str | None = None,
        plain_current: bool = False,
        deep: bool = False,
        isolated: bool = False,
        memory_words: bool = False,
        archive_precision: bool = False,
        archive_snapshot: _ArchiveSnapshot | None = None,
    ) -> tuple[
        IndexGeneration,
        list[tuple[sqlite3.Row, float, tuple[RankingExplanation, ...]]],
        tuple[str, ...],
    ]:
        generation, path = (
            (archive_snapshot.generation, archive_snapshot.path)
            if archive_snapshot is not None
            else index.active(state.read_budget)
        )
        reasons: list[str] = []
        lexical: list[int] = []
        semantic: list[int] = []
        value_scores: dict[int, float] = {}
        with (
            closing(connect(path, immutable=True, require_extension=False)) as connection,
            bounded_connection(connection, state.read_budget),
        ):
            _attach(connection, state)
            terms = register_word_match(connection, text) if memory_words else ()
            lexical_text = " ".join(terms) if memory_words else text
            if archive_precision:
                lexical_text = " ".join(
                    term
                    for term in query_words(text, allow_filler_only=False)
                    if term not in _ARCHIVE_FILLER
                )
            connection.set_progress_handler(
                lambda: int(
                    monotonic() > deadline
                    or (state.read_budget is not None and state.read_budget.exhausted())
                ),
                1000,
            )
            vector_job: Future[tuple[int, ...]] | None = None
            if mode not in {"lexical", "literal", "structured"}:
                if (
                    self.pool.encoder is None
                    or self.pool.encoder.specification != generation.embedding
                ):
                    reasons.append("semantic_encoder_missing_or_profile_mismatch")
                else:
                    eligible_ids = vector_ids or (
                        "SELECT e.id FROM embedding_records e JOIN documents d "
                        "ON d.id=e.document_id WHERE " + scope
                    )
                    parameters = values
                    max_distance = None
                    if archive_precision:
                        max_distance = (
                            sqrt(2.0 * (1.0 - _ARCHIVE_MIN_COSINE))
                            if generation.distance_metric == "l2"
                            else 1.0 - _ARCHIVE_MIN_COSINE
                        )
                    if plain_current and generation.layout == "ember.search-layout@0.6.0":
                        now = timestamp_to_unix_microseconds(
                            timestamp_from_datetime(state.database.clock.now())
                        )
                        statement = (
                            "SELECT document_id FROM vec_documents WHERE embedding MATCH ? "
                            "AND k=40 "
                            "AND active=1 AND valid_from<=? AND valid_until>? ORDER BY distance"
                        )
                        parameters = (now, now)
                    else:
                        columns = (
                            "e.document_id, v.distance" if archive_precision else "e.document_id"
                        )
                        statement = (
                            f"SELECT {columns} FROM vec_documents v JOIN embedding_records e ON "
                            "e.id=v.rowid WHERE v.embedding MATCH ? AND k=40 "
                            "AND v.rowid IN (" + eligible_ids + ") ORDER BY v.distance"
                        )
                    release = index.lease(path)
                    try:
                        vector_job = (self.deep_vectors if deep else self.fast_vectors).submit(
                            VectorRequest(
                                generation=path,
                                canonical=state.database.path,
                                statement=statement,
                                parameters=parameters,
                                encoding=self.pool.query(text, deep=isolated),
                                release=release,
                                budget=state.read_budget,
                                max_distance=max_distance,
                            )
                        )
                    except (EmbeddingError, EngineBusyError):
                        reasons.append(
                            "deep_semantic_deferred_for_foreground_isolation"
                            if isolated
                            else "semantic_deadline_or_admission_miss"
                        )
                    finally:
                        if vector_job is None:
                            release()
            try:
                if mode == "literal":
                    connection.create_function(
                        "ember_casefold", 1, str.casefold, deterministic=True
                    )
                    lexical = [
                        row_int(row, "id")
                        for row in connection.execute(
                            "SELECT d.id FROM documents d WHERE d.kind='message' AND "
                            + scope
                            + " AND instr(ember_casefold(d.text),?)>0 "
                            "ORDER BY d.source_at_us,d.id LIMIT 40",
                            (*values, text.casefold()),
                        ).fetchall()
                    ]
                elif mode not in {"semantic", "structured"} and fts_expression(lexical_text):
                    value_join = (
                        " JOIN canonical.memory_revisions r ON r.memory_id=d.target_id "
                        "AND r.revision=d.revision "
                        if memory_words
                        else " "
                    )
                    order = (
                        "value_hits DESC,r.importance DESC,document_fts.rank,d.id LIMIT 100"
                        if memory_words
                        else "document_fts.rank LIMIT 40"
                    )
                    projection = (
                        "d.id,ember_word_hits(" + EXTRACTED_TEXT_SQL + ") AS value_hits,"
                        "r.importance AS memory_importance"
                        if memory_words
                        else "d.id"
                    )
                    rows = connection.execute(
                        "SELECT " + projection + " FROM document_fts CROSS JOIN documents d "
                        "ON d.id=document_fts.rowid "
                        + value_join
                        + "WHERE document_fts MATCH ? AND "
                        + scope
                        + " ORDER BY "
                        + order,
                        (fts_expression(lexical_text), *values),
                    ).fetchall()
                    lexical = [row_int(row, "id") for row in rows]
                    if memory_words and terms:
                        for row in rows:
                            hits = row_int(row, "value_hits")
                            importance: object = row["memory_importance"]
                            if not isinstance(importance, (int, float)):
                                raise IndexError()
                            if hits:
                                value_scores[row_int(row, "id")] = (
                                    0.05 * hits / len(terms) * (0.5 + importance / 2)
                                )
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode != sqlite3.SQLITE_INTERRUPT:
                    raise
                reasons.append("lexical_soft_deadline_miss")
            if vector_job is not None:
                try:
                    semantic = list(
                        vector_job.result(timeout=max(0.0, deadline - monotonic() - 0.015))
                    )
                except (EmbeddingError, EngineBusyError, IndexError, TimeoutError):
                    reasons.append("semantic_deadline_or_admission_miss")
            # Finish bounded exact-ID reads after the soft ranking deadline. Archive
            # batches still respect the deep request's hard read budget.
            budget = state.read_budget if archive_snapshot is not None else None
            if budget is None:
                connection.set_progress_handler(None, 0)
            else:
                connection.set_progress_handler(lambda: int(budget.exhausted()), 1000)
            ranks: dict[int, list[RankingExplanation]] = {}
            for candidates, reason in ((lexical, "LEXICAL_MATCH"), (semantic, "SEMANTIC_MATCH")):
                for rank, identity in enumerate(candidates, 1):
                    contribution = 1.0 / (60 + rank)
                    ranks.setdefault(identity, []).append(
                        RankingExplanation(
                            method="reciprocal_rank_fusion_v1",
                            score=contribution,
                            reasons=(reason,),
                            extensions={"ember.rank": rank},
                        )
                    )
            # Promote literal values before hydration as well as final ranking, so
            # a soft deadline cannot spend all its reads on generic semantic labels.
            for identity, contribution in value_scores.items():
                ranks[identity].append(
                    RankingExplanation(
                        method="extracted_word_match_v1",
                        score=contribution,
                        reasons=("LEXICAL_MATCH",),
                    )
                )
            result: list[tuple[sqlite3.Row, float, tuple[RankingExplanation, ...]]] = []
            if archive_snapshot is None:
                for identity, explanations in ranks.items():
                    row = connection.execute(
                        "SELECT * FROM documents d WHERE d.id=? AND " + scope, (identity, *values)
                    ).fetchone()
                    if row is not None:
                        result.append(
                            (row, sum(item.score for item in explanations), tuple(explanations))
                        )
            elif ranks:
                # Preserve the second scope check, without an exact-ID read per candidate.
                columns = _ARCHIVE_HEADER_COLUMNS
                if not archive_snapshot.certified:
                    columns += ",d.payload_json"
                placeholders = ",".join("?" for _ in ranks)
                rows = connection.execute(
                    f"SELECT {columns} FROM documents d WHERE d.id IN ({placeholders}) AND "
                    + scope,
                    (*ranks, *values),
                ).fetchall()
                for row in rows:
                    explanations = ranks[row_int(row, "id")]
                    result.append(
                        (row, sum(item.score for item in explanations), tuple(explanations))
                    )
            if archive_snapshot is None or not archive_snapshot.certified:
                # Keep eager validation in its original order on uncertified indexes.
                result.sort(key=lambda item: (-item[1], row_int(item[0], "id")))
            return generation, result, tuple(reasons)

    def memory(
        self,
        state: CharacterState,
        canonical: sqlite3.Connection,
        query: MemoryQuery,
        *,
        deep: bool = False,
    ) -> SearchResponse | None:
        """Filter canonical scope before top-k, then fuse/rank/deduplicate verified revisions."""
        epoch = state.policy_epoch(canonical)
        if epoch != self._epoch:
            if not deep:
                self.pool.invalidate()
            self._epoch = epoch
        deadline = monotonic() + 0.15
        try:
            index = self.indexes[self.identity]
            previous, path = index.active(state.read_budget)
            pending = (
                canonical.execute(
                    "SELECT 1 FROM index_outbox WHERE sequence>? AND kind NOT IN "
                    "('source_changed','pin','unpin','record_access') LIMIT 1",
                    (previous.outbox_sequence,),
                ).fetchone()
                is not None
            )
            restrictive_pending = (
                canonical.execute(
                    "SELECT 1 FROM index_outbox WHERE sequence>? "
                    "AND kind NOT IN ('source_changed','pin','unpin','record_access',"
                    "'seed_grants','remember') "
                    "AND NOT(kind='memory_revision' AND revision=1) LIMIT 1",
                    (previous.outbox_sequence,),
                ).fetchone()
                is not None
            )
            certified = (
                previous.header_projection == "ember.canonical-headers@0.2.0"
                and not restrictive_pending
                and unrestricted(canonical)
                and query.temporal.recorded_before is None
                and not any(
                    (
                        query.temporal.observed_from,
                        query.temporal.observed_until,
                        query.temporal.valid_from,
                        query.temporal.valid_until,
                    )
                )
                and (
                    index.certified(previous, path, cached_only=True)
                    if deep
                    else index.certified(previous, path)
                )
            )
            scope, values = (
                projected_memory_scope(state, query)
                if certified
                else _memory_eligibility(state, query)
            )
            generation, candidates, reasons = self._candidates(
                state,
                self.indexes[self.identity],
                scope,
                values,
                query.text,
                query.mode,
                deadline,
                None if certified else _memory_vector_ids(state, query),
                deep=deep,
                isolated=deep,
                memory_words=True,
                plain_current=(
                    certified
                    and not query.kinds
                    and not query.modalities
                    and not query.predicates
                    and not query.entity_ids
                    and query.lifecycles == ("active",)
                    and query.temporal.valid_at is None
                ),
            )
            current_clock = row_int(
                canonical.execute("SELECT recorded_clock_us FROM store_metadata").fetchone(),
                "recorded_clock_us",
            )
            pending = (
                canonical.execute(
                    "SELECT 1 FROM index_outbox WHERE sequence>? AND kind NOT IN "
                    "('source_changed','pin','unpin','record_access') LIMIT 1",
                    (generation.outbox_sequence,),
                ).fetchone()
                is not None
            )
            stale = pending or (not certified and current_clock != generation.canonical_clock_us)
            result: list[MemoryResult] = []
            seen: set[str] = set()
            for row, score, ranking in candidates:
                if certified and generation.layout in {
                    "ember.search-layout@0.4.0",
                    "ember.search-layout@0.5.0",
                    "ember.search-layout@0.6.0",
                }:
                    if row_text(row, "payload_key") in seen:
                        continue
                if result and monotonic() > deadline:
                    reasons = (*reasons, "final_validation_soft_deadline_miss")
                    break
                memory = state.read_memory(
                    canonical, UUID(row_text(row, "target_id")), row_int(row, "revision")
                )
                # Exact active/history eligibility is checked again on the character
                # connection; a stale index can never deliver an obsolete current row.
                sql, params = memory_scope(
                    state, query.model_copy(update={"text": "", "limit": 100})
                )
                verify = sql.rsplit(" ORDER BY ", 1)[0] + " AND r.memory_id=? AND r.revision=?"
                if (
                    canonical.execute(
                        verify, (*params[:-1], str(memory.memory_id), memory.revision)
                    ).fetchone()
                    is None
                ):
                    continue
                fingerprint = canonical_object_sha256(memory.content.payload)
                if (
                    certified
                    and generation.layout
                    in {
                        "ember.search-layout@0.4.0",
                        "ember.search-layout@0.5.0",
                        "ember.search-layout@0.6.0",
                    }
                    and (fingerprint != row_text(row, "payload_key"))
                ):
                    raise IndexError()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                adjustments = _adjustments(
                    canonical,
                    memory,
                    score,
                    query,
                    timestamp_to_unix_microseconds(
                        timestamp_from_datetime(state.database.clock.now())
                    ),
                )
                result.append(
                    MemoryResult(
                        memory=memory,
                        score=score + sum(item.score for item in adjustments),
                        ranking=(*ranking, *adjustments),
                    )
                )
            if stale and query.mode != "semantic":
                sql, params = memory_scope(state, query.model_copy(update={"limit": 100}))
                dirty = sql.rsplit(" ORDER BY ", 1)[0] + (
                    " AND r.recorded_at_us>? ORDER BY r.recorded_at_us DESC LIMIT 100"
                )
                terms = query_words(query.text)
                from .index_builder import memory_text

                present = {item.memory.memory_id for item in result}
                for row in canonical.execute(dirty, (*params[:-1], generation.canonical_clock_us)):
                    memory = load_model(row, Memory)
                    projected = memory_text(memory, {}).casefold()
                    if memory.memory_id not in present and any(term in projected for term in terms):
                        result.append(
                            MemoryResult(
                                memory=memory,
                                score=1.0 / 61,
                                ranking=(
                                    RankingExplanation(
                                        method="pending_canonical_lexical",
                                        score=1.0 / 61,
                                        reasons=("LEXICAL_MATCH", "DEGRADED_FALLBACK"),
                                    ),
                                ),
                            )
                        )
                        present.add(memory.memory_id)
            if stale or "semantic_deadline_or_admission_miss" in reasons:
                sql, params = memory_scope(
                    state,
                    query.model_copy(update={"text": "", "limit": 6}),
                    metadata_only=True,
                    recent_first=True,
                )
                present = {item.memory.memory_id for item in result}
                for row in canonical.execute(sql, params).fetchall():
                    identity = UUID(row_text(row, "memory_id"))
                    if identity not in present:
                        memory = state.read_memory(canonical, identity, row_int(row, "revision"))
                        result.append(
                            MemoryResult(
                                memory=memory,
                                score=0.001,
                                ranking=(
                                    RankingExplanation(
                                        method="bounded_recent_structured_fallback_v1",
                                        score=0.001,
                                        reasons=("DEGRADED_FALLBACK", "STRUCTURED_FACT_MATCH"),
                                    ),
                                ),
                            )
                        )
                        present.add(identity)
            result.sort(key=lambda item: (-item.score, str(item.memory.memory_id)))
            state.check_policy(canonical, epoch)
            return SearchResponse(
                results=tuple(result[: query.limit]),
                coverage=RetrievalCoverage(
                    status="partial" if reasons or stale else "complete",
                    index_status="available",
                    reason_codes=(*reasons, *(("index_outbox_pending",) if stale else ())),
                    extensions={
                        "ember.retrieval": {
                            "generation": str(generation.generation),
                            "policy_epoch": epoch,
                            "ranker": WORD_RANKER,
                            "generation_ranker": generation.ranker,
                            "distance_metric": generation.distance_metric,
                            "certified_headers": certified,
                            "outbox_sequence": generation.outbox_sequence,
                            "soft_deadline_ms": 150,
                            "mode": query.mode,
                            "temporal": query.temporal.model_dump_json(),
                        }
                    },
                ),
            )
        except (IndexError, sqlite3.Error, ValidationError, ValueError):
            self.last_failure = "missing_or_corrupt_derived_index"
            return None

    def archive(
        self,
        state: CharacterState,
        canonical: sqlite3.Connection,
        query: ArchiveQuery,
        *,
        deep: bool = False,
    ) -> ArchiveSearchResponse | None:
        """Search shared archive projections with whole-scope eligibility and exact provenance."""
        epoch = state.policy_epoch(canonical)
        if epoch != self._epoch:
            if not deep:
                self.pool.invalidate()
            self._epoch = epoch
        deadline = monotonic() + 0.5
        precision = (
            query.mode == "hybrid"
            and query.options.extensions.get(_ARCHIVE_RANKING_KEY) == _ARCHIVE_PRECISION_PROFILE
        )
        scope, values = _archive_eligibility(state, query)
        stores = canonical.execute(
            "SELECT DISTINCT source_store_id FROM source_grants WHERE capability='archive_search'"
        ).fetchall()
        identities = {self.identity, *(UUID(row_text(row, "source_store_id")) for row in stores)}
        if query.source_store_ids:
            identities.intersection_update(query.source_store_ids)
        candidates: list[tuple[_ArchiveHeader, ArchiveResult | None]] = []
        generations: list[str] = []
        reasons: list[str] = []
        certify_bytes = 0 if state.read_budget is not None else _ARCHIVE_CERTIFY_BYTES
        try:
            with ExitStack() as leases:
                for identity in sorted(identities, key=str):
                    index = self.indexes.get(identity)
                    if index is None:
                        reasons.append("source_index_unavailable")
                        continue
                    if index.source_coverage() == "evidence_only":
                        reasons.append("source_library_evidence_only")
                    try:
                        generation, path, release = index.lease_active(state.read_budget)
                        leases.callback(release)
                    except IndexError:
                        reasons.append("source_index_unavailable")
                        continue
                    certified = index.certified(generation, path, cached_only=True)
                    if not certified and certify_bytes and monotonic() < deadline:
                        size = path.stat().st_size
                        if size <= certify_bytes:
                            # ponytail: cap cold hashing per request. Large reopened indexes
                            # hydrate eagerly until build/replay certifies them.
                            certify_bytes -= size
                            certified = index.certified(generation, path, max_bytes=size)
                    snapshot = _ArchiveSnapshot(generation, path, certified)
                    try:
                        _, rows, partial = self._candidates(
                            state,
                            index,
                            scope,
                            values,
                            query.text,
                            query.mode,
                            deadline,
                            deep=True,
                            isolated=deep,
                            archive_precision=precision,
                            archive_snapshot=snapshot,
                        )
                    except IndexError:
                        reasons.append("source_index_unavailable")
                        continue
                    if index.source_pending(generation):
                        reasons.append("source_index_outbox_pending")
                    generations.append(str(generation.generation))
                    reasons.extend(partial)
                    for row, score, ranking in rows:
                        header = _archive_header(snapshot, row, score, ranking)
                        item = None
                        if not snapshot.certified:
                            item = _hydrate_archive(state, canonical, query, header, row)
                            if item is None:
                                continue
                        candidates.append((header, item))
                if not generations:
                    return None
                # Preserve ties by store UUID and local document ID, independent of SQL order.
                candidates.sort(
                    key=lambda pair: (
                        -pair[0].score,
                        pair[0].snapshot.generation.store_id,
                        pair[0].document_id,
                    )
                )
                results = _archive_results(state, canonical, query, candidates)
            state.check_policy(canonical, epoch)
            return ArchiveSearchResponse(
                results=results,
                coverage=RetrievalCoverage(
                    status="partial" if reasons else "complete",
                    index_status="available",
                    reason_codes=tuple(dict.fromkeys(reasons)),
                    extensions={
                        "ember.retrieval": {
                            "generations": ",".join(generations),
                            "policy_epoch": epoch,
                            "soft_deadline_ms": 500,
                            "mode": query.mode,
                            **(
                                {_ARCHIVE_RANKING_KEY: _ARCHIVE_PRECISION_PROFILE}
                                if precision
                                else {}
                            ),
                            "temporal": query.temporal.model_dump_json(),
                        }
                    },
                ),
            )
        except (
            IndexError,
            OSError,
            sqlite3.Error,
            SourceUnavailableError,
            AccessDeniedError,
            InvalidEvidenceError,
            NotFoundError,
            ValidationError,
            ValueError,
        ):
            if state.read_budget is not None:
                state.read_budget.check()
            self.last_failure = "archive_projection_or_policy_changed"
            return None
