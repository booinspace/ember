"""Two bounded passes: private replay metadata first, then atomic canonical source batches."""

import json
import os
import zipfile
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

from pydantic import JsonValue, TypeAdapter, ValidationError, model_validator

from ember_memory_abi.archive import (
    ArchiveWriteReceipt,
    ImportCursor,
    Message,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
    SourceRecord,
)
from ember_memory_abi.common import CanonicalId, ContractModel, canonical_object_sha256

from ..base.archive import ArchiveInspectionError, inspect_zip
from ..chatgpt.source_models import SourceConversation
from ..chatgpt.streaming import ConversationJsonError, ConversationLimitError
from ..report_models import InventoryEntry
from .adapters import graph_units
from .contracts import ImportReport, ImportTarget, RecordIdentity, RecordKey, SourceImportError
from .pipeline import apply_source_revisions, source_record_semantic_hash
from .source_io import reject_links
from .zip_source import ZipSource, preserve_zip

ADAPTER = "chatgpt_zip"
KEY_ADAPTER = "chatgpt_zip_v1"
VERSION = "0.3.0"
BATCH_SIZE = 256
MAXIMUM_GRAPH_RECORDS = 10_000
MAXIMUM_PLAN_BYTES = 64 * 1024**2
PROCESSING_SPEC = ProcessingSpecRef(
    name="ember.chatgpt_zip.normalize",
    version=VERSION,
    specification_sha256=sha256(
        b"chatgpt-zip-v1:explicit-visible:legacy-completed-text:exclude-reasoning"
    ).hexdigest(),
)


class RecordReplay(ContractModel):
    """Compact reconstruction metadata; bodies stay in canonical source storage."""

    identity_index: int
    revision: int
    part_ids: tuple[CanonicalId, ...] = ()


class ConversationReplay(ContractModel):
    """Stable reconstruction inputs survive a crash between a conversation's write batches."""

    identities: tuple[RecordIdentity, ...]
    records: tuple[RecordReplay, ...]
    batch_ends: tuple[int, ...]
    records_sha256: str

    @model_validator(mode="after")
    def _validate_replay_shape(self) -> "ConversationReplay":
        indices = tuple(item.identity_index for item in self.records)
        if (
            len(self.records) != len(self.identities)
            or len(indices) != len(set(indices))
            or set(indices) != set(range(len(self.identities)))
            or self.batch_ends != _batch_ends(len(self.records))
            or any(item.revision < 1 for item in self.records)
            or len({item.record_id for item in self.identities}) != len(self.identities)
        ):
            raise ValueError("Invalid conversation replay metadata.")
        return self


class ComponentReplay(ContractModel):
    """Qualified component binding and its original conversation order, without physical paths."""

    path_sha256: str
    container_path_sha256: tuple[str, ...]
    content_sha256: str
    conversations: tuple[ConversationReplay, ...]


class ZipReplayPlan(ContractModel):
    """Private metadata checkpoint; published completely before any canonical source write."""

    version: Literal["0.3.0"] = "0.3.0"
    key_adapter: Literal["chatgpt_zip_v1"] = "chatgpt_zip_v1"
    source_namespace: str
    logical_inventory_sha256: str
    processing_spec_sha256: str
    report: ImportReport
    components: tuple[ComponentReplay, ...]

    @model_validator(mode="after")
    def _validate_binding(self) -> "ZipReplayPlan":
        component_ids = tuple(
            (item.container_path_sha256, item.path_sha256) for item in self.components
        )
        if (
            self.report.adapter != ADAPTER
            or not self.source_namespace.strip()
            or len(component_ids) != len(set(component_ids))
            or self.processing_spec_sha256 != PROCESSING_SPEC.specification_sha256
        ):
            raise ValueError("Replay metadata has an invalid source or adapter binding.")
        return self


class _Envelope(ContractModel):
    payload_sha256: str
    plan: ZipReplayPlan


