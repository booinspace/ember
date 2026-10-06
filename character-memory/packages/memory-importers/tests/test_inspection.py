"""Synthetic importer tests; these do not establish actual-export capabilities."""

import hashlib
import stat
import warnings
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError

from character_memory_importers.base.archive import ArchiveInspectionError, inspect_zip
from character_memory_importers.chatgpt.reconnaissance import inspect_conversations
from character_memory_importers.report_models import (
    ConversationStructure,
    InspectionLimits,
    ReconnaissanceReport,
    UnsupportedConversationStructure,
)

FIXTURE = Path(__file__).parent / "fixtures/conversations.synthetic.json"


def _write_zip(
    path: Path, members: Sequence[tuple[str | zipfile.ZipInfo, bytes]] | None = None
) -> None:
    # Duplicate-path tests intentionally trigger zipfile's duplicate-name warning.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members or [("conversations.json", FIXTURE.read_bytes())]:
                archive.writestr(name, payload)


@pytest.fixture
def source_archive(tmp_path: Path) -> Path:
    """Provide one synthetic archive per test, isolated from personal sources."""
    path = tmp_path / "source.zip"
    _write_zip(path)
    return path


def _fixture_json() -> JsonValue:
    adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
    return adapter.validate_json(FIXTURE.read_bytes())


def _structure(report: ReconnaissanceReport) -> ConversationStructure:
    structure = report.chatgpt.conversation_components[0].structure
    assert isinstance(structure, ConversationStructure)
    return structure


def test_report_excludes_content_identifiers_reasoning_and_unknown_keys(
    source_archive: Path,
) -> None:
    _write_zip(
        source_archive,
        [
            ("conversations.json", FIXTURE.read_bytes()),
            ("PRIVATE_FILENAME_SENTINEL.png", b"fake-image"),
        ],
    )
    original = source_archive.read_bytes()
    report = inspect_zip(source_archive, source_namespace="synthetic-account")
    encoded = report.model_dump_json(exclude_none=True)
    assert "SENTINEL" not in encoded
    assert "synthetic-conversation" not in encoded
    assert "relative_path" not in encoded
    assert report.source.raw_sha256 == hashlib.sha256(original).hexdigest()
    assert source_archive.read_bytes() == original
    assert sorted(path.name for path in source_archive.parent.iterdir()) == ["source.zip"]
    structure = _structure(report)
    assert structure.counts["nodes"] == 5
    assert structure.counts["branch_points"] == 1
    assert structure.counts["reasoning_messages_observed_excluded"] == 1
    assert structure.non_reasoning_part_types == {"object": 1, "string": 3}
    assert report.chatgpt.saved_memories == "not_established"
    assert ReconnaissanceReport.model_validate_json(encoded) == report


def test_logical_hash_is_independent_of_zip_order(source_archive: Path) -> None:
    members = [("b.txt", b"beta"), ("a.txt", b"alpha")]
    _write_zip(source_archive, members)
    first = inspect_zip(
        source_archive, source_namespace="synthetic"
    ).source.logical_inventory_sha256
    _write_zip(source_archive, list(reversed(members)))
    assert (
        first
        == inspect_zip(source_archive, source_namespace="synthetic").source.logical_inventory_sha256
    )


def test_member_paths_require_explicit_opt_in(source_archive: Path) -> None:
    report = inspect_zip(source_archive, source_namespace="synthetic", include_member_paths=True)
    assert report.inventory[0].relative_path == "conversations.json"
    assert report.privacy.member_paths == "included_sensitive"


@pytest.mark.parametrize(
    "member_path",
    [
        "../SECRET.txt",
        "/SECRET.txt",
        "C:/SECRET.txt",
        "C:SECRET.txt",
        "..\\SECRET.txt",
        "\\\\host\\SECRET.txt",
        "a/../SECRET.txt",
        "a/SECRET.txt:stream",
    ],
)
def test_unsafe_paths_are_rejected_without_disclosure(
    source_archive: Path, member_path: str
) -> None:
    _write_zip(source_archive, [(member_path, b"payload")])
    with pytest.raises(ArchiveInspectionError) as raised:
        inspect_zip(source_archive, source_namespace="synthetic")
    assert "SECRET" not in str(raised.value)


def test_symlinks_are_rejected(source_archive: Path) -> None:
    information = zipfile.ZipInfo("link")
    information.create_system = 3
    information.external_attr = (stat.S_IFLNK | 0o777) << 16
    _write_zip(source_archive, [(information, b"target")])
    with pytest.raises(ArchiveInspectionError, match="symlinks"):
        inspect_zip(source_archive, source_namespace="synthetic")


@pytest.mark.parametrize("names", [("same", "same"), ("A.txt", "a.txt")])
def test_duplicate_and_case_ambiguous_members_are_rejected(
    source_archive: Path, names: tuple[str, str]
) -> None:
    _write_zip(source_archive, [(names[0], b"a"), (names[1], b"b")])
    with pytest.raises(ArchiveInspectionError, match="duplicate or case-ambiguous"):
        inspect_zip(source_archive, source_namespace="synthetic")


