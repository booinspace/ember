"""Content-free structural observations, not an importer or a schema declaration.

Only fixed vocabulary or hashed unknown labels leave this module. Message text,
titles, external IDs, attachment locators, and reasoning are never returned.
"""

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import JsonValue

from ..report_models import (
    ConversationInspection,
    ConversationStructure,
    JsonKind,
    UnsupportedConversationStructure,
)

KNOWN_FIELDS = frozenset(
    {
        "id",
        "conversation_id",
        "title",
        "create_time",
        "update_time",
        "mapping",
        "current_node",
        "is_archived",
        "is_deleted",
        "parent",
        "children",
        "message",
        "author",
        "role",
        "name",
        "metadata",
        "content",
        "content_type",
        "parts",
        "text",
        "conversations",
        "channel",
        "recipient",
        "status",
        "end_turn",
        "weight",
        "model_slug",
        "default_model_slug",
        "finish_details",
        "timestamp_",
        "attachments",
        "file_id",
        "asset_pointer",
        "image_asset_pointer",
        "audio_asset_pointer",
        "message_type",
    }
)
KNOWN_ROLES = frozenset({"user", "assistant", "system", "tool", "developer"})
KNOWN_CHANNELS = frozenset({"analysis", "reasoning", "final", "commentary", "summary"})
KNOWN_CONTENT = frozenset(
    {
        "text",
        "multimodal_text",
        "image",
        "audio",
        "video",
        "code",
        "execution_output",
        "reasoning",
        "thoughts",
        "reasoning_recap",
        "model_editable_context",
        "user_editable_context",
    }
)
REASONING_CHANNELS = frozenset({"analysis", "reasoning"})
REASONING_CONTENT = frozenset({"reasoning", "thoughts", "reasoning_recap"})
ANOMALIES = (
    "malformed_conversations",
    "missing_or_invalid_mapping",
    "missing_or_invalid_current_node",
    "parent_cycles",
    "malformed_nodes",
    "missing_parent_references",
    "missing_child_references",
    "asymmetric_parent_edges",
    "asymmetric_child_edges",
    "duplicate_child_edges",
    "missing_or_invalid_children",
    "malformed_messages",
    "malformed_content",
    "missing_or_invalid_parts",
)
type FieldLevel = Literal["conversation", "node", "message", "metadata", "content"]