class _PreparedTarget:
    """Reuse the maintenance port with in-memory key/revision inputs for one source graph."""

    def __init__(
        self,
        parent: ImportTarget,
        identities: tuple[RecordIdentity, ...],
        previous: Mapping[tuple[str, CanonicalId], SourceRecord],
    ) -> None:
        self.parent = parent
        self.source_store_id = parent.source_store_id
        self.namespace = parent.namespace
        self.identities = {
            (item.key.adapter, item.key.record_kind, item.key.external_id): item
            for item in identities
        }
        self.previous = previous

    def identify(self, keys: tuple[RecordKey, ...]) -> tuple[RecordIdentity, ...]:
        try:
            return tuple(
                self.identities[key.adapter, key.record_kind, key.external_id] for key in keys
            )
        except KeyError as error:
            raise SourceImportError("Source graph disagrees with its replay identities.") from error

    def latest(self, kind: str, identity: CanonicalId) -> SourceRecord | None:
        return self.previous.get((kind, identity))

    def latest_many(
        self, identities: tuple[tuple[str, CanonicalId], ...]
    ) -> Mapping[tuple[str, CanonicalId], SourceRecord]:
        return {key: self.previous[key] for key in identities if key in self.previous}

    def cursor(self, cursor: ImportCursor) -> ImportCursor | None:
        return self.parent.cursor(cursor)

    def commit(
        self, batch: NormalizedArchiveBatch, identities: tuple[RecordIdentity, ...]
    ) -> ArchiveWriteReceipt:
        return self.parent.commit(batch, identities)


def _pair_key(conversation_id: str, node_id: str) -> str:
    """Encode provider-ID pairs injectively under the versioned ZIP key adapter."""
    return "v1:" + json.dumps([conversation_id, node_id], ensure_ascii=True, separators=(",", ":"))


def _batch_ends(record_count: int) -> tuple[int, ...]:
    if record_count == 0:
        return ()
    return (*range(BATCH_SIZE, record_count, BATCH_SIZE), record_count)


def _keys(value: JsonValue) -> tuple[str, tuple[RecordKey, ...]]:
    conversation = SourceConversation.model_validate(value)
    if (
        conversation.id is not None
        and conversation.conversation_id is not None
        and conversation.id != conversation.conversation_id
    ):
        raise SourceImportError("Conversation has conflicting provider identities.")
    external = conversation.id if conversation.id is not None else conversation.conversation_id
    if not external or any(not key for key in conversation.mapping):
        raise SourceImportError("Conversation or graph-node identity is missing.")
    entries = [("conversation", external)]
    entries.extend(("graph_node", _pair_key(external, key)) for key in conversation.mapping)
    entries.extend(
        ("message", _pair_key(external, key))
        for key, node in conversation.mapping.items()
        if node.message is not None
    )
    if len(entries) > MAXIMUM_GRAPH_RECORDS:
        raise SourceImportError("Conversation exceeds the 10,000 record bound.")
    keys = tuple(
        RecordKey(adapter=KEY_ADAPTER, record_kind=kind, external_id=key) for kind, key in entries
    )
    if len({(item.record_kind, item.external_id) for item in keys}) != len(keys):
        raise SourceImportError("Conversation contains duplicate normalized source identities.")
    return external, keys


def _identify(target: ImportTarget, keys: tuple[RecordKey, ...]) -> tuple[RecordIdentity, ...]:
    identities = tuple(
        identity
        for offset in range(0, len(keys), 512)
        for identity in target.identify(keys[offset : offset + 512])
    )
    if tuple(item.key for item in identities) != keys or len(
        {item.record_id for item in identities}
    ) != len(identities):
        raise SourceImportError("Maintenance store returned conflicting source identities.")
    return identities


def _adjust(
    records: tuple[SourceRecord, ...], revisions: dict[str, int]
) -> tuple[SourceRecord, ...]:
    adapter: TypeAdapter[SourceRecord] = TypeAdapter(SourceRecord)
    value_adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
    return tuple(
        adapter.validate_json(
            json.dumps(
                apply_source_revisions(
                    value_adapter.validate_python(record.model_dump(mode="json")), revisions
                ),
                allow_nan=False,
            )
        )
        for record in records
    )


def _records_hash(records: tuple[SourceRecord, ...]) -> str:
    return canonical_object_sha256(
        [item.model_dump(mode="json", exclude={"recorded_at"}) for item in records]
    )


def _component_fingerprint(component: ComponentReplay) -> str:
    return canonical_object_sha256(
        {
            "container_path_sha256": list(component.container_path_sha256),
            "path_sha256": component.path_sha256,
            "content_sha256": component.content_sha256,
        }
    )


