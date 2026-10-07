"""Portable source references and exact codepoint/UTF-8 evidence validation."""

from hashlib import sha256
from typing import Self

from pydantic import model_validator

from .common import CanonicalId, ContractModel, NonnegativeInteger, Revision, Sha256, WireString
from .errors import InvalidEvidenceError


class SourceRef(ContractModel):
    """Reference an immutable canonical source revision without exposing physical locators."""

    source_store_id: CanonicalId
    record_kind: WireString
    record_id: CanonicalId
    record_revision: Revision
    part_id: CanonicalId | None = None

    def identity_key(self) -> tuple[CanonicalId, str, CanonicalId, int, CanonicalId | None]:
        """Return canonical identity independently of descriptive extension metadata."""
        return (
            self.source_store_id,
            self.record_kind,
            self.record_id,
            self.record_revision,
            self.part_id,
        )


class EvidenceSpan(ContractModel):
    """Nonempty half-open coordinates into exact, unnormalized UTF-8 source text."""

    start_codepoint: NonnegativeInteger
    end_codepoint: NonnegativeInteger
    start_byte: NonnegativeInteger
    end_byte: NonnegativeInteger
    source_content_sha256: Sha256
    quoted_span_sha256: Sha256

    @model_validator(mode="after")
    def _nonempty(self) -> Self:
        if self.end_codepoint <= self.start_codepoint or self.end_byte <= self.start_byte:
            raise ValueError("Evidence spans must be nonempty in both coordinate systems.")
        return self


class Evidence(ContractModel):
    """Immutable evidence identity bound to an exact source revision and content part."""

    evidence_id: CanonicalId
    source: SourceRef
    span: EvidenceSpan


class EvidenceAssertion(ContractModel):
    """Interpretation of exact evidence; summaries never create independent corroboration."""

    evidence: Evidence
    relation: WireString
    modality: WireString
    authority: WireString
    actor_id: CanonicalId | None = None
    verdict: WireString = "unvalidated"


def source_text_sha256(text: str) -> str:
    """Hash exact UTF-8 text, retaining combining marks, whitespace, and line endings."""
    return sha256(text.encode("utf-8")).hexdigest()


def make_evidence_span(text: str, start_codepoint: int, end_codepoint: int) -> EvidenceSpan:
    """Build both coordinate systems for a valid nonempty slice of supplied source text."""
    if (
        isinstance(start_codepoint, bool)
        or isinstance(end_codepoint, bool)
        or not 0 <= start_codepoint < end_codepoint <= len(text)
    ):
        raise InvalidEvidenceError()
    try:
        return EvidenceSpan(
            start_codepoint=start_codepoint,
            end_codepoint=end_codepoint,
            start_byte=len(text[:start_codepoint].encode("utf-8")),
            end_byte=len(text[:end_codepoint].encode("utf-8")),
            source_content_sha256=source_text_sha256(text),
            quoted_span_sha256=source_text_sha256(text[start_codepoint:end_codepoint]),
        )
    except UnicodeError as error:
        raise InvalidEvidenceError() from error


def validate_evidence(evidence: Evidence, *, source: SourceRef, text: str) -> str:
    """Return the exact quote only after validating revision, bounds, coordinates, and hashes.

    The caller resolves the source through trusted storage and access policy. Passing
    source text alone cannot validate a different source identity/revision. Errors
    have fixed diagnostics and never include the source or quoted content.
    """
    span = evidence.span
    if evidence.source.identity_key() != source.identity_key() or span.end_codepoint > len(text):
        raise InvalidEvidenceError()
    try:
        encoded = text.encode("utf-8")
        if span.end_byte > len(encoded):
            raise InvalidEvidenceError()
        quote = text[span.start_codepoint : span.end_codepoint]
        byte_quote = encoded[span.start_byte : span.end_byte].decode("utf-8")
        if (
            quote != byte_quote
            or len(text[: span.start_codepoint].encode("utf-8")) != span.start_byte
            or len(text[: span.end_codepoint].encode("utf-8")) != span.end_byte
            or source_text_sha256(text) != span.source_content_sha256
            or source_text_sha256(quote) != span.quoted_span_sha256
        ):
            raise InvalidEvidenceError()
    except UnicodeError as error:
        raise InvalidEvidenceError() from error
    return quote


def codepoint_to_utf16(text: str, index: int) -> int:
    """Convert a browser-selection boundary without normalizing canonical source text."""
    if isinstance(index, bool) or not 0 <= index <= len(text):
        raise InvalidEvidenceError()
    try:
        return len(text[:index].encode("utf-16-le")) // 2
    except UnicodeError as error:
        raise InvalidEvidenceError() from error


def utf16_to_codepoint(text: str, index: int) -> int:
    """Reject boundaries inside surrogate pairs rather than rounding a browser selection."""
    if isinstance(index, bool) or index < 0:
        raise InvalidEvidenceError()
    try:
        encoded = text.encode("utf-16-le")
        if index * 2 > len(encoded):
            raise InvalidEvidenceError()
        return len(encoded[: index * 2].decode("utf-16-le"))
    except UnicodeError as error:
        raise InvalidEvidenceError() from error
