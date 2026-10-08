"""Scraper and mapping-graph adapters: original text is data, never authority."""

from collections import Counter
from collections.abc import Callable, Mapping
from decimal import Decimal

from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from ember_memory_abi.archive import (
    ActorAttribution,
    BranchSelection,
    ContentPart,
    Conversation,
    GraphNode,
    Message,
    SourceLink,
    SourceRecord,
)
from ember_memory_abi.common import (
    CanonicalId,
    canonical_object_sha256,
    timestamp_from_unix_microseconds,
)
from ember_memory_abi.evidence import SourceRef, source_text_sha256
from ember_memory_abi.temporal import SourceTimestamp

from ..chatgpt.source_models import SourceConversation, SourceMessage, SourceModel
from .contracts import ImportTarget, RecordIdentity, RecordKey, SourceImportError

STAMP = "1970-01-01T00:00:00.000000Z"


def graph_message_visible(message: SourceMessage, role: str) -> bool:
    """Recognize explicit visible channels and the observed completed legacy text shape."""
    content = message.content
    if (
        content is None
        or content.content_type not in {"text", "multimodal_text", "code"}
        or message.channel in {"analysis", "reasoning", "thoughts"}
        or (message.metadata or {}).get("is_visually_hidden_from_conversation") is True
    ):
        return False
    if role == "user":
        return True
    if role != "assistant" or message.recipient not in {None, "all"}:
        return False
    if message.channel in {"final", "commentary"}:
        return True
    return (
        message.channel is None
        and content.content_type == "text"
        and message.recipient == "all"
        and message.status == "finished_successfully"
        and message.end_turn is True
    )


class ScrapedMessage(SourceModel):
    """A flattened observation lacking trustworthy graph/channel metadata."""

    role: str
    text: str = Field(repr=False)
    time: int | float | None = None


class ScrapedConversation(SourceModel):
    """Provider identity, timestamps and message order are preserved as observations."""

    id: str = Field(repr=False)
    title: str | None = Field(default=None, repr=False)
    created: int | float | None = None
    updated: int | float | None = None
    messages: list[ScrapedMessage] = Field(repr=False)


class ScraperExport(SourceModel):
    """Failure entries remain only in the verbatim source, never normalized error text."""

    exporter_version: str
    conversations: list[ScrapedConversation] = Field(repr=False)
    failed_conversations: list[JsonValue] = Field(
        default_factory=list[JsonValue], validation_alias="failedConversations", repr=False
    )


def source_time(value: JsonValue) -> SourceTimestamp:
    """Interpret finite Unix-second candidates; unknown clocks never become import time."""
    if value is None:
        return SourceTimestamp()
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and abs(value) < 100000000000
    ):
        try:
            microseconds = int(Decimal(str(value)) * 1000000)
            return SourceTimestamp(
                value=timestamp_from_unix_microseconds(microseconds),
                raw_value=value,
                units="seconds",
                precision="source_numeric",
                basis="unix_seconds_adapter",
                timezone_assumption="UTC",
            )
        except (ValueError, OverflowError):
            pass
    return SourceTimestamp(ambiguity_reasons=("unsupported_source_clock",))


def _identities(
    target: ImportTarget, adapter: str, keys: list[tuple[str, str]]
) -> tuple[RecordIdentity, ...]:
    if len(keys) > 10000:
        raise SourceImportError("Conversation exceeds the 10,000 record bound.")
    result: list[RecordIdentity] = []
    for offset in range(0, len(keys), 512):
        result.extend(
            target.identify(
                tuple(
                    RecordKey(adapter=adapter, record_kind=kind, external_id=key)
                    for kind, key in keys[offset : offset + 512]
                )
            )
        )
    return tuple(result)


def _reference(target: ImportTarget, identity: RecordIdentity) -> SourceRef:
    return SourceRef(
        source_store_id=target.source_store_id,
        record_kind=identity.key.record_kind,
        record_id=identity.record_id,
        record_revision=1,
    )


def _graph_node_key(
    conversation_id: str,
    node_id: str,
    pair_key: Callable[[str, str], str] | None,
) -> str:
    return (
        pair_key(conversation_id, node_id)
        if pair_key is not None
        else conversation_id + "/" + node_id
    )