def _cursor(logical_inventory_sha256: str, component: ComponentReplay, index: int) -> ImportCursor:
    return ImportCursor(
        logical_inventory_sha256=logical_inventory_sha256,
        component_sha256=_component_fingerprint(component),
        next_record_index=index,
        adapter=KEY_ADAPTER,
        adapter_version=VERSION,
    )


def _build(source: ZipSource, target: ImportTarget) -> ZipReplayPlan:
    units: dict[str, list[ConversationReplay]] = {}
    seen: set[str] = set()
    messages = available = excluded = 0
    metadata_bytes = 0
    for entry, value in source.graphs():
        external, keys = _keys(value)
        if external in seen:
            raise SourceImportError("Duplicate conversation identities across ZIP components.")
        seen.add(external)
        identities = _identify(target, keys)
        previous = target.latest_many(
            tuple((item.key.record_kind, item.record_id) for item in identities)
        )
        prepared = _PreparedTarget(target, identities, previous)
        normalized_units = graph_units(value, prepared, key_adapter=KEY_ADAPTER, pair_key=_pair_key)
        if len(normalized_units) != 1 or normalized_units[0][0] != identities:
            raise SourceImportError("Conversation normalization changed its planned identities.")
        records = normalized_units[0][1]
        changed = any(
            (old := previous.get((record.source.record_kind, record.source.record_id))) is None
            or source_record_semantic_hash(record) != source_record_semantic_hash(old)
            for record in records
        )
        revisions = {
            str(item.record_id): (
                old.source.record_revision + int(changed)
                if (old := previous.get((item.key.record_kind, item.record_id))) is not None
                else 1
            )
            for item in identities
        }
        adjusted = _adjust(records, revisions)
        identity_indices = {item.record_id: index for index, item in enumerate(identities)}
        metadata: list[RecordReplay] = []
        for record in adjusted:
            index = identity_indices.get(record.source.record_id)
            if index is None or identities[index].key.record_kind != record.source.record_kind:
                raise SourceImportError("Normalized graph records disagree with source identities.")
            metadata.append(
                RecordReplay(
                    identity_index=index,
                    revision=record.source.record_revision,
                    part_ids=(
                        tuple(part.part_id for part in record.parts)
                        if isinstance(record, Message)
                        else ()
                    ),
                )
            )
        replay = ConversationReplay(
            identities=identities,
            records=tuple(metadata),
            batch_ends=_batch_ends(len(adjusted)),
            records_sha256=_records_hash(adjusted),
        )
        metadata_bytes += len(replay.model_dump_json().encode("utf-8"))
        if metadata_bytes > MAXIMUM_PLAN_BYTES - 1024 * 1024:
            raise SourceImportError("Replay metadata exceeds its 64 MiB bound.")
        units.setdefault(entry.path_sha256, []).append(replay)
        for item in adjusted:
            if isinstance(item, Message):
                messages += 1
                available += sum(part.status == "available" for part in item.parts)
                excluded += sum(part.status == "excluded" for part in item.parts)
    raw = source.report.source.raw_sha256
    if raw is None:
        raise SourceImportError("ZIP raw identity is missing.")
    return ZipReplayPlan(
        source_namespace=target.namespace,
        logical_inventory_sha256=source.report.source.logical_inventory_sha256,
        processing_spec_sha256=PROCESSING_SPEC.specification_sha256,
        report=ImportReport(
            adapter=ADAPTER,
            source_store_id=target.source_store_id,
            source_sha256=raw,
            conversations=len(seen),
            messages=messages,
            available_texts=available,
            excluded_texts=excluded,
            records_written=0,
            coverage=(
                "mapping_graph_preserved",
                "legacy_completed_text_visibility",
                "reasoning_and_recaps_excluded",
                "assets_and_html_preserved_raw_only",
                "unsupported_parts_not_fetched",
                "saved_memories_not_established",
            ),
        ),
        components=tuple(
            ComponentReplay(
                path_sha256=entry.path_sha256,
                container_path_sha256=entry.container_path_sha256,
                content_sha256=entry.content_sha256,
                conversations=tuple(units.get(entry.path_sha256, ())),
            )
            for entry in source.components
        ),
    )


