"""Request-local read constraints reuse the existing engine and worker owners."""

import sqlite3
from collections import OrderedDict

from ember_memory_abi.archive import Conversation, GraphNode, Message, SourceRecord
from ember_memory_abi.common import CanonicalId
from ember_memory_abi.errors import AccessDeniedError, SourceUnavailableError
from ember_memory_abi.evidence import Evidence, SourceRef, validate_evidence

from .archive import source_text
from .engine import CanonicalEngine
from .read_budget import ReadBudget


class DeepSources:
    """Lower hydration limits without changing source authority or creating another owner."""

    def __init__(self, engine: CanonicalEngine, budget: ReadBudget | None = None) -> None:
        self.engine = engine
        self.budget = budget
        # Immutable payload reuse is private to one investigation. Permissions and
        # current source headers are always checked independently at delivery.
        self._records: OrderedDict[tuple[object, ...], tuple[SourceRecord, int]] = OrderedDict()
        self._record_bytes = 0

    def resolve(self, reference: SourceRef) -> SourceRecord:
        """Oversized records are unavailable in deep mode, never silently truncated."""
        if self.budget is not None:
            self.budget.check()
        key = reference.identity_key()[:4]
        cached = self._records.get(key)
        if cached is not None:
            self._records.move_to_end(key)
            return cached[0]
        record = self.engine.resolve(
            reference, max_payload_bytes=256 * 1024, read_budget=self.budget
        )
        size = len(record.model_dump_json().encode("utf-8"))
        while self._records and (
            len(self._records) >= 256 or self._record_bytes + size > 2 * 1024 * 1024
        ):
            _, (_, evicted_bytes) = self._records.popitem(last=False)
            self._record_bytes -= evicted_bytes
        self._records[key] = (record, size)
        self._record_bytes += size
        return record

    def validate(self, evidence: Evidence) -> str:
        """Exact bounded quotes preserve original coordinates and integrity checks."""
        if evidence.span.end_codepoint - evidence.span.start_codepoint > 2000:
            raise SourceUnavailableError()
        try:
            record = self.resolve(evidence.source)
        except SourceUnavailableError:
            # Partial provenance stores may retain exact materialized evidence only.
            return self.engine.validate(
                evidence,
                max_payload_bytes=256 * 1024,
                max_quote_codepoints=2000,
                read_budget=self.budget,
            )
        reference = SourceRef.model_validate(
            {**record.source.model_dump(), "part_id": evidence.source.part_id}
        )
        return validate_evidence(
            evidence, source=reference, text=source_text(record, evidence.source)
        )

    def branch_status(self, record: SourceRecord) -> str:
        """Inspect an original branch with one bounded path lookup, never N source loads."""
        if isinstance(record, Conversation):
            return record.selection.status if record.selection else "unavailable"
        if not isinstance(record, (Message, GraphNode)):
            return "not_applicable"
        try:
            conversation = self.resolve(record.conversation)
            if not isinstance(conversation, Conversation) or conversation.selection is None:
                return "ambiguous"
            if conversation.selection.status != "resolved":
                return "ambiguous"
            if isinstance(record, GraphNode):
                return (
                    "selected"
                    if any(
                        ref.identity_key() == record.source.identity_key()
                        for ref in conversation.selection.path
                    )
                    else "alternative"
                )
            return self.engine.message_branch_status(record, conversation, read_budget=self.budget)
        except SourceUnavailableError:
            return "unavailable"

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