def _part(
    target: ImportTarget,
    reference: SourceRef,
    text: str | None,
    *,
    visible: bool,
    fixed_id: CanonicalId | None = None,
) -> ContentPart:
    from uuid import uuid4

    previous = target.latest(reference.record_kind, reference.record_id)
    part_id = fixed_id or (
        previous.parts[0].part_id if isinstance(previous, Message) and previous.parts else uuid4()
    )
    if not visible:
        return ContentPart(
            part_id=part_id,
            kind="text",
            status="excluded",
            exclusion_reason="channel_or_role_unverified",
        )
    if not text:
        return ContentPart(part_id=part_id, kind="text", status="unavailable")
    return ContentPart(
        part_id=part_id,
        kind="text",
        status="available",
        text=text,
        content_sha256=source_text_sha256(text),
    )


def scraper_units(
    value: JsonValue, target: ImportTarget
) -> tuple[list[tuple[tuple[RecordIdentity, ...], tuple[SourceRecord, ...]]], int]:
    """Normalize explicit user observations; retain ambiguity and exclude unverified output."""
    try:
        exported = ScraperExport.model_validate(value)
    except ValidationError as error:
        raise SourceImportError("JSON does not match the supported scraper schema.") from error
    units: list[tuple[tuple[RecordIdentity, ...], tuple[SourceRecord, ...]]] = []
    seen: set[str] = set()
    for conversation in exported.conversations:
        if conversation.id in seen:
            raise SourceImportError("Duplicate conversation identities are ambiguous.")
        seen.add(conversation.id)
        keys = [("conversation", conversation.id)]
        counts: Counter[str] = Counter()
        for message in conversation.messages:
            fingerprint = canonical_object_sha256(message.model_dump(mode="json"))
            counts[fingerprint] += 1
            keys.append(
                ("message", conversation.id + "/" + fingerprint + "/" + str(counts[fingerprint]))
            )
        identities = _identities(target, "scraper_json", keys)
        refs = [_reference(target, identity) for identity in identities]
        records: list[SourceRecord] = [
            Conversation(
                source=refs[0],
                recorded_at=STAMP,
                title=conversation.title,
                created_time=source_time(conversation.created),
                updated_time=source_time(conversation.updated),
                selection=BranchSelection(
                    status="unavailable", reason_codes=("source_graph_not_exported",)
                ),
            )
        ]
        for message, reference in zip(conversation.messages, refs[1:], strict=True):
            records.append(
                Message(
                    source=reference,
                    recorded_at=STAMP,
                    conversation=refs[0],
                    actor=ActorAttribution(role=message.role),
                    created_time=source_time(message.time),
                    parts=(_part(target, reference, message.text, visible=message.role == "user"),),
                )
            )
        units.append((identities, tuple(records)))
    return units, len(exported.failed_conversations)


