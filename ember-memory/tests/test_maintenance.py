"""Explicit operator import/proposal/confirmation/recall uses real source provenance."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from ember_memory_abi.memory import CandidateMemory, EntityRef
from ember_memory_cli.operations import main
from ember_memory_engine import CanonicalEngine


@pytest.mark.end_to_end
def test_operator_can_import_nested_chatgpt_zip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "export.zip"
    nested_bytes = io.BytesIO()
    conversation = {
        "id": "synthetic-chatgpt-id",
        "current_node": "user",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["user"], "message": None},
            "user": {
                "id": "user",
                "parent": "root",
                "children": [],
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["Synthetic export text."]},
                },
            },
        },
    }
    with zipfile.ZipFile(nested_bytes, "w", compression=zipfile.ZIP_DEFLATED) as nested:
        nested.writestr("conversations-000.json", json.dumps([conversation]))
        nested.writestr("user.json", "{}")
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as outer:
        outer.writestr("export/account.zip", nested_bytes.getvalue())

    root = tmp_path / "data"
    assert (
        main(
            [
                "import",
                "chatgpt-zip",
                str(source),
                "--source-namespace",
                "synthetic-account",
                "--root",
                str(root),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["adapter"] == "chatgpt_zip"
    assert report["conversations"] == 1
    assert report["messages"] == 1


@pytest.mark.end_to_end
def test_operator_adoption_restarts_with_original_quote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "data"
    source = tmp_path / "source.json"
    text = "My primary GPU is RTX 3090."
    source.write_text(
        json.dumps(
            {
                "exporter_version": "synthetic",
                "conversations": [
                    {"id": "one", "messages": [{"role": "user", "text": text, "time": None}]}
                ],
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "import",
                "scraper-json",
                str(source),
                "--source-namespace",
                "synthetic",
                "--root",
                str(root),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["character", "--root", str(root)]) == 0
    subject = EntityRef.model_validate_json(capsys.readouterr().out)
    with CanonicalEngine(root) as engine:
        library = next(store for store in engine.stores() if store.kind == "library")
        # Read the canonical message identity from this private normalization plan.
        manifest = next((root / "libraries" / str(library.store_id) / "manifests").glob("*.json"))
        envelope = json.loads(manifest.read_text(encoding="utf-8"))
        message_id = next(
            item["record_id"]
            for batch in envelope["plan"]["batches"]
            for item in batch["identities"]
            if item["key"]["record_kind"] == "message"
        )
    candidate_path = tmp_path / "review.json"
    assert (
        main(
            [
                "propose",
                "--character",
                str(subject.store_id),
                "--source-store",
                str(library.store_id),
                "--record",
                message_id,
                "--output",
                str(candidate_path),
                "--root",
                str(root),
            ]
        )
        == 0
    )
    assert "One unapproved candidate" in capsys.readouterr().out
    candidate = CandidateMemory.model_validate_json(candidate_path.read_bytes())
    assert candidate.draft.content.confidence == 0.5
    assert main(["recall", "--character", str(subject.store_id), "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["results"] == []
    arguments = [
        "confirm",
        "--character",
        str(subject.store_id),
        "--input",
        str(candidate_path),
        "--yes",
        "--root",
        str(root),
    ]
    assert main(arguments) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["outcome"] == "applied"
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "already_applied"
    assert (
        main(
            [
                "sources",
                "--character",
                str(subject.store_id),
                "--memory",
                receipt["affected"][0]["memory_id"],
                "--root",
                str(root),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)[0]["quote"] == text