@pytest.mark.parametrize(
    "limits",
    [
        InspectionLimits(max_members=1),
        InspectionLimits(max_member_bytes=50),
        InspectionLimits(max_total_bytes=150),
        InspectionLimits(max_compression_ratio=1),
    ],
)
def test_resource_limits_are_enforced(source_archive: Path, limits: InspectionLimits) -> None:
    _write_zip(source_archive, [("a.txt", b"x" * 100), ("b.txt", b"y" * 100)])
    with pytest.raises(ArchiveInspectionError, match="limit"):
        inspect_zip(source_archive, source_namespace="synthetic", limits=limits)


def test_large_json_is_hashed_and_explicitly_deferred(source_archive: Path) -> None:
    report = inspect_zip(
        source_archive,
        source_namespace="synthetic",
        limits=InspectionLimits(max_json_bytes=1),
    )
    assert report.inventory[0].json_status == "deferred_size_limit"
    assert report.chatgpt.conversation_components == ()
    assert report.warnings


@pytest.mark.parametrize(
    "payload",
    [
        b'{"mapping": {}, "mapping": {}}',
        b"[NaN]",
        b"[Infinity]",
        b"[1e999]",
        b"[broken",
        b"\xff",
    ],
)
def test_invalid_duplicate_and_nonfinite_json_produce_coverage_outcomes(
    source_archive: Path, payload: bytes
) -> None:
    _write_zip(source_archive, [("conversations.json", payload)])
    report = inspect_zip(source_archive, source_namespace="synthetic")
    assert report.inventory[0].json_status == "invalid_or_ambiguous_json"
    assert report.warnings


def test_corrupt_zip_is_a_chained_safe_domain_error(source_archive: Path) -> None:
    source_archive.write_bytes(b"not a zip")
    with pytest.raises(ArchiveInspectionError) as raised:
        inspect_zip(source_archive, source_namespace="synthetic")
    assert raised.value.__cause__ is not None
    assert str(source_archive) not in str(raised.value)


def test_empty_namespace_is_rejected(source_archive: Path) -> None:
    with pytest.raises(ArchiveInspectionError, match="namespace"):
        inspect_zip(source_archive, source_namespace="")


def test_graph_anomalies_block_automatic_branch_activation() -> None:
    data = _fixture_json()
    assert isinstance(data, list) and isinstance(data[0], dict)
    conversation = data[0]
    mapping = conversation["mapping"]
    assert isinstance(mapping, dict)
    root, question = mapping["root"], mapping["question"]
    assert isinstance(root, dict) and isinstance(question, dict)
    children = question["children"]
    assert isinstance(children, list)
    conversation["current_node"] = "absent"
    root["parent"] = "question"
    children.append("missing")
    result = inspect_conversations(data)
    assert isinstance(result, ConversationStructure)
    assert result.automatic_branch_activation == "blocked"
    assert result.counts["parent_cycles"] == 1
    assert result.counts["missing_child_references"] == 1


def test_unknown_content_and_channel_values_are_hashed() -> None:
    value: JsonValue = [
        {
            "mapping": {
                "node": {
                    "parent": None,
                    "children": [],
                    "message": {
                        "channel": "PRIVATE_UNKNOWN_CHANNEL",
                        "content": {"content_type": "PRIVATE_UNKNOWN_CONTENT", "parts": []},
                    },
                }
            },
            "current_node": "node",
        }
    ]
    encoded = inspect_conversations(value).model_dump_json()
    assert "PRIVATE_UNKNOWN" not in encoded
    assert "unknown_sha256:" in encoded


def test_malformed_value_types_produce_counts_without_disclosing_values() -> None:
    value: JsonValue = [
        {
            "mapping": {
                "node": {
                    "parent": None,
                    "children": [],
                    "message": {
                        "channel": {"private": "secret"},
                        "content": {"content_type": ["secret"]},
                    },
                }
            },
            "current_node": "node",
        }
    ]
    result = inspect_conversations(value)
    assert isinstance(result, ConversationStructure)
    assert "value_type:object" in result.channels
    assert "secret" not in result.model_dump_json()
    assert isinstance(inspect_conversations({}), UnsupportedConversationStructure)


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (10**400, "numeric_unix_candidate"),
        (True, "unsupported_type:boolean"),
    ],
)
def test_timestamp_representation_handles_large_integers_and_booleans(
    timestamp: int | bool, expected: str
) -> None:
    result = inspect_conversations([{"create_time": timestamp}])
    assert isinstance(result, ConversationStructure)
    assert result.timestamp_representations["conversation.create_time:" + expected] == 1


@pytest.mark.parametrize("value", [0, -1, True, "10"])
def test_report_resource_bounds_reject_invalid_or_coerced_values(value: object) -> None:
    with pytest.raises(ValidationError):
        InspectionLimits.model_validate({"max_json_bytes": value})