def graph_units(
    value: JsonValue,
    target: ImportTarget,
    *,
    part_ids: Mapping[CanonicalId, tuple[CanonicalId, ...]] | None = None,
    key_adapter: str = "chatgpt_mapping",
    pair_key: Callable[[str, str], str] | None = None,
) -> list[tuple[tuple[RecordIdentity, ...], tuple[SourceRecord, ...]]]:
    """Preserve mapping nodes/edges without silently resolving ambiguous branches.

    The default provider keys preserve the original JSON adapter's existing identity
    contract. ZIP imports can supply a separately versioned, unambiguous pair encoding.
    """
    if isinstance(value, dict) and "conversations" in value:
        value = value["conversations"]
    if isinstance(value, dict) and "mapping" in value:
        value = [value]
    try:
        conversations = TypeAdapter(list[SourceConversation]).validate_python(value)
    except ValidationError as error:
        raise SourceImportError(
            "JSON does not match the supported mapping-graph schema."
        ) from error
    units: list[tuple[tuple[RecordIdentity, ...], tuple[SourceRecord, ...]]] = []
    seen: set[str] = set()
    for conversation in conversations:
        external = conversation.id or conversation.conversation_id
        if not external or external in seen:
            raise SourceImportError("Conversation identity is missing or duplicated.")
        seen.add(external)

        keys = [("conversation", external)]
        keys.extend(
            ("graph_node", _graph_node_key(external, key, pair_key)) for key in conversation.mapping
        )
        keys.extend(
            ("message", _graph_node_key(external, key, pair_key))
            for key, node in conversation.mapping.items()
            if node.message is not None
        )
        identities = _identities(target, key_adapter, keys)
        refs = {
            (identity.key.record_kind, identity.key.external_id): _reference(target, identity)
            for identity in identities
        }
        conv_ref = refs["conversation", external]
        node_refs = {
            key: refs["graph_node", _graph_node_key(external, key, pair_key)]
            for key in conversation.mapping
        }
        path: list[SourceRef] = []
        walked: set[str] = set()
        current = conversation.current_node
        while current is not None and current in conversation.mapping and current not in walked:
            walked.add(current)
            path.append(node_refs[current])
            current = conversation.mapping[current].parent
        reasons: list[str] = []
        if not path or current is not None:
            reasons.append("selected_path_unresolved")
        for key, node in conversation.mapping.items():
            if node.id is not None and node.id != key:
                reasons.append("node_identity_ambiguous")
            if node.parent and (
                node.parent not in conversation.mapping
                or key not in (conversation.mapping[node.parent].children or [])
            ):
                reasons.append("graph_relationship_ambiguous")
            if len(node.children or []) != len(set(node.children or [])):
                reasons.append("duplicate_graph_children")
            for child in node.children or []:
                if child not in conversation.mapping or conversation.mapping[child].parent != key:
                    reasons.append("graph_relationship_ambiguous")
        path.reverse()
        selection = (
            BranchSelection(status="ambiguous", reason_codes=tuple(sorted(set(reasons))))
            if reasons
            else BranchSelection(status="resolved", selected_node=path[-1], path=tuple(path))
        )
        records: list[SourceRecord] = [
            Conversation(
                source=conv_ref,
                recorded_at=STAMP,
                title=conversation.title,
                created_time=source_time(conversation.create_time),
                updated_time=source_time(conversation.update_time),
                archived=conversation.is_archived,
                deleted=conversation.is_deleted,
                selection=selection,
            )
        ]

        def link(key: str, references: dict[str, SourceRef] = node_refs) -> SourceLink:
            if key in references:
                return SourceLink(status="resolved", target=references[key])
            return SourceLink(status="missing", raw_reference_sha256=source_text_sha256(key))

        for key, node in conversation.mapping.items():
            message_ref = refs.get(("message", _graph_node_key(external, key, pair_key)))
            records.append(
                GraphNode(
                    source=node_refs[key],
                    recorded_at=STAMP,
                    conversation=conv_ref,
                    parent=link(node.parent) if node.parent else None,
                    children=tuple(link(child) for child in node.children or []),
                    message=message_ref,
                )
            )
            message = node.message
            if message is None or message_ref is None:
                continue
            role = message.author.role if message.author and message.author.role else "unknown"
            content = message.content
            text = content.text if content else None
            source_parts: list[JsonValue] = (
                list(content.parts)
                if content and content.parts is not None
                else [text]
                if text is not None
                else []
            )
            visible = graph_message_visible(message, role)
            old = target.latest("message", message_ref.record_id)
            parts: list[ContentPart] = []
            from uuid import uuid4

            for index, raw_part in enumerate(source_parts):
                part_text = raw_part if isinstance(raw_part, str) else None
                part_id = (
                    part_ids[message_ref.record_id][index]
                    if part_ids is not None
                    else old.parts[index].part_id
                    if isinstance(old, Message) and index < len(old.parts)
                    else uuid4()
                )
                parts.append(
                    ContentPart(
                        part_id=part_id,
                        kind="text",
                        status="available",
                        text=part_text,
                        content_sha256=source_text_sha256(part_text),
                    )
                    if visible and part_text
                    else ContentPart(
                        part_id=part_id,
                        status=(
                            "excluded"
                            if not visible
                            else "unsupported"
                            if part_text is None
                            else "unavailable"
                        ),
                        kind="text" if part_text is not None else "unsupported_source_part",
                        exclusion_reason=(
                            "unverified_or_reasoning_channel"
                            if not visible
                            else "unsupported_content_part"
                            if part_text is None
                            else None
                        ),
                    )
                )
            if not parts:
                parts.append(
                    _part(
                        target,
                        message_ref,
                        None,
                        visible=visible,
                        fixed_id=part_ids[message_ref.record_id][0]
                        if part_ids is not None
                        else None,
                    )
                )
            records.append(
                Message(
                    source=message_ref,
                    recorded_at=STAMP,
                    conversation=conv_ref,
                    actor=ActorAttribution(role=role),
                    channel=message.channel,
                    created_time=source_time(message.create_time),
                    updated_time=source_time(message.update_time),
                    parts=tuple(parts),
                )
            )
        units.append((identities, tuple(records)))
    return units
