"""Isolated native Ember archive ingestion and retrieval for public AMB data."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import batched
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

from ember_memory_abi.archive import (
    ContentPart,
    Document,
    ImportCursor,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.common import canonical_object_sha256, timestamp_from_datetime
from ember_memory_abi.evidence import SourceRef, source_text_sha256
from ember_memory_abi.interfaces import ArchiveQuery
from ember_memory_abi.memory import ArchiveChunk
from ember_memory_abi.temporal import SourceTimestamp
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.models import SourceGrant, SourceKey
from ember_memory_engine.search_contracts import Encoder

ADAPTER = "ember.amb-public"
SPEC = ProcessingSpecRef(
    name=ADAPTER,
    version="0.1.0",
    specification_sha256=canonical_object_sha256({"adapter": ADAPTER, "layout": "document-v1"}),
)

type SearchMode = Literal["lexical", "hybrid"]


@dataclass(frozen=True)
class InputDocument:
    """Only ordinary ingest fields; no query answers, labels, or scoring hints."""

    id: str
    content: str
    user_id: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class Retrieval:
    """Ranked distinct AMB IDs from exact native provenance, with measured coverage."""

    ids: tuple[str, ...]
    latency_ms: float
    coverage: str
    reasons: tuple[str, ...] = ()


def _time(value: str | None) -> SourceTimestamp:
    if value is None:
        return SourceTimestamp()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return SourceTimestamp(raw_value=value, ambiguity_reasons=("unparsed_public_timestamp",))
    if parsed.tzinfo is None:
        return SourceTimestamp(raw_value=value, ambiguity_reasons=("unknown_source_timezone",))
    return SourceTimestamp(value=timestamp_from_datetime(parsed), raw_value=value, basis="source")


def ingest(
    engine: CanonicalEngine, documents: Sequence[InputDocument], namespace: str, inventory: str
) -> tuple[UUID, dict[UUID, str], UUID]:
    """Write a fresh library for one AMB isolation unit; return exact source ID crosswalk."""
    library = engine.create_library(namespace)
    writer = engine.archive(library.store_id)
    index = 0
    references: list[SourceRef] = []
    crosswalk: dict[UUID, str] = {}
    # AMB can repeat public IDs with different source rows. Each original row gets an
    # independent native identity; only the eventual benchmark rank collapses its AMB ID.
    for batch in batched(documents, 64):
        keys = writer.prepare_keys(
            tuple(
                SourceKey(
                    adapter=ADAPTER,
                    record_kind="document",
                    external_id=f"{index + offset}:{doc.id}",
                )
                for offset, doc in enumerate(batch)
            )
        )
        records: list[Document] = []
        for source_key, item in zip(keys, batch, strict=True):
            source = SourceRef(
                source_store_id=library.store_id,
                record_kind="document",
                record_id=source_key.record_id,
                record_revision=1,
            )
            records.append(
                Document(
                    source=source,
                    recorded_at=timestamp_from_datetime(datetime.now(UTC)),
                    created_time=_time(item.timestamp),
                    parts=(
                        ContentPart(
                            part_id=uuid4(),
                            kind="text",
                            status="available",
                            text=item.content,
                            content_sha256=source_text_sha256(item.content),
                        ),
                    ),
                )
            )
            references.append(source)
            crosswalk[source.record_id] = item.id
        index += len(batch)
        writer.write_batch(
            NormalizedArchiveBatch(
                source_store_id=library.store_id,
                source_namespace=library.namespace,
                idempotency_key=f"{inventory}:{index}",
                processing_spec=SPEC,
                records=tuple(records),
                resume_cursor=ImportCursor(
                    logical_inventory_sha256=inventory,
                    component_sha256=inventory,
                    next_record_index=index,
                    adapter=ADAPTER,
                    adapter_version=SPEC.version,
                ),
            ),
            keys=keys,
        )
    character = engine.create_character().store_id
    for grant in batched(references, 1000):
        engine.grant_source(
            character, SourceGrant(references=grant, capabilities=frozenset({"archive_search"}))
        )
    return library.store_id, crosswalk, character


class ArchiveBenchmark:
    """Run Ember only in an explicitly fresh scratch root; never touch a deployed store."""

    def __init__(
        self,
        root: Path,
        encoder: Encoder,
        mode: SearchMode,
        limit: int,
    ) -> None:
        self.engine = CanonicalEngine(root, encoder=encoder)
        self.encoder = encoder
        self.mode: SearchMode = mode
        self.limit = limit
        self.units: dict[str | None, tuple[UUID, dict[UUID, str], UUID]] = {}
        self.index_documents = 0
        self.index_vectors = 0

    def close(self) -> None:
        """Drain native index and query workers."""
        self.engine.close()

    def add(self, user: str | None, documents: Sequence[InputDocument], inventory: str) -> None:
        """Import and explicitly rebuild one private unit's native source index."""
        if user in self.units or any(item.user_id != user for item in documents):
            raise ValueError("Invalid AMB isolation unit.")
        unit = ingest(self.engine, documents, f"amb-unit-{len(self.units)}", inventory)
        generation = self.engine.rebuild_index(unit[0], self.encoder)
        self.index_documents += generation.documents
        self.index_vectors += generation.vectors
        self.units[user] = unit

    def retrieve(self, query: str, user: str | None, budget: int | None = None) -> Retrieval:
        """Run one native search; deduplicate chunk IDs to original document IDs by first hit."""
        started = perf_counter()
        limit = min(self.limit, budget) if budget is not None else self.limit
        if not query.strip() or limit == 0:
            return Retrieval((), 0.0, "skipped")
        unit = self.units.get(user)
        if unit is None:
            return Retrieval((), 0.0, "no_unit")
        source, crosswalk, character = unit
        request = ArchiveQuery(text=query, mode=self.mode, source_store_ids=(source,), limit=limit)
        if self.mode == "hybrid":
            # Deep archive accepts only admitted query vectors. Count ordinary CPU encoding
            # here rather than prewarming on benchmark questions or changing service policy.
            self.engine.encoding.query(query).result()
        response = self.engine.character(character).search_archive(request)
        ids: dict[str, None] = {}
        for hit in response.results:
            record = hit.record
            references = (
                tuple(span.evidence.source for span in record.source_spans)
                if isinstance(record, ArchiveChunk)
                else (record.source,)
            )
            for ref in references:
                if ref.source_store_id != source:
                    raise ValueError("Native archive returned a source outside the requested unit.")
                ids[crosswalk[ref.record_id]] = None
        return Retrieval(
            tuple(ids)[:limit],
            (perf_counter() - started) * 1000,
            response.coverage.status,
            response.coverage.reason_codes,
        )
