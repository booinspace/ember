"""Normalized Archive 0.1: generic source records, graph ambiguity, and atomic batches.

Provider-specific keys/physical locators stay in importer/engine crosswalks. These
contracts are independent of ChatGPT layouts and create no canonical persistence.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .common import (
    CanonicalId,
    ContractModel,
    NonnegativeInteger,
    Revision,
    Sha256,
    UtcTimestamp,
    WireString,
    unsupported_extension_names,
)
from .errors import UnsupportedWriteError
from .evidence import SourceRef, source_text_sha256
from .temporal import SourceTimestamp


class ProcessingSpecRef(ContractModel):
    """An independently versioned processing specification, without runtime dependencies."""

    name: WireString
    version: WireString
    specification_sha256: Sha256


class SourceLink(ContractModel):
    """Preserve missing/invalid graph links rather than flattening or repairing them."""

    status: Literal["resolved", "missing", "invalid"]
    target: SourceRef | None = None
    raw_reference_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def _resolution(self) -> Self:
        if (self.status == "resolved") != (self.target is not None):
            raise ValueError("Only resolved source links have canonical targets.")
        return self


class BranchSelection(ContractModel):
    """A derived selection observation; ambiguous paths cannot authorize activation."""

    status: Literal["resolved", "ambiguous", "unavailable"]
    selected_node: SourceRef | None = None
    path: tuple[SourceRef, ...] = ()
    reason_codes: tuple[WireString, ...] = ()

    @model_validator(mode="after")
    def _selection(self) -> Self:
        if self.status == "resolved":
            if (
                self.selected_node is None
                or not self.path
                or self.path[-1].identity_key() != self.selected_node.identity_key()
            ):
                raise ValueError(
                    "A resolved selection requires a path ending at the selected node."
                )
        elif not self.reason_codes:
            raise ValueError(
                "An unresolved selection requires explicit ambiguity/coverage reasons."
            )
        return self

    @property
    def branch_dependent_activation_blocked(self) -> bool:
        """Only branch ambiguity is evaluated here; other activation/grant checks still apply."""
        return self.status != "resolved"


class ContentPart(ContractModel):
    """Ordered visible text or a typed absence, unsupported part, or reasoning exclusion."""

    part_id: CanonicalId
    kind: WireString
    status: Literal["available", "unavailable", "unsupported", "excluded"]
    text: str | None = Field(default=None, repr=False)
    content_sha256: Sha256 | None = None
    asset: SourceRef | None = None
    exclusion_reason: WireString | None = None

    @model_validator(mode="after")
    def _content(self) -> Self:
        if self.status == "excluded" or self.kind in {"analysis", "reasoning", "thoughts"}:
            if self.text is not None or self.asset is not None:
                raise ValueError("Excluded reasoning cannot enter normalized content.")
            if self.status != "excluded" or self.exclusion_reason is None:
                raise ValueError("Reasoning exclusions require an explicit status and reason.")
        if self.text is not None:
            if self.status != "available" or self.content_sha256 != source_text_sha256(self.text):
                raise ValueError(
                    "Visible text must have its exact UTF-8 hash and available status."
                )
        elif self.status == "available" and self.asset is None:
            raise ValueError("An available part requires visible text or an asset reference.")
        return self


class ArchiveRecord(ContractModel):
    """Canonical source revision; recorded time is a maintenance clock only."""

    source: SourceRef
    recorded_at: UtcTimestamp

    def _check_record_kind(self, expected: str) -> None:
        if self.source.record_kind != expected or self.source.part_id is not None:
            raise ValueError(
                "Archive record identity must match its record kind without a part ID."
            )

    def _check_same_store(self, reference: SourceRef) -> None:
        if reference.source_store_id != self.source.source_store_id:
            raise ValueError(
                "Archive graph/content references must stay within their source store."
            )

    def _check_relationship(self, reference: SourceRef, kind: str) -> None:
        self._check_same_store(reference)
        if reference.record_kind != kind or reference.part_id is not None:
            raise ValueError("Archive relationships refer to whole records of the expected kind.")


class Conversation(ArchiveRecord):
    """Normalized conversation identity and source metadata, independent of graph messages."""

    record_type: Literal["conversation"] = "conversation"
    title: str | None = Field(default=None, repr=False)
    created_time: SourceTimestamp = Field(default_factory=SourceTimestamp)
    updated_time: SourceTimestamp = Field(default_factory=SourceTimestamp)
    archived: bool | None = None
    deleted: bool | None = None
    selection: BranchSelection | None = None

    @model_validator(mode="after")
    def _identity(self) -> Self:
        self._check_record_kind(self.record_type)
        if self.selection is not None:
            for reference in (
                *self.selection.path,
                *((self.selection.selected_node,) if self.selection.selected_node else ()),
            ):
                self._check_relationship(reference, "graph_node")
        return self


class GraphNode(ArchiveRecord):
    """Retain null-message roots and original parent/child observations, including disagreements."""

    record_type: Literal["graph_node"] = "graph_node"
    conversation: SourceRef
    parent: SourceLink | None = None
    children: tuple[SourceLink, ...] = ()
    message: SourceRef | None = None

    @model_validator(mode="after")
    def _identity(self) -> Self:
        self._check_record_kind(self.record_type)
        self._check_relationship(self.conversation, "conversation")
        for link in (*self.children, *((self.parent,) if self.parent else ())):
            if link.target is not None:
                self._check_relationship(link.target, "graph_node")
        if self.message is not None:
            self._check_relationship(self.message, "message")
        return self


class ActorAttribution(ContractModel):
    """Source actor claims, without silently resolving role/name into personal identity."""

    role: WireString
    actor_id: CanonicalId | None = None
    display_name: str | None = Field(default=None, repr=False)


class Message(ArchiveRecord):
    """Normalized visible parts and explicit exclusions; graph membership is stored separately."""

    record_type: Literal["message"] = "message"
    conversation: SourceRef
    actor: ActorAttribution
    channel: WireString | None = None
    recipient: WireString | None = None
    created_time: SourceTimestamp = Field(default_factory=SourceTimestamp)
    updated_time: SourceTimestamp = Field(default_factory=SourceTimestamp)
    parts: tuple[ContentPart, ...] = ()

    @model_validator(mode="after")
    def _identity_and_privacy(self) -> Self:
        self._check_record_kind(self.record_type)
        self._check_relationship(self.conversation, "conversation")
        if len({part.part_id for part in self.parts}) != len(self.parts):
            raise ValueError("Content part identities must be unique within a revision.")
        for part in self.parts:
            if part.asset is not None:
                self._check_same_store(part.asset)
        if self.channel in {"analysis", "reasoning"} and any(
            part.status != "excluded" for part in self.parts
        ):
            raise ValueError("Reasoning channels may contain only explicit excluded parts.")
        return self


class Document(ArchiveRecord):
    """Generic preserved document/observation, using the same exact visible part semantics."""

    record_type: Literal["document"] = "document"
    title: str | None = Field(default=None, repr=False)
    created_time: SourceTimestamp = Field(default_factory=SourceTimestamp)
    parts: tuple[ContentPart, ...] = ()

    @model_validator(mode="after")
    def _identity(self) -> Self:
        self._check_record_kind(self.record_type)
        if len({part.part_id for part in self.parts}) != len(self.parts):
            raise ValueError("Content part identities must be unique within a revision.")
        for part in self.parts:
            if part.asset is not None:
                self._check_same_store(part.asset)
        return self


class Asset(ArchiveRecord):
    """Portable asset identity/availability; local paths live in backend locators."""

    record_type: Literal["asset"] = "asset"
    availability: Literal["available", "unavailable", "unresolved"]
    content_sha256: Sha256 | None = None
    size_bytes: NonnegativeInteger | None = None
    media_type: WireString | None = None

    @model_validator(mode="after")
    def _identity_and_availability(self) -> Self:
        self._check_record_kind(self.record_type)
        if self.availability == "available" and (
            self.content_sha256 is None or self.size_bytes is None
        ):
            raise ValueError("Available local assets require a hash and byte size.")
        return self


class OpaqueArchiveRecord(ArchiveRecord):
    """Read-preserved unsupported source kind; original bytes stay in the raw vault.

    This carries identity/coverage and namespaced metadata, never an unfiltered
    unknown content body. Normalization must separately exclude hidden reasoning.
    """

    record_type: Literal["opaque"] = "opaque"
    status: Literal["unsupported"] = "unsupported"
    reason_codes: tuple[WireString, ...]


type SourceRecord = Annotated[
    Conversation | GraphNode | Message | Document | Asset | OpaqueArchiveRecord,
    Field(discriminator="record_type"),
]


class ImportCursor(ContractModel):
    """Portable resumability against an immutable fingerprint, without physical file paths."""

    logical_inventory_sha256: Sha256
    component_sha256: Sha256
    next_record_index: NonnegativeInteger
    adapter: WireString
    adapter_version: WireString


class NormalizedArchiveBatch(ContractModel):
    """Maintenance write boundary: records and resume cursor must commit atomically.

    Namespace/store binding is established by trusted maintenance configuration;
    the writer verifies fingerprints, crosswalks, versions, and current ownership.
    """

    archive_version: Literal["0.1.0"] = "0.1.0"
    source_store_id: CanonicalId
    source_namespace: WireString
    idempotency_key: Annotated[str, Field(min_length=1, max_length=256)]
    processing_spec: ProcessingSpecRef
    records: Annotated[tuple[SourceRecord, ...], Field(min_length=1, max_length=1000)]
    resume_cursor: ImportCursor

    @model_validator(mode="after")
    def _bounded_identity(self) -> Self:
        identities: set[tuple[str, CanonicalId, Revision]] = set()
        for record in self.records:
            if record.source.source_store_id != self.source_store_id:
                raise ValueError("Batch records must belong to the configured source store.")
            key = (
                record.source.record_kind,
                record.source.record_id,
                record.source.record_revision,
            )
            if key in identities:
                raise ValueError("An archive batch cannot repeat the same canonical revision.")
            identities.add(key)
        return self


class ArchiveWriteReceipt(ContractModel):
    """A committed batch result; uncertain writes are never assumed safe to replay."""

    idempotency_key: WireString
    outcome: Literal["applied", "already_applied"]
    records_written: NonnegativeInteger
    resume_cursor: ImportCursor


def validate_archive_write(
    batch: NormalizedArchiveBatch,
    *,
    supported_extension_names: frozenset[str] = frozenset(),
) -> None:
    """Reject unsupported archive write semantics; storage still verifies source/crosswalks.

    Unsupported/excluded content parts with no body can record coverage gaps.
    Unknown available content and opaque record kinds cannot acquire semantics by
    merely passing schema validation. Missing graph links remain faithful observations.
    """
    if unsupported_extension_names(batch, supported_extension_names):
        raise UnsupportedWriteError()
    supported_parts = {
        "text",
        "code",
        "tool_output",
        "transcript",
        "image",
        "audio",
        "video",
        "file",
    }
    for record in batch.records:
        if isinstance(record, OpaqueArchiveRecord):
            raise UnsupportedWriteError()
        if isinstance(record, (Message, Document)) and any(
            part.status == "available" and part.kind not in supported_parts for part in record.parts
        ):
            raise UnsupportedWriteError()
