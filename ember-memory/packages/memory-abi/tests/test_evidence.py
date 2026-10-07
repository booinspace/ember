"""Exact revision and Unicode-coordinate evidence conformance, using synthetic text."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ember_memory_abi.errors import InvalidEvidenceError
from ember_memory_abi.evidence import (
    Evidence,
    EvidenceSpan,
    SourceRef,
    codepoint_to_utf16,
    make_evidence_span,
    source_text_sha256,
    utf16_to_codepoint,
    validate_evidence,
)
from ember_memory_abi.memory import MemorySource

from .conftest import TEXT


def test_unicode_quote_validates_both_coordinate_systems(
    evidence: Evidence, source: SourceRef
) -> None:
    assert validate_evidence(evidence, source=source, text=TEXT) == TEXT[10:22]
    assert (
        evidence.span.end_byte - evidence.span.start_byte
        > evidence.span.end_codepoint - evidence.span.start_codepoint
    )


@pytest.mark.parametrize(
    "field", ["source_store_id", "record_id", "record_revision", "record_kind", "part_id"]
)
def test_matching_text_cannot_validate_a_different_source_identity(
    evidence: Evidence, source: SourceRef, field: str
) -> None:
    values = source.model_dump()
    values[field] = (
        2 if field == "record_revision" else "document" if field == "record_kind" else uuid4()
    )
    different = SourceRef.model_validate(values)
    with pytest.raises(InvalidEvidenceError):
        validate_evidence(evidence, source=different, text=TEXT)


def test_source_annotations_do_not_change_canonical_reference_identity(
    evidence: Evidence, source: SourceRef
) -> None:
    annotated = SourceRef.model_validate(
        {**source.model_dump(), "extensions": {"example.note": "future"}}
    )
    assert validate_evidence(evidence, source=annotated, text=TEXT) == TEXT[10:22]


@pytest.mark.parametrize(
    "field",
    [
        "start_byte",
        "end_byte",
        "start_codepoint",
        "end_codepoint",
        "source_content_sha256",
        "quoted_span_sha256",
    ],
)
def test_tampered_coordinate_or_hash_never_validates(
    evidence: Evidence, source: SourceRef, field: str
) -> None:
    values = evidence.span.model_dump()
    values[field] = "0" * 64 if "sha256" in field else values[field] + 1
    tampered = Evidence(
        evidence_id=evidence.evidence_id, source=source, span=EvidenceSpan.model_validate(values)
    )
    with pytest.raises(InvalidEvidenceError):
        validate_evidence(tampered, source=source, text=TEXT)


def test_repeated_quotes_cannot_hide_wrong_byte_position(source: SourceRef) -> None:
    text = "same same"
    span = make_evidence_span(text, 0, 4)
    values = {**span.model_dump(), "start_byte": 5, "end_byte": 9}
    evidence = Evidence(
        evidence_id=uuid4(), source=source, span=EvidenceSpan.model_validate(values)
    )
    with pytest.raises(InvalidEvidenceError):
        validate_evidence(evidence, source=source, text=text)


def test_equivalent_normalized_text_does_not_validate_original_evidence(source: SourceRef) -> None:
    text = "e\u0301\r\n"
    evidence = Evidence(
        evidence_id=uuid4(), source=source, span=make_evidence_span(text, 0, len(text))
    )
    with pytest.raises(InvalidEvidenceError):
        validate_evidence(evidence, source=source, text="é\n")


@pytest.mark.parametrize("start,end", [(0, 0), (-1, 1), (0, 10000), (True, 2)])
def test_invalid_span_bounds_fail_with_content_free_domain_error(start: int, end: int) -> None:
    with pytest.raises(InvalidEvidenceError) as raised:
        make_evidence_span("PRIVATE_TEXT_SENTINEL", start, end)
    assert "SENTINEL" not in str(raised.value)


def test_browser_utf16_offsets_roundtrip_and_reject_a_split_surrogate() -> None:
    text = "a🍰e\u0301"
    assert [codepoint_to_utf16(text, index) for index in range(len(text) + 1)] == [0, 1, 3, 4, 5]
    for index in range(len(text) + 1):
        assert utf16_to_codepoint(text, codepoint_to_utf16(text, index)) == index
    with pytest.raises(InvalidEvidenceError):
        utf16_to_codepoint(text, 2)
    with pytest.raises(InvalidEvidenceError):
        utf16_to_codepoint(text, 6)


def test_resolved_memory_source_requires_the_exact_original_quote(evidence: Evidence) -> None:
    MemorySource(evidence=evidence, availability="available", quote=TEXT[10:22])
    with pytest.raises(ValidationError):
        MemorySource(evidence=evidence, availability="available", quote="invented")
    unavailable = MemorySource(evidence=evidence, availability="unavailable")
    assert unavailable.quote is None
    assert source_text_sha256("é") != source_text_sha256("e\u0301")