def kind(value: JsonValue) -> JsonKind:
    """Classify a narrowed JSON value without exposing its content."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def safe_label(value: JsonValue, known: frozenset[str]) -> str:
    """Return a fixed label or hash; unknown strings never appear verbatim."""
    if isinstance(value, str):
        if value in known:
            return value
        return "unknown_sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
    return "value_type:" + kind(value)


def field_shapes(value: JsonValue) -> dict[str, str]:
    """Describe root fields with sanitized keys and JSON kinds, without values."""
    if not isinstance(value, dict):
        return {"root_type": kind(value)}
    return {safe_label(key, KNOWN_FIELDS): kind(item) for key, item in sorted(value.items())}


def _timestamp(value: JsonValue) -> str:
    if value is None:
        return "unknown_null"
    # Integers can exceed float range; representation inspection must not overflow.
    if isinstance(value, int) and not isinstance(value, bool):
        return "numeric_unix_candidate"
    if isinstance(value, float):
        return "numeric_unix_candidate" if math.isfinite(value) else "invalid_nonfinite"
    if isinstance(value, str):
        return "string_requires_format_timezone_reconnaissance"
    return "unsupported_type:" + kind(value)


def _count_parent_cycles(mapping: dict[str, JsonValue]) -> int:
    finished: set[str] = set()
    cycle_count = 0
    for start in mapping:
        path: set[str] = set()
        current = start
        while current in mapping and current not in finished:
            if current in path:
                cycle_count += 1
                break
            path.add(current)
            node = mapping[current]
            parent = node.get("parent") if isinstance(node, dict) else None
            if not isinstance(parent, str):
                break
            current = parent
        finished.update(path)
    return cycle_count


@dataclass
class _Observations:
    # Mutable counters belong to one inspection; they are not serialized contracts.
    counts: Counter[str] = field(default_factory=Counter[str])
    roles: Counter[str] = field(default_factory=Counter[str])
    channels: Counter[str] = field(default_factory=Counter[str])
    content_types: Counter[str] = field(default_factory=Counter[str])
    part_types: Counter[str] = field(default_factory=Counter[str])
    timestamps: Counter[str] = field(default_factory=Counter[str])
    marker_types: Counter[str] = field(default_factory=Counter[str])
    fields: dict[FieldLevel, Counter[str]] = field(
        default_factory=lambda: {
            "conversation": Counter(),
            "node": Counter(),
            "message": Counter(),
            "metadata": Counter(),
            "content": Counter(),
        }
    )

    def observe_fields(self, level: FieldLevel, value: JsonValue) -> None:
        """Count sanitized field/type combinations without retaining source values."""
        if isinstance(value, dict):
            for key, item in value.items():
                self.fields[level][safe_label(key, KNOWN_FIELDS) + ":" + kind(item)] += 1


def _observe_content(content: JsonValue, channel: JsonValue, observations: _Observations) -> None:
    observations.observe_fields("content", content)
    if not isinstance(content, dict):
        observations.counts["malformed_content"] += 1
        return
    content_type = content.get("content_type")
    observations.content_types[safe_label(content_type, KNOWN_CONTENT)] += 1
    if (isinstance(channel, str) and channel in REASONING_CHANNELS) or (
        isinstance(content_type, str) and content_type in REASONING_CONTENT
    ):
        observations.counts["reasoning_messages_observed_excluded"] += 1
        return
    if isinstance(content.get("text"), str):
        observations.counts["messages_with_text_field"] += 1
        if "parts" not in content:
            observations.part_types["string"] += 1
            return
    parts = content.get("parts")
    if not isinstance(parts, list):
        observations.counts["missing_or_invalid_parts"] += 1
        return
    for part in parts:
        observations.part_types[kind(part)] += 1
        if isinstance(part, dict):
            observations.counts["structured_attachment_reference_candidates"] += sum(
                key in part
                for key in (
                    "file_id",
                    "asset_pointer",
                    "image_asset_pointer",
                    "audio_asset_pointer",
                )
            )


def _observe_message(message: JsonValue, observations: _Observations) -> None:
    if message is None:
        observations.counts["nodes_without_message"] += 1
        return
    if not isinstance(message, dict):
        observations.counts["malformed_messages"] += 1
        return
    observations.counts["messages"] += 1
    observations.observe_fields("message", message)
    for key in ("create_time", "update_time"):
        observations.timestamps["message." + key + ":" + _timestamp(message.get(key))] += 1
    author = message.get("author")
    role = author.get("role") if isinstance(author, dict) else None
    observations.roles[safe_label(role, KNOWN_ROLES)] += 1
    metadata = message.get("metadata")
    observations.observe_fields("metadata", metadata)
    if isinstance(metadata, dict):
        observations.counts["messages_with_model_metadata"] += int("model_slug" in metadata)
        attachments = metadata.get("attachments")
        if isinstance(attachments, list):
            observations.counts["attachment_metadata_entries"] += len(attachments)
    channel = message.get("channel")
    # This fallback observes export structure; it does not establish canonical semantics.
    if channel is None and isinstance(metadata, dict):
        channel = metadata.get("channel")
    observations.channels[safe_label(channel, KNOWN_CHANNELS)] += 1
    _observe_content(message.get("content"), channel, observations)


def _observe_parent(
    node_id: str, parent: JsonValue, mapping: dict[str, JsonValue], counts: Counter[str]
) -> None:
    if parent is None:
        counts["root_nodes"] += 1
    elif not isinstance(parent, str) or parent not in mapping:
        counts["missing_parent_references"] += 1
    else:
        parent_node = mapping[parent]
        children = parent_node.get("children") if isinstance(parent_node, dict) else None
        if not isinstance(children, list) or node_id not in children:
            counts["asymmetric_parent_edges"] += 1


def _observe_children(
    node_id: str, children: JsonValue, mapping: dict[str, JsonValue], counts: Counter[str]
) -> None:
    if not isinstance(children, list):
        counts["missing_or_invalid_children"] += 1
        return
    counts["branch_points"] += int(len(children) > 1)
    counts["child_edges"] += len(children)
    seen: set[str] = set()
    for child in children:
        if not isinstance(child, str) or child not in mapping:
            counts["missing_child_references"] += 1
            continue
        counts["duplicate_child_edges"] += int(child in seen)
        seen.add(child)
        child_node = mapping[child]
        if not isinstance(child_node, dict) or child_node.get("parent") != node_id:
            counts["asymmetric_child_edges"] += 1


def _observe_conversation(conversation: JsonValue, observations: _Observations) -> None:
    observations.counts["conversations"] += 1
    if not isinstance(conversation, dict):
        observations.counts["malformed_conversations"] += 1
        return
    observations.observe_fields("conversation", conversation)
    for key in ("create_time", "update_time"):
        observations.timestamps[
            "conversation." + key + ":" + _timestamp(conversation.get(key))
        ] += 1
    for key in ("is_archived", "is_deleted"):
        if key in conversation:
            observations.marker_types[key + ":" + kind(conversation[key])] += 1
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        observations.counts["missing_or_invalid_mapping"] += 1
        return
    selected = conversation.get("current_node")
    if not isinstance(selected, str) or selected not in mapping:
        observations.counts["missing_or_invalid_current_node"] += 1
    observations.counts["parent_cycles"] += _count_parent_cycles(mapping)
    for node_id, node in mapping.items():
        observations.counts["nodes"] += 1
        if not isinstance(node, dict):
            observations.counts["malformed_nodes"] += 1
            continue
        observations.observe_fields("node", node)
        _observe_parent(node_id, node.get("parent"), mapping, observations.counts)
        _observe_children(node_id, node.get("children"), mapping, observations.counts)
        _observe_message(node.get("message"), observations)


class ConversationObserver:
    """Accumulate sanitized statistics while retaining no conversations between calls."""

    def __init__(self) -> None:
        self._observations = _Observations()

    def observe(self, conversation: JsonValue) -> None:
        """Inspect one transient source graph; never select or normalize its branches."""
        _observe_conversation(conversation, self._observations)
        counters = (
            self._observations.roles,
            self._observations.channels,
            self._observations.content_types,
            *self._observations.fields.values(),
        )
        for counter in counters:
            if len(counter) > 1024:
                overflow = sorted(key for key in counter if key.startswith("unknown_sha256:"))
                for key in overflow[: max(0, len(counter) - 1023)]:
                    counter["other_unknown_labels"] += counter.pop(key)

    def finish(
        self,
        wrapper: Literal["array", "object_with_conversations_array", "single_conversation"],
    ) -> ConversationStructure:
        """Return immutable report counters; source text and identifiers are excluded."""
        return _finish_observations(self._observations, wrapper)


def _finish_observations(
    observations: _Observations,
    wrapper: Literal["array", "object_with_conversations_array", "single_conversation"],
) -> ConversationStructure:
    return ConversationStructure(
        wrapper=wrapper,
        counts=dict(sorted(observations.counts.items())),
        roles=dict(sorted(observations.roles.items())),
        channels=dict(sorted(observations.channels.items())),
        content_types=dict(sorted(observations.content_types.items())),
        non_reasoning_part_types=dict(sorted(observations.part_types.items())),
        timestamp_representations=dict(sorted(observations.timestamps.items())),
        archive_deletion_marker_types=dict(sorted(observations.marker_types.items())),
        field_shapes={
            level: dict(sorted(values.items())) for level, values in observations.fields.items()
        },
        automatic_branch_activation=(
            "blocked" if any(observations.counts[key] for key in ANOMALIES) else "not_evaluated"
        ),
        limitations=(
            "Each label counter is bounded to 1024 labels; excess unknown hashes "
            "are aggregated as other_unknown_labels.",
            "Counts are structural observations; "
            "no branches, edits, or regenerations are normalized.",
            "Timestamp formats/timezones and attachment resolution require actual-export review.",
            "Saved memories, actor identity, and tool semantics "
            "are not inferred from message content.",
        ),
    )


def inspect_conversations(value: JsonValue) -> ConversationInspection:
    """Inspect supported envelopes; graph anomalies block automatic interpretation."""
    observer = ConversationObserver()
    if isinstance(value, list):
        for conversation in value:
            observer.observe(conversation)
        return observer.finish("array")
    if isinstance(value, dict):
        conversations = value.get("conversations")
        if isinstance(conversations, list):
            for conversation in conversations:
                observer.observe(conversation)
            return observer.finish("object_with_conversations_array")
        if isinstance(value.get("mapping"), dict):
            observer.observe(value)
            return observer.finish("single_conversation")
    return UnsupportedConversationStructure(root_type=kind(value))
