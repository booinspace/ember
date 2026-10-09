"""Bounded exact source windows; canonical coordinates remain the extraction authority."""

import sqlite3
from collections.abc import Iterator

from ember_memory_abi.archive import Document, Message
from ember_memory_abi.evidence import source_text_sha256

from .processing_contracts import JobDefinition, ModelWindow, ProcessingError, WindowRef
from .source_access import source_rows
from .source_graph import selected_path


def windows(source: sqlite3.Connection, job: JobDefinition) -> Iterator[WindowRef]:
    """Plan immutable metadata in bounded pages, without requiring a disposable search index."""
    request = job.request
    selection = (
        "AND revision=(SELECT MAX(n.revision) FROM source_records n WHERE "
        "n.record_kind=source_records.record_kind AND n.record_id=source_records.record_id "
        "AND n.recorded_at_us<=" + str(job.canonical_clock_us) + ")"
    )
    if request.source_kind == "summary":
        selection += (
            " AND record_id IN (SELECT record_id FROM source_keys "
            "WHERE adapter='chatgpt_memory_summary')"
        )
    elif request.source_kind == "archive":
        selection += " AND record_kind='message'"
    elif (
        request.source_kind == "native"
        and request.specification.extractor == "ember.atomic-claims@0.2.0"
    ):
        selection += (
            " AND record_kind='message' AND record_id IN (SELECT record_id FROM source_keys"
            " WHERE adapter='native_delivery')"
        )
    elif request.source_kind == "native":
        selection += (
            " AND record_kind='document' AND record_id NOT IN (SELECT record_id "
            "FROM source_keys WHERE adapter='chatgpt_memory_summary')"
        )
    selection += " AND record_kind IN ('message','document')"
    parameters: tuple[str | int, ...] = ()
    if request.source_references:
        selection += (
            " AND (record_kind,record_id,revision) IN ("
            + ",".join("(?,?,?)" for _ in request.source_references)
            + ")"
        )
        parameters = tuple(
            value
            for ref in request.source_references
            for value in (ref.record_kind, str(ref.record_id), ref.record_revision)
        )
    graph_cache: dict[tuple[str, int], tuple[dict[tuple[str, int], int], bool]] = {}
    for record in source_rows(source, job.canonical_clock_us, selection, parameters):
        if not isinstance(record, (Message, Document)):
            continue
        if isinstance(record, Message) and record.channel in {
            "analysis",
            "reasoning",
            "thoughts",
            "reasoning_recap",
        }:
            continue
        summary = (
            source.execute(
                "SELECT 1 FROM source_keys WHERE record_id=? AND adapter='chatgpt_memory_summary'",
                (str(record.source.record_id),),
            ).fetchone()
            is not None
        )
        branch = "not_applicable"
        role = record.actor.role if isinstance(record, Message) else "user"
        if isinstance(record, Message):
            graph_key = (str(record.conversation.record_id), record.conversation.record_revision)
            if graph_key not in graph_cache:
                if len(graph_cache) >= 32:
                    graph_cache.pop(next(iter(graph_cache)))
                graph_cache[graph_key] = selected_path(source, record.conversation)
            selected, resolved = graph_cache[graph_key]
            branch = (
                (
                    "selected"
                    if (str(record.source.record_id), record.source.record_revision) in selected
                    else "alternative"
                )
                if resolved
                else "ambiguous"
            )
        if request.specification.extractor == "ember.atomic-claims@0.2.0":
            trusted = (
                isinstance(record, Message)
                and record.actor.role == "user"
                and record.actor.actor_id == job.subject.entity_id
                and source.execute(
                    "SELECT 1 FROM source_keys WHERE adapter='native_delivery' "
                    "AND record_id=? AND external_id LIKE 'v2:user:%'",
                    (str(record.source.record_id),),
                ).fetchone()
                is not None
            )
            branch = "not_applicable" if trusted else "ambiguous"
        authority = (
            "summary"
            if summary
            else "direct_user"
            if isinstance(record, Message) and role == "user"
            else "assistant"
            if role == "assistant"
            else "observation"
        )
        for part in record.parts:
            if part.status != "available" or part.text is None or not part.text.strip():
                continue
            if part.kind in {"analysis", "reasoning", "thoughts", "reasoning_recap"}:
                continue
            text = part.text
            text_digest = source_text_sha256(text)
            start = 0
            while start < len(text):
                # UTF-8 bytes conservatively bound the same gateway prompt budget;
                # split Unicode codepoints rather than cutting byte sequences.
                end = min(len(text), start + request.specification.window_bytes)
                while len(text[start:end].encode("utf-8")) > request.specification.window_bytes:
                    end -= max(1, (end - start) // 8)
                if end < len(text):
                    boundary = text.rfind("\n", start + (end - start) // 2, end)
                    if boundary >= 0:
                        end = boundary + 1
                if end <= start:
                    raise ProcessingError("window_boundary_invalid")
                yield WindowRef(
                    source=record.source.model_copy(update={"part_id": part.part_id}),
                    source_sha256=text_digest,
                    start=start,
                    end=end,
                    text_sha256=source_text_sha256(text[start:end]),
                    role=role,
                    authority=authority,
                    branch=branch,
                    observed_at=record.created_time.value,
                )
                if end == len(text):
                    break
                start = max(start + 1, end - request.specification.overlap_codepoints)


def resolve_window(text: str, reference: WindowRef) -> ModelWindow:
    """Verify the original part and requested interval before any model input is sent."""
    fragment = text[reference.start : reference.end]
    if (
        reference.end > len(text)
        or source_text_sha256(text) != reference.source_sha256
        or source_text_sha256(fragment) != reference.text_sha256
    ):
        raise ProcessingError("source_window_changed")
    return ModelWindow(reference=reference, text=fragment)
