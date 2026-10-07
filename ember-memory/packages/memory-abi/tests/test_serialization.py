"""Conformance for portable identity, finite serialization, time, and extension semantics."""

import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import NAMESPACE_DNS, uuid5

import pytest
from pydantic import TypeAdapter, ValidationError

from ember_memory_abi.common import (
    ContractModel,
    UtcTimestamp,
    canonical_json_bytes,
    canonical_object_sha256,
    timestamp_from_datetime,
    timestamp_from_unix_microseconds,
    timestamp_to_unix_microseconds,
)
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.temporal import SourceTimestamp, TemporalBounds


@pytest.mark.parametrize("field", ["source_store_id", "record_id", "part_id"])
def test_canonical_identity_rejects_non_uuid4(source: SourceRef, field: str) -> None:
    values = source.model_dump()
    values[field] = uuid5(NAMESPACE_DNS, "synthetic")
    with pytest.raises(ValidationError):
        SourceRef.model_validate(values)


@pytest.mark.parametrize("revision", [0, -1, True, "1", 1.5])
def test_revision_requires_a_strict_positive_integer(source: SourceRef, revision: object) -> None:
    values = source.model_dump()
    values["record_revision"] = revision
    with pytest.raises(ValidationError):
        SourceRef.model_validate(values)


@pytest.mark.parametrize("field", ["path", "sqlite_row_id", "chatgpt_id", "vector_key"])
def test_source_ref_does_not_accept_physical_or_provider_identity(
    source: SourceRef, field: str
) -> None:
    with pytest.raises(ValidationError):
        SourceRef.model_validate({**source.model_dump(), field: "PRIVATE_SENTINEL"})


def test_source_ref_wire_roundtrip_preserves_unknown_namespaced_extensions(
    source: SourceRef,
) -> None:
    values = source.model_dump()
    values["record_kind"] = "future_record_kind"
    values["extensions"] = {"example.future": {"nested": [1, False, None, "☃"]}}
    model = SourceRef.model_validate(values)
    encoded = model.model_dump_json()
    assert SourceRef.model_validate_json(encoded) == model
    assert json.loads(encoded)["record_id"] == str(model.record_id).lower()
    assert model.record_kind == "future_record_kind"


@pytest.mark.parametrize("key", ["not_namespaced", "../path", "UPPER.Name", "name."])
def test_extensions_require_explicit_namespace(key: str) -> None:
    with pytest.raises(ValidationError):
        ContractModel(extensions={key: None})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_nested_values_fail_contracts_and_object_hashing(value: float) -> None:
    with pytest.raises(ValidationError):
        ContractModel(extensions={"example.data": {"score": value}})
    with pytest.raises((ValidationError, ValueError)):
        canonical_json_bytes({"score": value})


def test_canonical_hash_ignores_object_order_but_preserves_arrays_and_exact_unicode() -> None:
    first = {"z": [1, 2], "é": "e\u0301", "a": None}
    second = {"a": None, "é": "e\u0301", "z": [1, 2]}
    assert canonical_json_bytes(first) == b'{"a":null,"z":[1,2],"\xc3\xa9":"e\xcc\x81"}'
    assert canonical_object_sha256(first) == canonical_object_sha256(second)
    assert canonical_object_sha256(first) != canonical_object_sha256({**first, "z": [2, 1]})
    assert canonical_object_sha256(first) != canonical_object_sha256({**first, "é": "é"})


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-07T12:00:00Z",
        "2026-10-07T12:00:00.123Z",
        "2026-10-07T12:00:00.000000+00:00",
        "2026-02-30T12:00:00.000000Z",
        "2026-10-07T24:00:00.000000Z",
    ],
)
def test_canonical_timestamps_reject_ambiguous_or_invalid_forms(timestamp: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(UtcTimestamp).validate_python(timestamp)


@pytest.mark.parametrize("microseconds", [-1, 0, 1, -123456789012345, 1791376496000001])
def test_integer_time_roundtrips_without_float_precision_loss(microseconds: int) -> None:
    assert (
        timestamp_to_unix_microseconds(timestamp_from_unix_microseconds(microseconds))
        == microseconds
    )


def test_aware_time_converts_to_utc_and_naive_time_is_not_assigned_a_timezone() -> None:
    instant = datetime(2026, 10, 7, 13, 0, 0, 123456, tzinfo=timezone(timedelta(hours=1)))
    assert timestamp_from_datetime(instant) == "2026-10-07T12:00:00.123456Z"
    assert timestamp_from_datetime(datetime(1, 1, 1, tzinfo=UTC)).startswith("0001-")
    with pytest.raises(ValueError):
        timestamp_from_datetime(instant.replace(tzinfo=None))


def test_unknown_source_time_and_raw_ambiguity_survive_without_import_time_fallback() -> None:
    value = SourceTimestamp(
        raw_value="unclear local date",
        timezone_assumption=None,
        ambiguity_reasons=("timezone_unknown",),
    )
    assert value.value is None
    assert SourceTimestamp.model_validate_json(value.model_dump_json()) == value
    assert SourceTimestamp().value is None


@pytest.mark.parametrize("end", ["2026-10-06T12:00:00.000000Z", "2026-10-07T12:00:00.000000Z"])
def test_known_validity_bounds_must_form_a_nonempty_half_open_interval(end: str) -> None:
    with pytest.raises(ValidationError):
        TemporalBounds(valid_from="2026-10-07T12:00:00.000000Z", valid_until=end)
