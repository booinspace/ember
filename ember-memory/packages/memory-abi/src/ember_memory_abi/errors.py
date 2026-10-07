"""Typed, content-free domain failures shared by clients and persistence adapters."""

from typing import ClassVar

from .common import CanonicalId, ContractModel, Revision, WireString


class ErrorDetails(ContractModel):
    """Portable identifiers/revisions only; never prompts, source text, or machine paths."""

    record_id: CanonicalId | None = None
    expected_revision: Revision | None = None
    actual_revision: Revision | None = None
    requested_version: WireString | None = None


class ErrorInfo(ContractModel):
    """Serializable failure envelope whose code has no implied retry/authorization policy."""

    code: WireString
    details: ErrorDetails


class MemoryError(Exception):
    """Base typed domain failure with a fixed safe message and structured details."""

    code: ClassVar[str] = "memory_error"
    safe_message: ClassVar[str] = "Memory operation failed."

    def __init__(self, details: ErrorDetails | None = None) -> None:
        self.info = ErrorInfo(code=self.code, details=details or ErrorDetails())
        super().__init__(self.safe_message)


class NotFoundError(MemoryError):
    """The requested permitted canonical record or revision does not exist."""

    code = "not_found"
    safe_message = "The requested record was not found."


class RevisionConflictError(MemoryError):
    """A mutation's expected revision does not match current state."""

    code = "revision_conflict"
    safe_message = "The expected revision no longer matches current state."


class AccessDeniedError(MemoryError):
    """The trusted client binding, grants, or current suppression deny access."""

    code = "access_denied"
    safe_message = "The requested access is not permitted."


class UnsupportedWriteError(MemoryError):
    """A readable kind/modality/extension cannot acquire unsupported write semantics."""

    code = "unsupported_write"
    safe_message = "The requested write semantics are unsupported."


class UnsupportedVersionError(MemoryError):
    """A contract or processing version is not supported by this implementation."""

    code = "unsupported_version"
    safe_message = "The requested version is unsupported."


class InvalidEvidenceError(MemoryError):
    """Coordinates, source revision, Unicode boundaries, or hashes do not match."""

    code = "invalid_evidence"
    safe_message = "Evidence does not match the exact source revision and content."


class InvalidPredicateError(MemoryError):
    """A registered predicate's value, modality, or definition reference is invalid."""

    code = "invalid_predicate"
    safe_message = "The predicate definition, value, or modality is invalid."


class IndexUnavailableError(MemoryError):
    """A requested derived index is unavailable; canonical history is not lost."""

    code = "index_unavailable"
    safe_message = "The requested search index is unavailable."


class DeadlineExceededError(MemoryError):
    """Work could not complete within the explicitly owned request deadline."""

    code = "deadline_exceeded"
    safe_message = "The request deadline was exceeded."


class SourceUnavailableError(MemoryError):
    """An original source revision cannot currently be resolved or verified."""

    code = "source_unavailable"
    safe_message = "The original source is unavailable."
