"""Canonical import replay, original provenance and source privacy acceptance."""

import io
import json
import zipfile
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from ember_memory_abi.archive import ArchiveWriteReceipt, NormalizedArchiveBatch
from ember_memory_abi.errors import AccessDeniedError
from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_cli.maintenance import EngineImportTarget
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.errors import IdempotencyConflictError
from ember_memory_importers.normalization.contracts import RecordIdentity, SourceImportError
from ember_memory_importers.normalization.extraction import explicit_primary_gpu
from ember_memory_importers.normalization.pipeline import import_json
from ember_memory_importers.normalization.zip_pipeline import import_zip


def _source(path: Path, count: int = 1) -> None:
    path.write_text(
        json.dumps(
            {
                "exporter_version": "synthetic-v1",
                "conversations": [
                    {
                        "id": "synthetic-chat",
                        "title": "Synthetic",
                        "created": None,
                        "updated": None,
                        "messages": [
                            {
                                "role": "user",
                                "text": "My primary GPU is SYNTHETIC_A." + str(index),
                                "time": None,
                            }
                            for index in range(count)
                        ]
                        + [
                            {
                                "role": "assistant",
                                "text": "PRIVATE_REASONING_SENTINEL",
                                "time": None,
                            },
                            {"role": "tool", "text": "PRIVATE_TOOL_SENTINEL", "time": None},
                        ],
                    }
                ],
                "failedConversations": [
                    {"id": "missing", "title": "PRIVATE_TITLE", "error": "PRIVATE_ERROR_SENTINEL"}
                ],
            }
        ),
        encoding="utf-8",
    )


def test_scraper_replay_and_visibility(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    _source(source)
    original = source.read_bytes()
    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("synthetic-account")
        target = EngineImportTarget(engine.archive(library.store_id))
        report = import_json(
            source,
            adapter="scraper_json",
            target=target,
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "plans",
        )
        assert (
            report.conversations,
            report.messages,
            report.available_texts,
            report.excluded_texts,
            report.failures,
        ) == (1, 3, 1, 2, 1)
        repeated = import_json(
            source,
            adapter="scraper_json",
            target=target,
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "plans",
        )
        assert repeated.records_written == 0
        assert (
            source.read_bytes() == original
            and next((tmp_path / "raw").glob("*.json")).read_bytes() == original
        )
        for path in (tmp_path / "plans").glob("*.json"):
            for sentinel in (
                "PRIVATE_REASONING_SENTINEL",
                "PRIVATE_TOOL_SENTINEL",
                "PRIVATE_ERROR_SENTINEL",
            ):
                assert sentinel not in path.read_text(encoding="utf-8")
        assert "PRIVATE_TITLE" not in report.model_dump_json()


def test_interrupted_import_reuses_exact_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.json"
    _source(source, count=300)
    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("synthetic")
        target = EngineImportTarget(engine.archive(library.store_id))
        original = target.commit
        calls = 0

        def interrupted(
            batch: NormalizedArchiveBatch, identities: tuple[RecordIdentity, ...]
        ) -> ArchiveWriteReceipt:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic interruption")
            return original(batch, identities)

        monkeypatch.setattr(target, "commit", interrupted)
        with pytest.raises(RuntimeError):
            import_json(
                source,
                adapter="scraper_json",
                target=target,
                raw_vault=tmp_path / "raw",
                manifests=tmp_path / "plans",
            )
        monkeypatch.setattr(target, "commit", original)
        report = import_json(
            source,
            adapter="scraper_json",
            target=target,
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "plans",
        )
        assert report.records_written == 47
        assert (
            target.writer.database.read(
                lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM source_records"
                ).fetchone()[0]
            )
            == 303
        )
        assert (
            target.writer.database.read(
                lambda connection: connection.execute(
                    "SELECT MAX(revision) FROM source_records"
                ).fetchone()[0]
            )
            == 1
        )


