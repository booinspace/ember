"""Shared backend-independent validation and serialization primitives for ABI 0.1."""

import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Annotated, cast

from pydantic import UUID4, AfterValidator, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

ABI_VERSION = "0.4.0"
ARCHIVE_VERSION = "0.1.0"
CANONICAL_JSON_SPEC = "ember.canonical-json@0.1.0"

type CanonicalId = UUID4
type Revision = Annotated[int, Field(gt=0)]
type NonnegativeInteger = Annotated[int, Field(ge=0)]
type PositiveInteger = Annotated[int, Field(gt=0)]
type Confidence = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
type FiniteScore = Annotated[float, Field(allow_inf_nan=False)]
type WireString = Annotated[str, Field(min_length=1, max_length=256)]
type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type ExtensionKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*[.:][a-z0-9_.:-]+$")]


def _valid_timestamp(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as error:
        raise ValueError("Invalid canonical UTC timestamp.") from error
    return value


type UtcTimestamp = Annotated[
    str,
    Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$",
        json_schema_extra={"format": "date-time"},
    ),
    AfterValidator(_valid_timestamp),
]


# Stateless schema compilation belongs to module initialization, not every source
# dependency/hash check. Validation/serialization bytes remain unchanged.
_TIMESTAMP_ADAPTER: TypeAdapter[str] = TypeAdapter(UtcTimestamp)
_JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue, config=ConfigDict(strict=True))


class ContractModel(BaseModel):
    """Strict typed contracts; unknown data belongs in namespaced extensions.

    Frozen fields prevent reassignment, not mutation of nested JSON containers.
    Persistence implementations must snapshot values and enforce revision immutability.
    No contract construction creates identities, timestamps, or approved semantics.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, allow_inf_nan=False)
    extensions: dict[ExtensionKey, JsonValue] = Field(default_factory=dict, repr=False)


def timestamp_from_datetime(value: datetime) -> str:
    """Serialize an aware instant as UTC with exactly six fractional digits.

    Naive datetimes fail rather than acquiring an inferred local timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone is required.")
    value = value.astimezone(UTC)
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}.{value.microsecond:06d}Z"
    )


def timestamp_to_unix_microseconds(value: str) -> int:
    """Convert a canonical instant to integer microseconds without float rounding."""
    validated = _TIMESTAMP_ADAPTER.validate_python(value)
    instant = datetime.strptime(validated, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    delta = instant - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def timestamp_from_unix_microseconds(value: int) -> str:
    """Convert integer storage time to the canonical wire representation, including pre-epoch."""
    if type(value) is not int:
        raise ValueError("Storage time must be an integer microsecond count.")
    return timestamp_from_datetime(datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value))


def _finite_json(value: JsonValue) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Canonical JSON requires finite numbers.")
    if isinstance(value, dict):
        for child in value.values():
            _finite_json(child)
    elif isinstance(value, list):
        for child in value:
            _finite_json(child)


def canonical_json_bytes(value: JsonValue | ContractModel) -> bytes:
    """Encode ember.canonical-json@0.1.0 for reproducible object hashes.

    Sort object keys by Unicode codepoint, preserve array order, use UTF-8 with
    literal Unicode, no whitespace, and Python 3.12 JSON number formatting. This
    algorithm is explicitly versioned; it is not RFC 8785. Do not use it to hash
    raw source text, whose hash must use its exact UTF-8 bytes without normalization.
    """
    raw = value.model_dump(mode="json") if isinstance(value, ContractModel) else value
    narrowed = _JSON_ADAPTER.validate_python(raw)
    _finite_json(narrowed)
    try:
        return json.dumps(
            narrowed, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except UnicodeError as error:
        raise ValueError("Canonical JSON requires valid Unicode.") from error


def canonical_object_sha256(value: JsonValue | ContractModel) -> str:
    """Hash the specified canonical JSON bytes; source text uses a separate exact hash."""
    return sha256(canonical_json_bytes(value)).hexdigest()


def unsupported_extension_names(model: ContractModel, supported: frozenset[str]) -> frozenset[str]:
    """Find unknown extension names on typed contracts, leaving opaque JSON values intact.

    A JSON object inside an extension may itself have a key named extensions; it
    is payload data, not another contract namespace. Reflection is narrowed to
    ContractModel instances and ordered collections before traversing children.
    """
    unknown = set(model.extensions) - supported
    for name in type(model).model_fields:
        if name == "extensions":
            continue
        child: object = getattr(model, name)
        if isinstance(child, ContractModel):
            unknown.update(unsupported_extension_names(child, supported))
        elif isinstance(child, (tuple, list)):
            for item in cast(Sequence[object], child):
                if isinstance(item, ContractModel):
                    unknown.update(unsupported_extension_names(item, supported))
    return frozenset(unknown)
