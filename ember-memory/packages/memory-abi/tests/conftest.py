"""Synthetic contract fixtures; none is derived from personal/export source data."""

from uuid import uuid4

import pytest

from ember_memory_abi.archive import (
    Conversation,
    ImportCursor,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.evidence import Evidence, SourceRef, make_evidence_span
from ember_memory_abi.memory import (
    EntityRef,
    FactPayload,
    MemoryContent,
    MemoryDraft,
    StringValue,
)

STAMP = "2026-10-07T12:34:56.000001Z"
TEXT = "Synthetic café 🍰 and e\u0301 evidence."


@pytest.fixture
def source() -> SourceRef:
    """Use canonical random UUID4 identities, never provider/SQLite identifiers."""
    return SourceRef(
        source_store_id=uuid4(),
        record_kind="message",
        record_id=uuid4(),
        record_revision=1,
        part_id=uuid4(),
    )


@pytest.fixture
def evidence(source: SourceRef) -> Evidence:
    """Quote a Unicode span containing an astral character and combining mark."""
    return Evidence(evidence_id=uuid4(), source=source, span=make_evidence_span(TEXT, 10, 22))


@pytest.fixture
def draft() -> MemoryDraft:
    """Propose a GPU fact without asserting activation or ownership replacement."""
    return MemoryDraft(
        content=MemoryContent(
            kind="fact",
            modality="FACTUAL_ASSERTION",
            confidence=0.95,
            importance=0.6,
            payload=FactPayload(
                subject=EntityRef(store_id=uuid4(), entity_id=uuid4()),
                predicate="hardware.primary_gpu",
                definition_version="0.1.0",
                value=StringValue(value="SYNTHETIC_GPU"),
            ),
        )
    )


@pytest.fixture
def batch() -> NormalizedArchiveBatch:
    """Provide a resumable source batch with unknown historical timestamps left null."""
    store = uuid4()
    return NormalizedArchiveBatch(
        source_store_id=store,
        source_namespace="synthetic-account",
        idempotency_key="synthetic-batch",
        processing_spec=ProcessingSpecRef(
            name="ember.test", version="0.1.0", specification_sha256="a" * 64
        ),
        records=(
            Conversation(
                source=SourceRef(
                    source_store_id=store,
                    record_kind="conversation",
                    record_id=uuid4(),
                    record_revision=1,
                ),
                recorded_at=STAMP,
            ),
        ),
        resume_cursor=ImportCursor(
            logical_inventory_sha256="b" * 64,
            component_sha256="c" * 64,
            next_record_index=1,
            adapter="synthetic",
            adapter_version="0.1.0",
        ),
    )
