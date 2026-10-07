"""Public-format hypotheses tested with original, entirely synthetic source data."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from ember_memory_importers.base.archive import (
    ArchiveInspectionError,
    inspect_directory,
    inspect_zip,
)
from ember_memory_importers.chatgpt.reconnaissance import ConversationObserver
from ember_memory_importers.chatgpt.source_models import SourceConversation
from ember_memory_importers.chatgpt.streaming import (
    ConversationJsonError,
    inspect_conversation_stream,
)
from ember_memory_importers.report_models import ConversationStructure, InspectionLimits

FIXTURE = Path(__file__).parent / "fixtures/conversations.synthetic.json"


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as writer:
        for name, payload in members:
            writer.writestr(name, payload)
    return output.getvalue()


def _archive(tmp_path: Path, members: list[tuple[str, bytes]]) -> Path:
    path = tmp_path / "source.zip"
    path.write_bytes(_zip(members))
    return path


def _conversation() -> dict[str, object]:
    return {
        "id": "SYNTHETIC_EXTERNAL_ID",
        "title": "PRIVATE_TITLE_SENTINEL",
        "create_time": None,
        "current_node": "answer",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["answer"], "message": None},
            "answer": {
                "id": "answer",
                "parent": "root",
                "children": [],
                "message": {
                    "id": "message",
                    "author": {"role": "assistant"},
                    "content": {"content_type": "code", "text": "VISIBLE_TEXT_SENTINEL"},
                    "metadata": {"model_slug": "SYNTHETIC_MODEL"},
                },
            },
        },
    }


@pytest.mark.parametrize(
    "wrapper", ["array", "object_with_conversations_array", "single_conversation"]
)
def test_streamed_envelopes_and_code_text_are_content_free(wrapper: str) -> None:
    conversation = _conversation()
    value = (
        [conversation]
        if wrapper == "array"
        else {"conversations": [conversation], "new_field": None}
        if wrapper == "object_with_conversations_array"
        else conversation
    )
    inspected = inspect_conversation_stream(
        io.BytesIO(json.dumps(value).encode()), InspectionLimits()
    )
    structure = inspected.structure
    assert isinstance(structure, ConversationStructure)
    assert structure.wrapper == wrapper
    assert structure.counts["messages_with_text_field"] == 1
    assert structure.automatic_branch_activation == "not_evaluated"
    assert "SENTINEL" not in structure.model_dump_json()
    assert "SYNTHETIC_EXTERNAL_ID" not in structure.model_dump_json()


def test_streaming_component_exceeds_whole_json_limit_but_not_record_limit(tmp_path: Path) -> None:
    payload = json.dumps([_conversation()] * 5000).encode()
    source = _archive(tmp_path, [("conversations-000.json", payload)])
    report = inspect_zip(
        source,
        source_namespace="synthetic",
        limits=InspectionLimits(max_json_bytes=1, max_conversation_bytes=1024),
    )
    structure = report.chatgpt.conversation_components[0].structure
    assert isinstance(structure, ConversationStructure)
    assert structure.counts["conversations"] == 5000
    assert report.inventory[0].content_sha256 == hashlib.sha256(payload).hexdigest()
    assert report.inventory[0].json_status == "structurally_inspected"


@pytest.mark.parametrize(
    "payload",
    [
        b'[{}, {"mapping": {}, "mapping": {}}]',
        b'{"conversations": [], "conversations": []}',
        b'[{}, {"metadata": {"key": 1, "key": 2}}]',
        b"[{}] trailing",
        b"[{},",
        b'[{"create_time": 1e999}]',
    ],
)
def test_late_invalidity_discards_all_partial_counts(tmp_path: Path, payload: bytes) -> None:
    source = _archive(tmp_path, [("conversations-001.json", payload)])
    report = inspect_zip(source, source_namespace="synthetic")
    assert report.inventory[0].json_status == "invalid_or_ambiguous_json"
    assert report.chatgpt.conversation_components == ()
    assert report.inventory[0].content_sha256 == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "limits,payload",
    [
        (
            InspectionLimits(max_conversation_bytes=64),
            json.dumps([{"text": "x" * 200000}]).encode(),
        ),
        (InspectionLimits(max_json_depth=2), b'[{"x": [[[1]]]}]'),
        (InspectionLimits(max_json_events=10), b"[[1,2,3,4,5,6,7,8,9,10,11]]"),
    ],
    ids=["bytes", "depth", "events"],
)
def test_record_resource_bounds_are_explicit_coverage_gaps(
    tmp_path: Path, limits: InspectionLimits, payload: bytes
) -> None:
    source = _archive(tmp_path, [("conversations.json", payload)])
    report = inspect_zip(source, source_namespace="synthetic", limits=limits)
    assert report.inventory[0].json_status == "deferred_size_limit"
    assert report.chatgpt.conversation_components == ()
    assert report.warnings


def test_nested_shards_have_unique_identity_and_shared_asset_inventory(tmp_path: Path) -> None:
    nested = _zip(
        [("conversations-000.json", FIXTURE.read_bytes()), ("personal/file.dat", b"asset")]
    )
    source = _archive(tmp_path, [("Conversations-part-001.zip", nested), ("part-002.zip", nested)])
    original = source.read_bytes()
    report = inspect_zip(source, source_namespace="synthetic")
    assert len(report.chatgpt.conversation_components) == 2
    assert len({component.path_sha256 for component in report.chatgpt.conversation_components}) == 2
    assert all(
        component.container_path_sha256 for component in report.chatgpt.conversation_components
    )
    assert sum(entry.component == "asset_candidate" for entry in report.inventory) == 2
    assert report.source.files == 6
    assert report.source.uncompressed_bytes == 2 * (len(nested) + FIXTURE.stat().st_size + 5)
    assert source.read_bytes() == original
    assert list(tmp_path.iterdir()) == [source]
    assert "SENTINEL" not in report.model_dump_json()


@pytest.mark.parametrize("limit", ["members", "bytes"])
def test_nested_containers_cannot_reset_global_resource_limits(tmp_path: Path, limit: str) -> None:
    nested = _zip([("one.txt", b"one"), ("two.txt", b"two")])
    source = _archive(tmp_path, [("first.zip", nested), ("second.zip", nested)])
    limits = (
        InspectionLimits(max_members=5)
        if limit == "members"
        else InspectionLimits(max_total_bytes=2 * len(nested) + 5)
    )
    with pytest.raises(ArchiveInspectionError, match="limit"):
        inspect_zip(source, source_namespace="synthetic", limits=limits)


def test_nested_depth_gap_is_not_claimed_as_complete(tmp_path: Path) -> None:
    source = _archive(tmp_path, [("part.zip", _zip([("conversations.json", b"[]")]))])
    report = inspect_zip(
        source, source_namespace="synthetic", limits=InspectionLimits(max_nested_depth=0)
    )
    assert report.inventory[0].container_status == "deferred_depth_limit"
    assert report.chatgpt.conversation_components == ()
    assert report.warnings


@pytest.mark.parametrize("nested", [b"not a zip", _zip([("../PRIVATE_PATH_SENTINEL.txt", b"x")])])
def test_invalid_or_unsafe_nested_container_fails_privately(tmp_path: Path, nested: bytes) -> None:
    source = _archive(tmp_path, [("PRIVATE_CONTAINER_SENTINEL.zip", nested)])
    with pytest.raises(ArchiveInspectionError) as raised:
        inspect_zip(source, source_namespace="synthetic")
    assert "SENTINEL" not in str(raised.value)


def test_directory_and_flat_zip_have_equivalent_logical_inventory(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "conversations_001.json").write_bytes(FIXTURE.read_bytes())
    (directory / "file.dat").write_bytes(b"asset")
    source = _archive(
        tmp_path, [("file.dat", b"asset"), ("conversations_001.json", FIXTURE.read_bytes())]
    )
    report = inspect_directory(directory, source_namespace="synthetic")
    zipped = inspect_zip(source, source_namespace="synthetic")
    assert report.source.kind == "directory"
    assert report.source.raw_sha256 is None
    assert report.source.logical_inventory_sha256 == zipped.source.logical_inventory_sha256
    assert report.chatgpt == zipped.chatgpt


def test_directory_symlink_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    target = tmp_path / "outside.txt"
    target.write_bytes(b"private")
    try:
        (directory / "link.txt").symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks requires platform permission")
    with pytest.raises(ArchiveInspectionError):
        inspect_directory(directory, source_namespace="synthetic")


def test_source_models_keep_unknowns_absence_null_and_all_branches() -> None:
    source = _conversation()
    source["future_metadata"] = {"unknown": [False, None, 42]}
    model = SourceConversation.model_validate(source)
    assert model.model_dump(exclude_unset=True) == source
    assert "create_time" in model.model_fields_set
    assert "update_time" not in model.model_fields_set
    assert model.create_time is None and model.update_time is None
    graph = SourceConversation.model_validate(json.loads(FIXTURE.read_bytes())[0])
    assert len(graph.mapping) == 5
    assert any(len(node.children or []) > 1 for node in graph.mapping.values())
    assert any(node.message is None for node in graph.mapping.values())


@pytest.mark.parametrize("unknown", [object(), float("nan"), float("inf")])
def test_source_models_narrow_unknown_values_to_finite_json(unknown: object) -> None:
    source = _conversation()
    source["future_field"] = unknown
    with pytest.raises(ValidationError):
        SourceConversation.model_validate(source)


def test_utf8_bom_and_arbitrary_precision_integer_do_not_change_shapes() -> None:
    payload = b'\xef\xbb\xbf[{"mapping": {}, "create_time": ' + b"9" * 400 + b"}]"
    result = inspect_conversation_stream(io.BytesIO(payload), InspectionLimits())
    assert isinstance(result.structure, ConversationStructure)
    assert (
        result.structure.timestamp_representations[
            "conversation.create_time:numeric_unix_candidate"
        ]
        == 1
    )


def test_streaming_error_messages_never_include_private_input() -> None:
    with pytest.raises(ConversationJsonError) as raised:
        inspect_conversation_stream(
            io.BytesIO(b'["PRIVATE_TEXT_SENTINEL", invalid]'), InspectionLimits()
        )
    assert "SENTINEL" not in str(raised.value)


def test_distinct_unknown_fields_cannot_grow_report_counters_without_bound() -> None:
    observer = ConversationObserver()
    for number in range(1100):
        observer.observe({"mapping": {}, "current_node": None, f"private-{number}": None})
    structure = observer.finish("array")
    assert len(structure.field_shapes["conversation"]) <= 1024
    assert structure.field_shapes["conversation"]["other_unknown_labels"] > 0
    assert "private-" not in structure.model_dump_json()
