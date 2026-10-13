"""Map separately authorized archive observations to bounded exact original excerpts."""

import re
from uuid import uuid4

from ember_memory_abi.archive import Document, Message
from ember_memory_abi.evidence import Evidence, make_evidence_span
from ember_memory_abi.interfaces import ArchiveResult
from ember_memory_abi.memory import ArchiveChunk


def archive_evidence(
    results: tuple[ArchiveResult, ...], *, topic_terms: set[str]
) -> tuple[Evidence, ...]:
    """Retain original coordinates; no guessed event dates or assistant belief activation."""
    selected: dict[tuple[object, ...], Evidence] = {}
    pattern = (
        re.compile(
            r"\b(?:" + "|".join(re.escape(term) for term in sorted(topic_terms)) + r")\b",
            re.IGNORECASE,
        )
        if topic_terms
        else None
    )

    def passage(text: str) -> tuple[int, int]:
        # Search original Unicode so case folding cannot corrupt codepoint coordinates.
        match = pattern.search(text) if pattern is not None else None
        start = max(0, match.start() - 500) if match is not None else 0
        return start, min(len(text), start + 2000)

    for result in results[:12]:
        record = result.record
        if isinstance(record, ArchiveChunk):
            candidates = tuple(span.evidence for span in record.source_spans)
        elif isinstance(record, Message | Document):
            candidates = tuple(
                Evidence(
                    evidence_id=uuid4(),
                    source=record.source.model_copy(update={"part_id": part.part_id}),
                    span=make_evidence_span(part.text, *passage(part.text)),
                )
                for part in record.parts
                if part.text and part.status == "available"
            )
        else:
            candidates = ()
        for evidence in candidates:
            if evidence.span.end_codepoint - evidence.span.start_codepoint > 2000:
                continue
            key = (*evidence.source.identity_key(), evidence.span.quoted_span_sha256)
            selected.setdefault(key, evidence)
            if len(selected) == 24:
                return tuple(selected.values())
    return tuple(selected.values())
