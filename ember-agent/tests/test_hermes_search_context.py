"""Copied search context is smaller than its unchanged native authority receipt."""

import copy
import json
from uuid import uuid4

import pytest

from ember_agent.runtime.hermes_tasks import SEARCH_CONTEXT_BYTES, copy_search_results
from ember_agent.runtime.task_router import ToolContext, ToolReceipt
from ember_agent.runtime.task_store import TaskConflict


def test_copied_context_skips_whole_unfit_rows_and_preserves_the_native_receipt() -> None:
    rows = [
        {
            "id": "s1",
            "url": "https://public.example/" + "x" * (512 - len("https://public.example/")),
            "title": "😀" * 128,
            "snippet": "😀" * 256,
        },
        {
            "id": "s2",
            "url": "https://docs.searxng.org/",
            "title": "Search API",
            "snippet": "GET/POST",
        },
    ]
    context = ToolContext(
        character_id=uuid4(), session_id=uuid4(), turn_id=uuid4(), task_id=uuid4()
    )
    receipt = ToolReceipt(context=context, tool="searx_search", data={"results": rows})
    assert len(receipt.model_dump_json().encode()) <= 4096
    before = receipt.model_dump_json()
    selected = copy_search_results(receipt.data["results"], 2)
    assert selected == [{**rows[1], "id": "q2-s2"}]
    assert receipt.model_dump_json() == before
    assert (
        len(json.dumps({"results": selected}, ensure_ascii=False).encode()) <= SEARCH_CONTEXT_BYTES
    )


def test_copy_budget_includes_utf8_wrapper_and_namespaced_identity() -> None:
    row = {"id": "s1", "url": "https://public.example/", "title": "雨", "snippet": ""}
    overhead = len(json.dumps({"results": [{**row, "id": "q3-s1"}]}, ensure_ascii=False).encode())
    row["snippet"] = "x" * (SEARCH_CONTEXT_BYTES - overhead)
    selected = copy_search_results([row], 3)
    assert len(json.dumps({"results": selected}, ensure_ascii=False).encode()) == 2048
    assert selected == [{**row, "id": "q3-s1"}]
    assert copy_search_results([{**row, "snippet": row["snippet"] + "é"}], 3) == []


def test_copy_budget_applies_to_the_aggregate_and_keeps_later_fitting_references() -> None:
    rows = [
        {"id": "s1", "url": "https://first.example/", "title": "first", "snippet": "x" * 1400},
        {"id": "s2", "url": "https://second.example/", "title": "second", "snippet": "x" * 1000},
        {"id": "s3", "url": "https://third.example/", "title": "third", "snippet": "x" * 100},
    ]
    original = copy.deepcopy(rows)
    selected = copy_search_results(rows, 1)
    assert selected == [{**rows[0], "id": "q1-s1"}, {**rows[2], "id": "q1-s3"}]
    assert rows == original
    assert len(json.dumps({"results": selected}, ensure_ascii=False).encode()) <= 2048


@pytest.mark.parametrize("row", [None, "invalid", {}, {"id": 2}])
def test_malformed_native_reference_never_becomes_a_copied_reference(row: object) -> None:
    with pytest.raises(TaskConflict, match="hermes_invalid_reference"):
        copy_search_results([row], 1)
