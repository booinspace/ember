"""Canonical archive contracts preserve ambiguity without granting unsupported semantics."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from ember_memory_abi.archive import (
    ActorAttribution,
    BranchSelection,
    ContentPart,
    Conversation,
    GraphNode,
    Message,
    NormalizedArchiveBatch,
    OpaqueArchiveRecord,
    SourceLink,
    SourceRecord,
    validate_archive_write,
)
from ember_memory_abi.errors import UnsupportedWriteError
from ember_memory_abi.evidence import SourceRef, source_text_sha256

from .conftest import STAMP


def _reference(store: object, kind: str) -> SourceRef:
    return SourceRef(source_store_id=store, record_kind=kind, record_id=uuid4(), record_revision=1)


def test_graph_null_messages_and_missing_duplicate_links_survive(
    batch: NormalizedArchiveBatch,
) -> None:
    conversation = batch.records[0].source
    target = _reference(batch.source_store_id, "graph_node")
    node = GraphNode(
        source=_reference(batch.source_store_id, "graph_node"),
        recorded_at=STAMP,
        conversation=conversation,
        message=None,
        children=(
            SourceLink(status="resolved", target=target),
            SourceLink(status="resolved", target=target),
            SourceLink(status="missing", raw_reference_sha256="d" * 64),
        ),
    )
    restored = GraphNode.model_validate_json(node.model_dump_json())
    assert restored.message is None
    assert len(restored.children) == 3
    assert restored.children[0].target == restored.children[1].target
    assert restored.children[2].status == "missing"


def test_ambiguous_branch_selection_never_authorizes_activation(
    batch: NormalizedArchiveBatch,
) -> None:
    node = _reference(batch.source_store_id, "graph_node")
    selection = BranchSelection(
        status="ambiguous", selected_node=node, reason_codes=("missing_parent",)
    )
    conversation = Conversation(
        source=batch.records[0].source, recorded_at=STAMP, selection=selection
    )
    assert conversation.selection.branch_dependent_activation_blocked
    with pytest.raises(ValidationError):
        BranchSelection(status="resolved", selected_node=node, path=())
    with pytest.raises(ValidationError):
        BranchSelection(status="ambiguous")


def test_archive_relationships_cannot_silently_cross_source_stores(
    batch: NormalizedArchiveBatch,
) -> None:
    with pytest.raises(ValidationError):
        GraphNode(
            source=_reference(batch.source_store_id, "graph_node"),
            recorded_at=STAMP,
            conversation=_reference(uuid4(), "conversation"),
        )


@pytest.mark.parametrize("kind", ["analysis", "reasoning", "thoughts"])
def test_reasoning_content_can_only_be_an_excluded_record_without_a_body(kind: str) -> None:
    with pytest.raises(ValidationError):
        ContentPart(
            part_id=uuid4(),
            kind=kind,
            status="available",
            text="SYNTHETIC_SENTINEL",
            content_sha256=source_text_sha256("SYNTHETIC_SENTINEL"),
        )
    part = ContentPart(
        part_id=uuid4(), kind=kind, status="excluded", exclusion_reason="hidden_reasoning"
    )
    assert part.text is None
    assert "SENTINEL" not in part.model_dump_json()


def test_reasoning_channel_cannot_hide_visible_text_behind_a_text_content_type(
    batch: NormalizedArchiveBatch,
) -> None:
    part = ContentPart(
        part_id=uuid4(),
        kind="text",
        status="available",
        text="SYNTHETIC_SENTINEL",
        content_sha256=source_text_sha256("SYNTHETIC_SENTINEL"),
    )
    with pytest.raises(ValidationError):
        Message(
            source=_reference(batch.source_store_id, "message"),
            recorded_at=STAMP,
            conversation=batch.records[0].source,
            actor=ActorAttribution(role="assistant"),
            channel="analysis",
            parts=(part,),
        )


def test_available_text_requires_its_exact_source_hash() -> None:
    with pytest.raises(ValidationError):
        ContentPart(
            part_id=uuid4(),
            kind="text",
            status="available",
            text="synthetic",
            content_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    "change",
    ["duplicate_revision", "wrong_store", "empty_records", "missing_cursor", "missing_key"],
)
def test_batch_requires_bounded_unique_store_revisions_and_atomic_resume_metadata(
    batch: NormalizedArchiveBatch, change: str
) -> None:
    values = batch.model_dump()
    if change == "duplicate_revision":
        values["records"] = (batch.records[0], batch.records[0])
    elif change == "wrong_store":
        values["source_store_id"] = uuid4()
        values["records"] = batch.records
    elif change == "empty_records":
        values["records"] = ()
    else:
        values.pop("resume_cursor" if change == "missing_cursor" else "idempotency_key")
    with pytest.raises(ValidationError):
        NormalizedArchiveBatch.model_validate(values)


def test_archive_batch_json_roundtrip_leaves_unknown_source_times_null(
    batch: NormalizedArchiveBatch,
) -> None:
    restored = NormalizedArchiveBatch.model_validate_json(batch.model_dump_json())
    assert restored == batch
    assert restored.records[0].created_time.value is None
    assert restored.records[0].recorded_at == STAMP
    validate_archive_write(restored)


def test_opaque_source_kinds_are_readable_but_not_supported_writes(
    batch: NormalizedArchiveBatch,
) -> None:
    record = OpaqueArchiveRecord(
        source=_reference(batch.source_store_id, "future_document_kind"),
        recorded_at=STAMP,
        reason_codes=("unsupported_kind",),
    )
    restored = TypeAdapter(SourceRecord).validate_json(record.model_dump_json())
    assert isinstance(restored, OpaqueArchiveRecord)
    values = {**batch.model_dump(), "records": (record,)}
    with pytest.raises(UnsupportedWriteError):
        validate_archive_write(NormalizedArchiveBatch.model_validate(values))


def test_unknown_available_content_is_not_promoted_to_supported_archive_semantics(
    batch: NormalizedArchiveBatch,
) -> None:
    record = Message(
        source=_reference(batch.source_store_id, "message"),
        recorded_at=STAMP,
        conversation=batch.records[0].source,
        actor=ActorAttribution(role="tool"),
        parts=(
            ContentPart(
                part_id=uuid4(),
                kind="future_content",
                status="available",
                text="synthetic",
                content_sha256=source_text_sha256("synthetic"),
            ),
        ),
    )
    with pytest.raises(UnsupportedWriteError):
        validate_archive_write(
            NormalizedArchiveBatch.model_validate({**batch.model_dump(), "records": (record,)})
        )


def test_unknown_metadata_roundtrips_but_requires_write_extension_support(
    batch: NormalizedArchiveBatch,
) -> None:
    values = {
        **batch.model_dump(),
        "records": batch.records,
        "extensions": {"example.adapter": {"unknown_source_field": None}},
    }
    extended = NormalizedArchiveBatch.model_validate(values)
    assert NormalizedArchiveBatch.model_validate_json(extended.model_dump_json()) == extended
    with pytest.raises(UnsupportedWriteError):
        validate_archive_write(extended)
    validate_archive_write(extended, supported_extension_names=frozenset({"example.adapter"}))


def test_record_relationships_cannot_point_at_content_parts(batch: NormalizedArchiveBatch) -> None:
    partial_conversation = SourceRef.model_validate(
        {**batch.records[0].source.model_dump(), "part_id": uuid4()}
    )
    with pytest.raises(ValidationError):
        GraphNode(
            source=_reference(batch.source_store_id, "graph_node"),
            recorded_at=STAMP,
            conversation=partial_conversation,
        )