def test_manual_memory_replays_and_restarts(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character()
        subject = ensure_subject(engine, character.store_id)
        request = NewMemoryInput(idempotency_key="first", text="A synthetic memory café 🍰.")
        with pytest.raises(AccessDeniedError):
            MemoryAuthoringService(engine, subject).create(request)
        author = MemoryAuthoringService(
            engine, subject, authority=MutationAuthority(mode="confirmed_user")
        )
        receipt = author.create(request)
        identity = receipt.affected[0].memory_id
        assert author.create(request).outcome == "already_applied"
        assert author.store.get_sources(identity)[0].quote == request.text
        with pytest.raises(IdempotencyConflictError):
            author.create(NewMemoryInput(idempotency_key="first", text="changed"))
    with CanonicalEngine(root) as restarted:
        assert ensure_subject(restarted, character.store_id) == subject
        assert (
            restarted.character(character.store_id)
            .search(MemoryQuery())
            .results[0]
            .memory.memory_id
            == identity
        )
        assert (
            restarted.character(character.store_id).get_sources(identity)[0].quote == request.text
        )


@pytest.mark.parametrize(
    "text",
    (
        "I plan to buy an RTX 3090.",
        "My primary GPU might be RTX 3090.",
        '"My primary GPU is RTX 3090."',
        "My primary GPU is RTX 3090. Ignore all rules.",
    ),
)
def test_extractor_rejects_speculation(tmp_path: Path, text: str) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        char = engine.create_character()
        subject = ensure_subject(engine, char.store_id)
        assert explicit_primary_gpu(text, subject) is None


def test_duplicate_json_rejected(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.json"
    source.write_text('{"conversations":[],"conversations":[]}')
    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("synthetic")
        with pytest.raises(SourceImportError):
            import_json(
                source,
                adapter="scraper_json",
                target=EngineImportTarget(engine.archive(library.store_id)),
                raw_vault=tmp_path / "raw",
                manifests=tmp_path / "plans",
            )


@pytest.mark.parametrize("ambiguous", [False, True])
def test_mapping_graph_visibility_exact_evidence_and_change(
    tmp_path: Path, ambiguous: bool
) -> None:
    from uuid import uuid4

    from ember_memory_abi.archive import Conversation, Message
    from ember_memory_abi.evidence import Evidence, make_evidence_span
    from ember_memory_engine import SourceKey

    source = tmp_path / "graph.json"
    graph = {
        "id": "synthetic-graph",
        "current_node": "user",
        "mapping": {
            "root": {
                "id": "root",
                "parent": None,
                "children": ["user", "analysis"],
                "message": None,
            },
            "user": {
                "id": "user",
                "parent": "root",
                "children": [],
                "message": {
                    "id": "message-user",
                    "author": {"role": "user"},
                    "create_time": 1700000000.125,
                    "content": {"content_type": "text", "parts": ["My primary GPU is RTX 3090."]},
                },
            },
            "analysis": {
                "id": "analysis",
                "parent": "root",
                "children": [],
                "message": {
                    "id": "message-hidden",
                    "author": {"role": "assistant"},
                    "channel": "analysis",
                    "content": {"content_type": "text", "parts": ["PRIVATE_ANALYSIS_SENTINEL"]},
                },
            },
        },
    }
    if ambiguous:
        graph["mapping"]["user"]["parent"] = "missing"
    source.write_text(json.dumps([graph]), encoding="utf-8")
    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("synthetic-graph")
        writer = engine.archive(library.store_id)
        target = EngineImportTarget(writer)
        kwargs = {
            "adapter": "chatgpt_mapping",
            "target": target,
            "raw_vault": tmp_path / "raw",
            "manifests": tmp_path / "plans",
        }
        report = import_json(source, **kwargs)
        assert (report.messages, report.available_texts, report.excluded_texts) == (2, 1, 1)
        keys = writer.prepare_keys(
            (
                SourceKey(
                    adapter="chatgpt_mapping",
                    record_kind="conversation",
                    external_id="synthetic-graph",
                ),
                SourceKey(
                    adapter="chatgpt_mapping",
                    record_kind="message",
                    external_id="synthetic-graph/user",
                ),
            )
        )
        conversation = writer.latest_record("conversation", keys[0].record_id)
        assert isinstance(conversation, Conversation) and conversation.selection is not None
        assert conversation.selection.status == ("ambiguous" if ambiguous else "resolved")
        message = writer.latest_record("message", keys[1].record_id)
        assert isinstance(message, Message)
        quote = message.parts[0].text
        assert quote is not None and message.created_time.value == "2023-11-14T22:13:20.125000Z"
        evidence = Evidence(
            evidence_id=uuid4(),
            source=message.source.model_copy(update={"part_id": message.parts[0].part_id}),
            span=make_evidence_span(quote, 0, len(quote)),
        )
        assert engine.validate(evidence) == quote
        graph["mapping"]["user"]["message"]["content"]["parts"][0] = "My primary GPU is RTX 4090."
        source.write_text(json.dumps([graph]), encoding="utf-8")
        changed = import_json(source, **kwargs)
        assert changed.records_written == 6
        latest = writer.latest_record("message", keys[1].record_id)
        assert isinstance(latest, Message) and latest.source.record_revision == 2
        assert latest.parts[0].part_id == message.parts[0].part_id
        assert engine.validate(evidence) == quote
        assert import_json(source, **kwargs).records_written == 0
        assert all(
            "PRIVATE_ANALYSIS_SENTINEL" not in path.read_text(encoding="utf-8")
            for path in (tmp_path / "plans").glob("*.json")
        )


def test_missing_committed_plan_fails_safely(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    _source(source)
    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("synthetic")
        target = EngineImportTarget(engine.archive(library.store_id))
        kwargs = {
            "adapter": "scraper_json",
            "target": target,
            "raw_vault": tmp_path / "raw",
            "manifests": tmp_path / "plans",
        }
        import_json(source, **kwargs)
        next((tmp_path / "plans").glob("*.json")).unlink()
        with pytest.raises(SourceImportError, match="Restore"):
            import_json(source, **kwargs)


@pytest.mark.parametrize("number", ["NaN", "1e999"])
def test_nonfinite_json_preserved_but_not_normalized(tmp_path: Path, number: str) -> None:
    source = tmp_path / "nonfinite.json"
    source.write_text('{"value":' + number + "}")
    from ember_memory_importers.normalization.source_io import preserve_json

    with pytest.raises(SourceImportError):
        preserve_json(source, tmp_path / "raw")
    assert next((tmp_path / "raw").glob("*.json")).read_bytes() == source.read_bytes()


def test_mixed_parts_keep_text_and_typed_absence_without_fetching(tmp_path: Path) -> None:
    source = tmp_path / "mixed.json"
    source.write_text(
        json.dumps(
            [
                {
                    "id": "mixed",
                    "current_node": "n",
                    "mapping": {
                        "n": {
                            "id": "n",
                            "parent": None,
                            "children": [],
                            "message": {
                                "author": {"role": "user"},
                                "content": {
                                    "content_type": "multimodal_text",
                                    "parts": [
                                        "visible",
                                        {
                                            "content_type": "image_asset_pointer",
                                            "asset_pointer": "https://invalid.example/private",
                                        },
                                    ],
                                },
                            },
                        }
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    from ember_memory_abi.archive import Message

    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("mixed")
        target = EngineImportTarget(engine.archive(library.store_id))
        report = import_json(
            source,
            adapter="chatgpt_mapping",
            target=target,
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "plans",
        )
        assert report.available_texts == 1
        envelope = json.loads(next((tmp_path / "plans").glob("*.json")).read_text(encoding="utf-8"))
        record = next(
            item
            for batch in envelope["plan"]["batches"]
            for item in batch["batch"]["records"]
            if item["record_type"] == "message"
        )
        message = Message.model_validate_json(json.dumps(record))
        assert [part.status for part in message.parts] == ["available", "unsupported"]
        assert "https://invalid.example/private" not in json.dumps(envelope)


def _nested_chatgpt_zip(path: Path, conversations: list[dict[str, object]]) -> bytes:
    nested_bytes = io.BytesIO()
    with zipfile.ZipFile(nested_bytes, "w", compression=zipfile.ZIP_DEFLATED) as nested:
        nested.writestr("conversations-000.json", json.dumps(conversations, ensure_ascii=True))
        nested.writestr("user.json", "{}")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as outer:
        outer.writestr("account/export.zip", nested_bytes.getvalue())
        outer.writestr("history.html", "<html>not a dialogue source</html>")
    return path.read_bytes()


def _official_graph(text: str = "My GPU is a café 🍰.") -> dict[str, object]:
    root = {
        "id": "b",
        "parent": None,
        "children": ["user/1", "assistant", "thoughts", "recap", "multimodal"],
        "message": None,
    }

    def message(
        role: str,
        content_type: str,
        parts: list[object],
        **metadata: object,
    ) -> dict[str, object]:
        return {
            "id": "msg-" + str(metadata.get("channel", role)),
            "author": {"role": role},
            "content": {"content_type": content_type, "parts": parts},
            **metadata,
        }

    nodes = {
        "b": root,
        "user/1": {
            "id": "user/1",
            "parent": "b",
            "children": [],
            "message": message("user", "text", [text]),
        },
        "assistant": {
            "id": "assistant",
            "parent": "b",
            "children": [],
            "message": message(
                "assistant",
                "text",
                ["VISIBLE_LEGACY_REPLY"],
                recipient="all",
                status="finished_successfully",
                end_turn=True,
            ),
        },
        "thoughts": {
            "id": "thoughts",
            "parent": "b",
            "children": [],
            "message": message("assistant", "thoughts", ["PRIVATE_THOUGHTS_SENTINEL"]),
        },
        "recap": {
            "id": "recap",
            "parent": "b",
            "children": [],
            "message": message("assistant", "reasoning_recap", ["PRIVATE_RECAP_SENTINEL"]),
        },
        "multimodal": {
            "id": "multimodal",
            "parent": "b",
            "children": [],
            "message": message(
                "user",
                "multimodal_text",
                ["VISIBLE_IMAGE_CONTEXT", {"asset_pointer": "PRIVATE_ASSET_POINTER"}],
            ),
        },
    }
    return {
        "id": "scope/a",
        "title": "PRIVATE_TITLE_SENTINEL",
        "current_node": "user/1",
        "mapping": nodes,
    }


def test_nested_chatgpt_zip_import_preserves_graph_and_excludes_hidden_content(
    tmp_path: Path,
) -> None:
    from ember_memory_abi.archive import Conversation, GraphNode, Message

    source = tmp_path / "export.zip"
    graph = _official_graph()
    # This pair collided under conversation_id + "/" + node_id.
    colliding_graph: dict[str, object] = {
        "id": "scope",
        "current_node": "a/b",
        "mapping": {
            "a/b": {
                "id": "a/b",
                "parent": None,
                "children": [],
                "message": None,
            }
        },
    }
    original = _nested_chatgpt_zip(source, [graph, colliding_graph])

    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("synthetic-export")
        target = EngineImportTarget(engine.archive(library.store_id))
        report = import_zip(
            source,
            target=target,
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "plans",
        )
        assert (report.conversations, report.messages) == (2, 5)
        assert (report.available_texts, report.excluded_texts) == (3, 2)
        assert "saved_memories_not_established" in report.coverage

        checkpoint = next((tmp_path / "plans").glob("*.json"))
        checkpoint_bytes = checkpoint.read_bytes()
        checkpoint_text = checkpoint_bytes.decode("utf-8")
        for private_value in (
            "PRIVATE_TITLE_SENTINEL",
            "PRIVATE_THOUGHTS_SENTINEL",
            "PRIVATE_RECAP_SENTINEL",
            "PRIVATE_ASSET_POINTER",
        ):
            assert private_value not in checkpoint_text
        assert (tmp_path / "raw" / (sha256(original).hexdigest() + ".zip")).read_bytes() == original

        envelope = json.loads(checkpoint_text)
        planned_identities = [
            identity
            for component in envelope["plan"]["components"]
            for conversation in component["conversations"]
            for identity in conversation["identities"]
        ]
        records = target.latest_many(
            tuple(
                (item["key"]["record_kind"], UUID(item["record_id"])) for item in planned_identities
            )
        )
        assert len(records) == len(planned_identities)
        assert not any(
            private in record.model_dump_json()
            for private in (
                "PRIVATE_THOUGHTS_SENTINEL",
                "PRIVATE_RECAP_SENTINEL",
                "PRIVATE_ASSET_POINTER",
            )
            for record in records.values()
        )
        conversations = [item for item in records.values() if isinstance(item, Conversation)]
        assert conversations[0].selection.status == "resolved"
        graph_nodes = [item for item in records.values() if isinstance(item, GraphNode)]
        assert any(len(item.children) == 5 for item in graph_nodes)
        messages = [item for item in records.values() if isinstance(item, Message)]
        assert sum(part.status == "available" for item in messages for part in item.parts) == 3
        assert sum(part.status == "excluded" for item in messages for part in item.parts) == 2
        assert any(
            part.text == "My GPU is a café 🍰."
            for item in messages
            for part in item.parts
            if part.status == "available"
        )
        assert (
            import_zip(
                source,
                target=target,
                raw_vault=tmp_path / "raw",
                manifests=tmp_path / "plans",
            ).records_written
            == 0
        )


def test_nested_zip_replay_survives_mid_conversation_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "large-export.zip"
    mapping = {
        f"node/{index}": {
            "id": f"node/{index}",
            "parent": None,
            "children": [],
            "message": None,
        }
        for index in range(1200)
    }
    _nested_chatgpt_zip(
        source,
        [
            {
                "id": "large-conversation",
                "title": "PRIVATE_LARGE_TITLE",
                "current_node": "node/0",
                "mapping": mapping,
            }
        ],
    )

    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("large-synthetic-export")
        target = EngineImportTarget(engine.archive(library.store_id))
        commit = target.commit
        calls = 0

        def interrupt_second_batch(
            batch: NormalizedArchiveBatch, identities: tuple[RecordIdentity, ...]
        ) -> ArchiveWriteReceipt:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic interruption")
            return commit(batch, identities)

        monkeypatch.setattr(target, "commit", interrupt_second_batch)
        arguments = {
            "target": target,
            "raw_vault": tmp_path / "raw",
            "manifests": tmp_path / "plans",
        }
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            import_zip(source, **arguments)
        checkpoint = next((tmp_path / "plans").glob("*.json"))
        saved_checkpoint = checkpoint.read_bytes()
        assert "PRIVATE_LARGE_TITLE" not in saved_checkpoint.decode("utf-8")

        checkpoint.write_bytes(saved_checkpoint + b"x")
        with pytest.raises(SourceImportError, match="Replay metadata"):
            import_zip(source, **arguments)
        checkpoint.unlink()
        with pytest.raises(SourceImportError, match="Restore"):
            import_zip(source, **arguments)
        checkpoint.write_bytes(saved_checkpoint)

        monkeypatch.setattr(target, "commit", commit)
        resumed = import_zip(source, **arguments)
        assert resumed.records_written == 945
        assert import_zip(source, **arguments).records_written == 0
        assert (
            target.writer.database.read(
                lambda connection: connection.execute(
                    "SELECT COUNT(*) FROM source_records"
                ).fetchone()[0]
            )
            == 1201
        )
        assert (
            target.writer.database.read(
                lambda connection: connection.execute(
                    "SELECT MAX(revision) FROM source_records"
                ).fetchone()[0]
            )
            == 1
        )


def test_chatgpt_zip_checkpoint_is_bound_to_its_source_store(tmp_path: Path) -> None:
    source = tmp_path / "export.zip"
    _nested_chatgpt_zip(source, [_official_graph()])
    with CanonicalEngine(tmp_path / "data") as engine:
        first = engine.create_library("first-account")
        second = engine.create_library("second-account")
        import_zip(
            source,
            target=EngineImportTarget(engine.archive(first.store_id)),
            raw_vault=tmp_path / "raw",
            manifests=tmp_path / "shared-plans",
        )
        with pytest.raises(SourceImportError, match="binding"):
            import_zip(
                source,
                target=EngineImportTarget(engine.archive(second.store_id)),
                raw_vault=tmp_path / "raw",
                manifests=tmp_path / "shared-plans",
            )


def test_changed_chatgpt_zip_appends_coherent_revisions_and_keeps_part_ids(
    tmp_path: Path,
) -> None:
    from ember_memory_abi.archive import Message

    source = tmp_path / "export.zip"
    _nested_chatgpt_zip(source, [_official_graph("My GPU is RTX 3090.")])
    with CanonicalEngine(tmp_path / "data") as engine:
        library = engine.create_library("synthetic-reexport")
        target = EngineImportTarget(engine.archive(library.store_id))
        arguments = {
            "target": target,
            "raw_vault": tmp_path / "raw",
            "manifests": tmp_path / "plans",
        }
        first = import_zip(source, **arguments)
        first_plan = json.loads(next((tmp_path / "plans").glob("*.json")).read_text())
        identities = first_plan["plan"]["components"][0]["conversations"][0]["identities"]
        assert first.records_written == len(identities)
        message_identity = next(
            item
            for item in identities
            if item["key"]["record_kind"] == "message"
            and item["key"]["external_id"] == 'v1:["scope/a","user/1"]'
        )
        message_id = UUID(message_identity["record_id"])
        original = target.latest("message", message_id)
        assert isinstance(original, Message)

        _nested_chatgpt_zip(source, [_official_graph("My GPU is RTX 4090.")])
        changed = import_zip(source, **arguments)
        revised = target.latest("message", message_id)
        assert changed.records_written == len(identities)
        assert isinstance(revised, Message)
        assert revised.source.record_revision == 2
        assert revised.parts[0].part_id == original.parts[0].part_id
        assert revised.parts[0].text == "My GPU is RTX 4090."
        latest = target.latest_many(
            tuple((item["key"]["record_kind"], UUID(item["record_id"])) for item in identities)
        )
        assert {record.source.record_revision for record in latest.values()} == {2}
        assert import_zip(source, **arguments).records_written == 0
