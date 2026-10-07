"""Engine-specific typed failures; public callers can catch the ABI MemoryError base."""

from ember_memory_abi.errors import MemoryError


class StoreOwnershipError(MemoryError):
    """Another process/store instance owns the canonical writer lock."""

    code = "store_ownership_conflict"
    safe_message = "The canonical store already has a writer owner."


class StorageError(MemoryError):
    """Canonical storage cannot complete an operation; no source/path values are logged."""

    code = "storage_unavailable"
    safe_message = "Canonical storage is unavailable."


class CorruptStoreError(StorageError):
    """Canonical metadata/revision bytes do not match the supported schema/contracts."""

    code = "canonical_state_invalid"
    safe_message = "Canonical state failed integrity validation."


class EngineBusyError(MemoryError):
    """Bounded worker admission is full; no work was accepted."""

    code = "engine_busy"
    safe_message = "The memory worker admission limit is full."


class EngineClosedError(MemoryError):
    """The composition owner closed or is draining this engine."""

    code = "engine_closed"
    safe_message = "The memory engine is closed or draining."


class IdempotencyConflictError(MemoryError):
    """A used scoped key cannot identify a different operation or semantic request."""

    code = "idempotency_conflict"
    safe_message = "The idempotency key is already bound to different request content."


class WriteOutcomeUnknownError(MemoryError):
    """Running write exceeded its caller deadline; use the identical request/key to resolve."""

    code = "write_outcome_unknown"
    safe_message = "A running write has an unresolved outcome; retain its idempotency key."


class UnsupportedQueryError(MemoryError):
    """This fallback does not implement requested pagination/query semantics."""

    code = "unsupported_query"
    safe_message = "The requested query semantics are unsupported."
