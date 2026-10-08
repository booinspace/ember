"""Saved-memory summary adapter; retain exact originals and never approve their claims."""

import re
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import JsonValue

from ember_memory_abi.archive import (
    ContentPart,
    Document,
    ImportCursor,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.common import canonical_object_sha256
from ember_memory_abi.evidence import SourceRef, source_text_sha256

from .contracts import ImportReport, ImportTarget, RecordKey, SourceImportError
from .source_io import preserve_json, reject_links

ADAPTER = "chatgpt_memory_summary"
VERSION = "0.1.0"


def _entries(value: JsonValue, field: str) -> tuple[tuple[str | None, str], ...]:
    if isinstance(value, dict):
        value = value.get("memories")
    if not isinstance(value, list) or len(value) > 100000:
        raise SourceImportError("Summary JSON requires a bounded list or a memories list.")
    result: list[tuple[str | None, str]] = []
    for item in value:
        if isinstance(item, str):
            result.append((None, item))
        elif isinstance(item, dict) and isinstance(item.get(field), str):
            text = item[field]
            identity = item.get("id")
            if identity is not None and (not isinstance(identity, str) or not identity):
                raise SourceImportError("Summary IDs must be nonempty strings.")
            assert isinstance(text, str)
            result.append((identity, text))
        else:
            raise SourceImportError("Summary entry does not match the selected text field.")
    if any(not text.strip() for _, text in result):
        raise SourceImportError("Summary entries must contain visible text.")
    identities = [identity for identity, _ in result if identity is not None]
    if len(set(identities)) != len(identities):
        raise SourceImportError("Duplicate saved-memory IDs prevent safe normalization.")
    return tuple(result)


def _preserve_text(source: Path, vault: Path) -> tuple[str, str]:
    reject_links(source)
    reject_links(vault)
    before = source.stat()
    with source.open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    after = source.stat()
    if len(raw) > 8 * 1024 * 1024 or (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        raise SourceImportError("Summary text exceeds its bound or changed while reading.")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise SourceImportError("Summary text must be UTF-8.") from error
    if not text.strip():
        raise SourceImportError("Summary text is empty.")
    digest = sha256(raw).hexdigest()
    vault.mkdir(parents=True, exist_ok=True)
    target = vault / (digest + ".txt")
    reject_links(target)
    if target.exists():
        if target.read_bytes() != raw:
            raise SourceImportError("Preserved summary integrity verification failed.")
    else:
        with target.open("xb") as stream:
            stream.write(raw)
    return text, digest


def import_summary(
    source: Path,
    *,
    target: ImportTarget,
    raw_vault: Path,
    manifests: Path,
    format: Literal["text", "json"] = "text",
    text_field: str = "text",
) -> ImportReport:
    """Normalize explicit summary entries in atomic resumable batches, without inference.

    Text/Markdown is one exact document. JSON accepts strings or entries with the
    explicitly selected text field; other metadata remains only in the verbatim vault.
    Unknown dates are never fabricated, and summaries are not conversation sources.
    """
    if format == "json":
        value, digest = preserve_json(source, raw_vault)
        entries = _entries(value, text_field)
    else:
        text, digest = _preserve_text(source, raw_vault)
        entries = ((None, text),)
    if any(
        re.search(r"<\s*(think|analysis|reasoning)\b", text, re.IGNORECASE) for _, text in entries
    ):
        raise SourceImportError("Reasoning-tagged summaries require a visible-only source.")
    spec = ProcessingSpecRef(
        name=ADAPTER,
        version=VERSION,
        specification_sha256=canonical_object_sha256(
            {"adapter": ADAPTER, "format": format, "text_field": text_field}
        ),
    )
    cursor = ImportCursor(
        logical_inventory_sha256=canonical_object_sha256(
            {"source": digest, "spec": spec.model_dump(mode="json")}
        ),
        component_sha256=digest,
        next_record_index=0,
        adapter=ADAPTER,
        adapter_version=VERSION,
    )
    prior = target.cursor(cursor)
    start = prior.next_record_index if prior is not None else 0
    written = 0
    for offset in range(start, len(entries), 128):
        batch = entries[offset : offset + 128]
        keys = tuple(
            RecordKey(
                adapter=ADAPTER,
                record_kind="document",
                external_id=(
                    "id:" + identity
                    if identity is not None
                    else "text:" + source_text_sha256(text) + ":" + str(offset + ordinal)
                ),
            )
            for ordinal, (identity, text) in enumerate(batch)
        )
        assigned = target.identify(keys)
        previous = target.latest_many(tuple(("document", item.record_id) for item in assigned))
        records: list[Document] = []
        for item, (_, text) in zip(assigned, batch, strict=True):
            old = previous.get(("document", item.record_id))
            if old is not None and not isinstance(old, Document):
                raise SourceImportError("Summary identity is not a document.")
            unchanged = (
                isinstance(old, Document) and len(old.parts) == 1 and old.parts[0].text == text
            )
            revision = old.source.record_revision + int(not unchanged) if old is not None else 1
            records.append(
                Document(
                    source=SourceRef(
                        source_store_id=target.source_store_id,
                        record_kind="document",
                        record_id=item.record_id,
                        record_revision=revision,
                    ),
                    recorded_at="1970-01-01T00:00:00.000000Z",
                    title="ChatGPT saved-memory summary",
                    parts=(
                        ContentPart(
                            part_id=old.parts[0].part_id
                            if isinstance(old, Document) and old.parts
                            else uuid4(),
                            kind="text",
                            status="available",
                            text=text,
                            content_sha256=source_text_sha256(text),
                        ),
                    ),
                )
            )
        next_cursor = cursor.model_copy(update={"next_record_index": offset + len(batch)})
        receipt = target.commit(
            NormalizedArchiveBatch(
                source_store_id=target.source_store_id,
                source_namespace=target.namespace,
                idempotency_key=ADAPTER
                + ":"
                + digest
                + ":"
                + spec.specification_sha256
                + ":"
                + str(offset),
                processing_spec=spec,
                records=tuple(records),
                resume_cursor=next_cursor,
            ),
            assigned,
        )
        written += receipt.records_written
    report = ImportReport(
        adapter=ADAPTER,
        source_store_id=target.source_store_id,
        source_sha256=digest,
        conversations=0,
        messages=0,
        available_texts=len(entries),
        excluded_texts=0,
        records_written=written,
        coverage=(
            "summary_only",
            "exact_original_text",
            "no_automatic_activation",
            "unknown_source_time",
        ),
    )
    reject_links(manifests)
    manifests.mkdir(parents=True, exist_ok=True)
    path = manifests / (ADAPTER + "-" + digest + ".json")
    if not path.exists():
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(report.model_dump_json(indent=2) + "\n")
    return report