def _component_key(entry: InventoryEntry) -> tuple[tuple[str, ...], str, str]:
    return (entry.container_path_sha256, entry.path_sha256, entry.content_sha256)


def _matches_source(plan: ZipReplayPlan, source: ZipSource, target: ImportTarget) -> bool:
    raw = source.report.source.raw_sha256
    if raw is None:
        return False
    expected = tuple(_component_key(entry) for entry in source.components)
    actual = tuple(
        (item.container_path_sha256, item.path_sha256, item.content_sha256)
        for item in plan.components
    )
    return (
        plan.version == VERSION
        and plan.key_adapter == KEY_ADAPTER
        and plan.report.source_store_id == target.source_store_id
        and plan.source_namespace == target.namespace
        and plan.report.source_sha256 == raw
        and plan.logical_inventory_sha256 == source.report.source.logical_inventory_sha256
        and plan.processing_spec_sha256 == PROCESSING_SPEC.specification_sha256
        and actual == expected
    )


def _write_checkpoint(path: Path, plan: ZipReplayPlan) -> None:
    payload = (
        _Envelope(payload_sha256=canonical_object_sha256(plan), plan=plan)
        .model_dump_json()
        .encode("utf-8")
    )
    if len(payload) > MAXIMUM_PLAN_BYTES:
        raise SourceImportError("Replay metadata exceeds its 64 MiB bound.")
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix="zip-plan-", suffix=".partial", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _checkpoint(path: Path, source: ZipSource, target: ImportTarget) -> ZipReplayPlan:
    reject_links(path)
    if path.exists():
        if path.stat().st_size > MAXIMUM_PLAN_BYTES:
            raise SourceImportError("Replay metadata exceeds its 64 MiB bound.")
        try:
            envelope = _Envelope.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as error:
            raise SourceImportError("Replay metadata is corrupt or unreadable.") from error
        if envelope.payload_sha256 != canonical_object_sha256(envelope.plan) or not _matches_source(
            envelope.plan, source, target
        ):
            raise SourceImportError(
                "Replay metadata failed source, namespace or integrity binding."
            )
        return envelope.plan

    empty_components = tuple(
        ComponentReplay(
            path_sha256=entry.path_sha256,
            container_path_sha256=entry.container_path_sha256,
            content_sha256=entry.content_sha256,
            conversations=(),
        )
        for entry in source.components
    )
    for component in empty_components:
        if (
            target.cursor(_cursor(source.report.source.logical_inventory_sha256, component, 0))
            is not None
        ):
            raise SourceImportError("Restore original replay metadata before resuming this import.")

    plan = _build(source, target)
    _write_checkpoint(path, plan)
    return plan


def _record_position_boundaries(component: ComponentReplay) -> set[int]:
    boundaries = {0}
    offset = 0
    for conversation in component.conversations:
        boundaries.update(offset + end for end in conversation.batch_ends)
        offset += len(conversation.records)
    return boundaries


def _read_positions(plan: ZipReplayPlan, target: ImportTarget) -> dict[str, int]:
    positions: dict[str, int] = {}
    for component in plan.components:
        cursor = target.cursor(_cursor(plan.logical_inventory_sha256, component, 0))
        if cursor is None:
            position = 0
        else:
            if (
                cursor.logical_inventory_sha256 != plan.logical_inventory_sha256
                or cursor.component_sha256 != _component_fingerprint(component)
                or cursor.adapter != KEY_ADAPTER
                or cursor.adapter_version != VERSION
            ):
                raise SourceImportError("Committed import cursor does not match replay metadata.")
            position = cursor.next_record_index
        if position not in _record_position_boundaries(component):
            raise SourceImportError("Committed cursor is outside a planned batch boundary.")
        positions[component.path_sha256] = position
    return positions


def _reconstruct(
    value: JsonValue, replay: ConversationReplay, target: ImportTarget
) -> tuple[SourceRecord, ...]:
    prepared = _PreparedTarget(target, replay.identities, {})
    part_ids = {
        replay.identities[item.identity_index].record_id: item.part_ids
        for item in replay.records
        if item.part_ids
    }
    normalized_units = graph_units(
        value,
        prepared,
        part_ids=part_ids,
        key_adapter=KEY_ADAPTER,
        pair_key=_pair_key,
    )
    if len(normalized_units) != 1 or normalized_units[0][0] != replay.identities:
        raise SourceImportError("Source graph disagrees with replay metadata.")
    revisions = {
        str(replay.identities[item.identity_index].record_id): item.revision
        for item in replay.records
    }
    records = _adjust(normalized_units[0][1], revisions)
    if len(records) != len(replay.records) or _records_hash(records) != replay.records_sha256:
        raise SourceImportError("Reconstructed records disagree with replay metadata.")
    return records


