"""Validated, content-free inspection reports; these are not Memory ABI models."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

type JsonKind = Literal["null", "boolean", "number", "string", "array", "object"]
type ComponentKind = Literal[
    "conversation_json_candidate",
    "html_history_candidate",
    "memory_named_component_unverified",
    "account_metadata_candidate",
    "asset_candidate",
    "other",
]
type PositiveInteger = Annotated[int, Field(gt=0)]
type NonnegativeInteger = Annotated[int, Field(ge=0)]
type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class InspectionModel(BaseModel):
    """Reject implicit field coercion and unexpected fields at report construction."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class InspectionLimits(InspectionModel):
    """Positive resource bounds, applied before and during member decompression."""

    max_members: PositiveInteger = 100_000
    max_total_bytes: PositiveInteger = 8 * 1024**3
    max_member_bytes: PositiveInteger = 2 * 1024**3
    max_json_bytes: PositiveInteger = 64 * 1024**2
    max_compression_ratio: PositiveInteger = 1_000


class UnsupportedConversationStructure(InspectionModel):
    """An unrecognized component shape with no automatic branch interpretation."""

    status: Literal["unsupported_root_shape"] = "unsupported_root_shape"
    root_type: JsonKind
    automatic_branch_activation: Literal["blocked"] = "blocked"


class ConversationStructure(InspectionModel):
    """Aggregate shapes/counts; no source identifiers, text, or reasoning values."""

    status: Literal["structurally_inspected"] = "structurally_inspected"
    wrapper: Literal["array", "object_with_conversations_array"]
    counts: dict[str, NonnegativeInteger]
    roles: dict[str, NonnegativeInteger]
    channels: dict[str, NonnegativeInteger]
    content_types: dict[str, NonnegativeInteger]
    non_reasoning_part_types: dict[str, NonnegativeInteger]
    timestamp_representations: dict[str, NonnegativeInteger]
    archive_deletion_marker_types: dict[str, NonnegativeInteger]
    field_shapes: dict[str, dict[str, NonnegativeInteger]]
    automatic_branch_activation: Literal["blocked", "not_evaluated"]
    limitations: tuple[str, ...]


type ConversationInspection = Annotated[
    ConversationStructure | UnsupportedConversationStructure, Field(discriminator="status")
]


class ConversationComponent(InspectionModel):
    """Bind structural coverage to a hashed member path without disclosing its name."""

    path_sha256: Sha256
    structure: ConversationInspection


class InventoryEntry(InspectionModel):
    """A hashed source member, with explicit JSON coverage and optional private path."""

    path_sha256: Sha256
    component: ComponentKind
    size_bytes: NonnegativeInteger
    compressed_bytes: NonnegativeInteger
    content_sha256: Sha256
    relative_path: str | None = None
    json_status: (
        Literal["deferred_size_limit", "structurally_inspected", "invalid_or_ambiguous_json"] | None
    ) = None
    json_shape: dict[str, str] | None = None


class SourceInventory(InspectionModel):
    """Exact container identity and deterministic logical file inventory identity."""

    kind: Literal["zip"] = "zip"
    raw_sha256: Sha256
    logical_inventory_sha256: Sha256
    archive_size_bytes: NonnegativeInteger
    members_including_directories: NonnegativeInteger
    files: NonnegativeInteger
    uncompressed_bytes: NonnegativeInteger


class InspectionPrivacy(InspectionModel):
    """Describe omissions without including any omitted source values."""

    message_content: Literal["excluded"] = "excluded"
    titles_and_external_ids: Literal["excluded"] = "excluded"
    reasoning_content: Literal["excluded"] = "excluded"
    member_paths: Literal["hashed", "included_sensitive"]


class ChatGPTCoverage(InspectionModel):
    """Observed component coverage, without assuming saved memories are exported."""

    conversation_components: tuple[ConversationComponent, ...]
    saved_memories: Literal["not_established"] = "not_established"
    actual_export_capabilities: Literal["require_phase_0_review"] = "require_phase_0_review"


class InspectionOperations(InspectionModel):
    """Read-only inspection cannot advertise mutation, model, or remote-fetch effects."""

    source_modified: Literal[False] = False
    extraction: Literal[False] = False
    remote_fetch: Literal[False] = False
    canonical_writes: Literal[False] = False
    model_calls: Literal[False] = False


class ReconnaissanceReport(InspectionModel):
    """Provisional serialized preparation report, distinct from canonical history."""

    # Existing reports remain readable; new inspections use the ember namespace.
    report_format: Literal[
        "ember-memory.reconnaissance/0.0.1", "character-memory.reconnaissance/0.0.1"
    ] = "ember-memory.reconnaissance/0.0.1"
    phase: Literal["-1-tooling/0-input-inspection"] = "-1-tooling/0-input-inspection"
    generated_at: Annotated[str, Field(pattern=r"Z$")]
    source_namespace: Annotated[str, Field(min_length=1)]
    source: SourceInventory
    limits: InspectionLimits
    privacy: InspectionPrivacy
    inventory: tuple[InventoryEntry, ...]
    chatgpt: ChatGPTCoverage
    warnings: tuple[str, ...]
    operations: InspectionOperations = Field(default_factory=InspectionOperations)
