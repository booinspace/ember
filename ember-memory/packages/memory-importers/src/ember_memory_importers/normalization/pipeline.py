"""Versioned immutable normalization plans make multi-batch imports resumable and replayable."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, TypeAdapter, ValidationError

from ember_memory_abi.archive import (
    ImportCursor,
    Message,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
    SourceRecord,
)
from ember_memory_abi.common import ContractModel, canonical_object_sha256

from .adapters import STAMP, graph_units, scraper_units
from .contracts import ImportReport, ImportTarget, RecordIdentity, SourceImportError
from .source_io import preserve_json, reject_links


class PlannedBatch(ContractModel):
    """Private persisted replay inputs; mappings and records commit together."""

    identities: tuple[RecordIdentity, ...]
    batch: NormalizedArchiveBatch


class ImportPlan(ContractModel):
    """Immutable versioned normalization checkpoint; no approved or activated beliefs."""

    version: Literal["0.1.0"] = "0.1.0"
    report: ImportReport
    batches: tuple[PlannedBatch, ...]


class PlanEnvelope(ContractModel):
    """Detect changed private normalization state before replaying its canonical writes."""

    payload_sha256: str
    plan: ImportPlan


def _scrub(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        reference = {"source_store_id", "record_id", "record_kind"}.issubset(value)
        return {
            key: _scrub(item)
            for key, item in value.items()
            if not (reference and key == "record_revision")
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def source_record_semantic_hash(record: SourceRecord) -> str:
    """Hash the normalized record content while ignoring recorded-time observations."""
    json_adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
    value = json_adapter.validate_python(record.model_dump(mode="json", exclude={"recorded_at"}))
    return canonical_object_sha256(_scrub(value))


def apply_source_revisions(value: JsonValue, revisions: dict[str, int]) -> JsonValue:
    """Apply planned record revisions consistently across records and embedded references."""
    if isinstance(value, dict):
        result = {key: apply_source_revisions(item, revisions) for key, item in value.items()}
        identity = result.get("record_id")
        if (
            "source_store_id" in result
            and "record_kind" in result
            and isinstance(identity, str)
            and identity in revisions
        ):
            result["record_revision"] = revisions[identity]
        return result
    if isinstance(value, list):
        return [apply_source_revisions(item, revisions) for item in value]
    return value


def _build(value: JsonValue, digest: str, adapter: str, target: ImportTarget) -> ImportPlan:
    if adapter == "scraper_json":
        units, failures = scraper_units(value, target)
        coverage = (
            "flattened_graph_unavailable",
            "message_identity_content_occurrence",
            "unverified_assistant_tool_content_excluded",
            "saved_memory_collection_absent",
            "failed_conversations_not_exported",
        )
    elif adapter == "chatgpt_mapping":
        units = graph_units(value, target)
        failures = 0
        coverage = (
            "mapping_graph_preserved",
            "unsupported_parts_not_fetched",
            "unknown_metadata_in_raw_source",
        )
    else:
        raise SourceImportError("Unsupported source adapter.")
    all_records: list[SourceRecord] = []
    all_identities: list[RecordIdentity] = []
    record_adapter: TypeAdapter[SourceRecord] = TypeAdapter(SourceRecord)
    for identities, records in units:
        previous = {
            str(record.source.record_id): target.latest(
                record.source.record_kind, record.source.record_id
            )
            for record in records
        }
        changed = False
        for record in records:
            old = previous[str(record.source.record_id)]
            if old is None or source_record_semantic_hash(record) != source_record_semantic_hash(
                old
            ):
                changed = True
                break
        revisions = {
            identity: (old.source.record_revision + 1 if changed else old.source.record_revision)
            if old
            else 1
            for identity, old in previous.items()
        }
        for record in records:
            json_adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
            serialized = json_adapter.validate_python(record.model_dump(mode="json"))
            adjusted = apply_source_revisions(serialized, revisions)
            normalized = record_adapter.validate_json(json.dumps(adjusted, allow_nan=False))
            all_records.append(normalized.model_copy(update={"recorded_at": STAMP}))
        all_identities.extend(identities)
    keyed = {str(identity.record_id): identity for identity in all_identities}
    specification = ProcessingSpecRef(
        name="ember." + adapter + ".normalize",
        version="0.1.0",
        specification_sha256=sha256(
            (adapter + ":canonical-v1:visibility-conservative").encode()
        ).hexdigest(),
    )
    batches: list[PlannedBatch] = []
    for offset in range(0, len(all_records), 256):
        records = tuple(all_records[offset : offset + 256])
        identities = tuple(keyed[str(record.source.record_id)] for record in records)
        cursor = ImportCursor(
            logical_inventory_sha256=digest,
            component_sha256=digest,
            next_record_index=offset + len(records),
            adapter=adapter,
            adapter_version="0.1.0",
        )
        batch = NormalizedArchiveBatch(
            source_store_id=target.source_store_id,
            source_namespace=target.namespace,
            idempotency_key=adapter + ":" + digest + ":" + str(offset),
            processing_spec=specification,
            records=records,
            resume_cursor=cursor,
        )
        batches.append(PlannedBatch(identities=identities, batch=batch))
    messages = [record for record in all_records if isinstance(record, Message)]
    report = ImportReport(
        adapter=adapter,
        source_store_id=target.source_store_id,
        source_sha256=digest,
        conversations=len(units),
        messages=len(messages),
        available_texts=sum(part.status == "available" for msg in messages for part in msg.parts),
        excluded_texts=sum(part.status == "excluded" for msg in messages for part in msg.parts),
        failures=failures,
        records_written=0,
        coverage=coverage,
    )
    return ImportPlan(report=report, batches=tuple(batches))


def import_json(
    path: Path, *, adapter: str, target: ImportTarget, raw_vault: Path, manifests: Path
) -> ImportReport:
    """Preserve, plan, then replay bounded atomic batches; return coverage without source text."""
    value, digest = preserve_json(path, raw_vault)
    reject_links(manifests)
    manifests.mkdir(parents=True, exist_ok=True)
    checkpoint = manifests / (adapter + "-" + digest + "-v0.1.0.json")
    reject_links(checkpoint)
    try:
        if checkpoint.exists():
            if checkpoint.is_symlink() or checkpoint.stat().st_size > 128 * 1024 * 1024:
                raise SourceImportError("Normalization checkpoint is unsafe or oversized.")
            envelope = PlanEnvelope.model_validate_json(checkpoint.read_bytes())
            plan = envelope.plan
            if (
                envelope.payload_sha256 != canonical_object_sha256(plan)
                or plan.report.source_store_id != target.source_store_id
                or plan.report.source_sha256 != digest
                or plan.report.adapter != adapter
            ):
                raise SourceImportError(
                    "Normalization checkpoint failed identity/integrity validation."
                )
        else:
            probe = ImportCursor(
                logical_inventory_sha256=digest,
                component_sha256=digest,
                next_record_index=0,
                adapter=adapter,
                adapter_version="0.1.0",
            )
            if target.cursor(probe) is not None:
                raise SourceImportError(
                    "Restore the original normalization checkpoint before replay."
                )
            plan = _build(value, digest, adapter, target)
            envelope = PlanEnvelope(payload_sha256=canonical_object_sha256(plan), plan=plan)
            payload = envelope.model_dump_json().encode("utf-8")
            if len(payload) > 128 * 1024 * 1024:
                raise SourceImportError("Normalization plan exceeds its bound.")
            temporary = checkpoint.with_suffix(".next")
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                import os

                os.fsync(output.fileno())
            temporary.replace(checkpoint)
        written = 0
        for planned in plan.batches:
            receipt = target.commit(planned.batch, planned.identities)
            if receipt.outcome == "applied":
                written += receipt.records_written
        return plan.report.model_copy(update={"records_written": written})
    except (OSError, ValidationError) as error:
        raise SourceImportError(
            "Normalization checkpoint could not be read or persisted."
        ) from error