def _replay(source: ZipSource, plan: ZipReplayPlan, target: ImportTarget) -> int:
    positions = _read_positions(plan, target)
    totals = {
        item.path_sha256: sum(len(unit.records) for unit in item.conversations)
        for item in plan.components
    }
    if all(positions[item.path_sha256] == totals[item.path_sha256] for item in plan.components):
        return 0

    components = {item.path_sha256: item for item in plan.components}
    conversation_indices: dict[str, int] = {}
    component_offsets: dict[str, int] = {}
    written = 0

    for entry, value in source.graphs():
        component = components.get(entry.path_sha256)
        if component is None:
            raise SourceImportError("ZIP contains a component absent from replay metadata.")
        index = conversation_indices.get(entry.path_sha256, 0)
        if index >= len(component.conversations):
            raise SourceImportError("ZIP has more conversations than its replay metadata.")
        replay = component.conversations[index]
        conversation_indices[entry.path_sha256] = index + 1
        offset = component_offsets.get(entry.path_sha256, 0)
        component_offsets[entry.path_sha256] = offset + len(replay.records)
        if offset + len(replay.records) <= positions[entry.path_sha256]:
            continue

        records = _reconstruct(value, replay, target)
        identities_by_id = {item.record_id: item for item in replay.identities}
        start = 0
        for end in replay.batch_ends:
            cursor_position = offset + end
            if cursor_position > positions[entry.path_sha256]:
                batch_records = records[start:end]
                batch_identities = tuple(
                    identities_by_id[item.source.record_id] for item in batch_records
                )
                batch = NormalizedArchiveBatch(
                    source_store_id=target.source_store_id,
                    source_namespace=target.namespace,
                    idempotency_key=(
                        f"{ADAPTER}:{VERSION}:{plan.report.source_sha256}:"
                        f"{entry.path_sha256}:{offset + start}"
                    ),
                    processing_spec=PROCESSING_SPEC,
                    records=batch_records,
                    resume_cursor=_cursor(
                        plan.logical_inventory_sha256, component, cursor_position
                    ),
                )
                receipt = target.commit(batch, batch_identities)
                if receipt.outcome == "applied":
                    written += receipt.records_written
            start = end

    for component in plan.components:
        if conversation_indices.get(component.path_sha256, 0) != len(component.conversations):
            raise SourceImportError("ZIP has fewer conversations than replay metadata.")
        if component_offsets.get(component.path_sha256, 0) != totals[component.path_sha256]:
            raise SourceImportError("ZIP record coverage disagrees with replay metadata.")
    return written


def import_zip(
    path: Path, *, target: ImportTarget, raw_vault: Path, manifests: Path
) -> ImportReport:
    """Inspect/preserve once, plan metadata, then normalize through resumable owned writes.

    Original assets remain in the ZIP. Reasoning is never in replay metadata or
    canonical text. Memory activation/grants are separate explicit operator actions.
    This is a blocking maintenance operation, not realtime event-loop work.
    """
    try:
        reject_links(path)
        reject_links(manifests)
        report = inspect_zip(path, source_namespace=target.namespace, include_member_paths=True)
        preserved = preserve_zip(path, raw_vault, report)
        source = ZipSource(preserved, report)
        manifests.mkdir(parents=True, exist_ok=True)
        checkpoint = manifests / (
            "chatgpt-zip-" + str(report.source.raw_sha256) + "-v" + VERSION + ".json"
        )
        plan = _checkpoint(checkpoint, source, target)
        return plan.report.model_copy(update={"records_written": _replay(source, plan, target)})
    except (
        ArchiveInspectionError,
        ConversationJsonError,
        ConversationLimitError,
        ValidationError,
        OSError,
        zipfile.BadZipFile,
        KeyError,
        IndexError,
    ) as error:
        raise SourceImportError(
            "ZIP import source or replay state is invalid; original data is retained."
        ) from error
