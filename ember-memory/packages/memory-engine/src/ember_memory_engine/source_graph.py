"""Shared exact selected-path validation for grouping and model-source windows."""

import sqlite3

from ember_memory_abi.archive import Conversation, GraphNode
from ember_memory_abi.evidence import SourceRef

from .database import load_model


def selected_path(
    source: sqlite3.Connection, reference: SourceRef
) -> tuple[dict[tuple[str, int], int], bool]:
    """Validate the exact parent chain; ambiguity never acquires selected-path semantics."""
    row = source.execute(
        "SELECT * FROM source_records WHERE record_kind='conversation' AND "
        "record_id=? AND revision=?",
        (str(reference.record_id), reference.record_revision),
    ).fetchone()
    if row is None:
        return {}, False
    conversation = load_model(row, Conversation)
    if conversation.selection is None or conversation.selection.status != "resolved":
        return {}, False
    selected: dict[tuple[str, int], int] = {}
    previous: SourceRef | None = None
    for position, node in enumerate(conversation.selection.path):
        row = source.execute(
            "SELECT * FROM source_records WHERE record_kind='graph_node' AND "
            "record_id=? AND revision=?",
            (str(node.record_id), node.record_revision),
        ).fetchone()
        if row is None:
            return {}, False
        record = load_model(row, GraphNode)
        if (
            record.conversation != reference
            or (previous is None and record.parent is not None)
            or (
                previous is not None
                and (
                    record.parent is None
                    or record.parent.status != "resolved"
                    or record.parent.target != previous
                )
            )
        ):
            return {}, False
        previous = record.source
        if record.message:
            key = (str(record.message.record_id), record.message.record_revision)
            if key in selected:
                return {}, False
            selected[key] = position
    return selected, True
