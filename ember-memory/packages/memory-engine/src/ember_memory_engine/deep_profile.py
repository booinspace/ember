"""Request-local read constraints reuse the existing engine and worker owners."""

import sqlite3

from ember_memory_abi.archive import SourceRecord
from ember_memory_abi.common import CanonicalId
from ember_memory_abi.errors import AccessDeniedError
from ember_memory_abi.evidence import Evidence, SourceRef

from .engine import CanonicalEngine
from .read_budget import ReadBudget


class DeepSources:
    """Lower hydration limits without changing source authority or creating another owner."""

    def __init__(self, engine: CanonicalEngine, budget: ReadBudget | None = None) -> None:
        self.engine = engine
        self.budget = budget

    def resolve(self, reference: SourceRef) -> SourceRecord:
        """Oversized records are unavailable in deep mode, never silently truncated."""
        return self.engine.resolve(reference, max_payload_bytes=256 * 1024, read_budget=self.budget)

    def validate(self, evidence: Evidence) -> str:
        """Exact bounded quotes preserve original coordinates and integrity checks."""
        return self.engine.validate(
            evidence,
            max_payload_bytes=256 * 1024,
            max_quote_codepoints=2000,
            read_budget=self.budget,
        )

    def persist_evidence(
        self,
        evidence: Evidence,
        *,
        local_connection: sqlite3.Connection | None = None,
        local_store_id: CanonicalId | None = None,
    ) -> None:
        """A deep read profile never admits writes."""
        raise AccessDeniedError()

    def unavailable_sources(
        self,
        references: tuple[SourceRef, ...],
        *,
        local_connection: sqlite3.Connection,
        local_store_id: CanonicalId,
        call_budget: int,
    ) -> tuple[tuple[SourceRef, ...], int]:
        """Retain existing batched header availability checks."""
        return self.engine.unavailable_sources(
            references,
            local_connection=local_connection,
            local_store_id=local_store_id,
            call_budget=call_budget,
            read_budget=self.budget,
        )
