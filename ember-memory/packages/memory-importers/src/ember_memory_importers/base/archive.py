"""Bounded read-only ZIP/directory inventory; never normalizes or fetches sources."""

import hashlib
import json
import math
import os
import re
import stat
import zipfile
import zlib
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import SpooledTemporaryFile
from typing import Literal, Never

from pydantic import ConfigDict, JsonValue, TypeAdapter, ValidationError

from ..chatgpt.reconnaissance import field_shapes, inspect_conversations
from ..chatgpt.streaming import (
    ConversationJsonError,
    ConversationLimitError,
    inspect_conversation_stream,
)
from ..report_models import (
    ChatGPTCoverage,
    ComponentKind,
    ConversationComponent,
    InspectionLimits,
    InspectionPrivacy,
    InventoryEntry,
    ReconnaissanceReport,
    SourceInventory,
)
from .filesystem import DirectorySourceError, directory_files, file_signature
from .streams import BinaryReader, BinaryWriter


class ArchiveInspectionError(ValueError):
    """Inspection failed with a content-free diagnostic suitable for CLI output."""


class _SourceJsonError(ValueError):
    pass


@dataclass(frozen=True)
class _JsonInspection:
    status: Literal["deferred_size_limit", "structurally_inspected", "invalid_or_ambiguous_json"]
    shape: dict[str, str] | None = None
    conversation: ConversationComponent | None = None
    warning: str | None = None


@dataclass
class _InventoryState:
    limits: InspectionLimits
    include_paths: bool
    entries: list[InventoryEntry] = field(default_factory=list[InventoryEntry])
    conversations: list[ConversationComponent] = field(default_factory=list[ConversationComponent])
    warnings: set[str] = field(default_factory=set[str])
    total_bytes: int = 0
    members: int = 0


def _safe_path(info: zipfile.ZipInfo) -> str:
    # orig_filename retains names truncated by ZipInfo at NUL boundaries.
    original = info.orig_filename
    name = original.replace("\\", "/")
    pieces = name.rstrip("/").split("/")
    if (
        not name
        or "\x00" in original
        or name.startswith("/")
        or PureWindowsPath(original).drive
        or any(piece in {"", ".", ".."} or ":" in piece for piece in pieces)
    ):
        raise ArchiveInspectionError("Archive contains unsafe or ambiguous member paths.")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ArchiveInspectionError("Archive contains symlinks; inspection is refused.")
    if info.flag_bits & 1:
        raise ArchiveInspectionError("Encrypted archive members are unsupported.")
    return name


def _validated_members(
    members: list[zipfile.ZipInfo], limits: InspectionLimits
) -> list[tuple[zipfile.ZipInfo, str]]:
    if len(members) > limits.max_members:
        raise ArchiveInspectionError("Archive exceeds the member-count limit.")
    declared_bytes = 0
    seen: set[str] = set()
    validated: list[tuple[zipfile.ZipInfo, str]] = []
    # Validate the whole declared inventory before decompressing any source member.
    for info in members:
        relative = _safe_path(info)
        identity = relative.rstrip("/").casefold()
        if identity in seen:
            raise ArchiveInspectionError(
                "Archive contains duplicate or case-ambiguous member paths."
            )
        seen.add(identity)
        if info.file_size > limits.max_member_bytes:
            raise ArchiveInspectionError("Archive exceeds the per-member size limit.")
        declared_bytes += info.file_size
        if declared_bytes > limits.max_total_bytes:
            raise ArchiveInspectionError("Archive exceeds the total uncompressed-size limit.")
        if info.file_size > max(1, info.compress_size) * limits.max_compression_ratio:
            raise ArchiveInspectionError("Archive exceeds the compression-ratio limit.")
        validated.append((info, relative))
    return sorted(validated, key=lambda member: member[1])


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise _SourceJsonError("duplicate_json_keys")
        result[key] = value
    return result


def _reject_constant(_: str) -> Never:
    raise _SourceJsonError("nonfinite_json_number")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise _SourceJsonError("nonfinite_json_number")
    return result


def _component(path: str) -> ComponentKind:
    basename = PurePosixPath(path).name.lower()
    if re.fullmatch(r"conversations(?:[-_]\d+)?\.json", basename):
        return "conversation_json_candidate"
    if PurePosixPath(path).suffix.lower() == ".zip":
        return "nested_zip_candidate"
    if basename == "chat.html":
        return "html_history_candidate"
    if basename in {"memory.json", "memories.json", "memory.md", "memories.md"}:
        return "memory_named_component_unverified"
    if basename == "user.json":
        return "account_metadata_candidate"
    if PurePosixPath(path).suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".mp3",
        ".wav",
        ".mp4",
        ".pdf",
        ".dat",
        ".m4a",
        ".ogg",
    }:
        return "asset_candidate"
    return "other"


def _inspect_json(
    payload: bytearray | None, path_hash: str, component: ComponentKind
) -> _JsonInspection:
    if payload is None:
        return _JsonInspection(
            status="deferred_size_limit",
            warning="A JSON member was hashed but exceeds the bounded structural-parser limit.",
        )
    try:
        # The standard parser detects duplicate keys; validation immediately narrows its output.
        parsed: object = json.loads(
            payload.decode("utf-8-sig"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
        adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue, config=ConfigDict(strict=True))
        value = adapter.validate_python(parsed)
    except (UnicodeError, ValueError, RecursionError, OverflowError, ValidationError):
        # Invalid source JSON is an explicit coverage outcome, not an ignored exception.
        return _JsonInspection(
            status="invalid_or_ambiguous_json",
            warning="A JSON member is invalid, too deeply nested, or has ambiguous duplicate keys.",
        )
    conversation = (
        ConversationComponent(path_sha256=path_hash, structure=inspect_conversations(value))
        if component == "conversation_json_candidate"
        else None
    )
    return _JsonInspection("structurally_inspected", field_shapes(value), conversation)


def _stream_json(
    stream: BinaryReader, path_hash: str, chain: tuple[str, ...], limits: InspectionLimits
) -> _JsonInspection:
    try:
        inspected = inspect_conversation_stream(stream, limits)
    except ConversationLimitError:
        return _JsonInspection(
            "deferred_size_limit",
            warning="A conversation component exceeds a per-record parser bound; "
            "partial observations were discarded.",
        )
    except ConversationJsonError:
        return _JsonInspection(
            "invalid_or_ambiguous_json",
            warning="A conversation component contains invalid or ambiguous JSON; "
            "partial observations were discarded.",
        )
    return _JsonInspection(
        "structurally_inspected",
        inspected.shape,
        ConversationComponent(
            path_sha256=path_hash,
            container_path_sha256=chain,
            structure=inspected.structure,
        ),
    )


def _path_hash(relative: str, chain: tuple[str, ...]) -> str:
    # Flat path hashes stay compatible. Container chains disambiguate equal leaf names.
    identity = json.dumps([*chain, relative], separators=(",", ":")) if chain else relative
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _copy_bounded(
    stream: BinaryReader, retained: BinaryWriter | None, declared: int, state: _InventoryState
) -> str:
    digest = hashlib.sha256()
    read_bytes = 0
    while block := stream.read(1024 * 1024):
        read_bytes += len(block)
        state.total_bytes += len(block)
        if (
            read_bytes > declared
            or read_bytes > state.limits.max_member_bytes
            or state.total_bytes > state.limits.max_total_bytes
        ):
            raise ArchiveInspectionError("Actual source data exceeds configured limits.")
        digest.update(block)
        if retained is not None:
            retained.write(block)
    if read_bytes != declared:
        raise ArchiveInspectionError("Source data does not match its declared size.")
    return digest.hexdigest()


def _scan_file(
    stream: BinaryReader,
    relative: str,
    declared: int,
    compressed: int,
    chain: tuple[str, ...],
    state: _InventoryState,
) -> None:
    if declared > state.limits.max_member_bytes:
        raise ArchiveInspectionError("Source exceeds the per-member size limit.")
    component = _component(relative)
    path_hash = _path_hash(relative, chain)
    is_json = PurePosixPath(relative).suffix.lower() == ".json"
    nested = component == "nested_zip_candidate"
    descend = nested and len(chain) < state.limits.max_nested_depth
    retain = (
        descend
        or component == "conversation_json_candidate"
        or is_json
        and declared <= state.limits.max_json_bytes
    )
    with ExitStack() as owned:
        # Bounded temporary staging is deleted on every exit; no source tree is extracted.
        staged = owned.enter_context(SpooledTemporaryFile(max_size=8 * 1024**2)) if retain else None
        content_hash = _copy_bounded(stream, staged, declared, state)
        inspected: _JsonInspection | None = None
        if staged is not None:
            staged.seek(0)
        if component == "conversation_json_candidate" and staged is not None:
            inspected = _stream_json(staged, path_hash, chain, state.limits)
        elif is_json:
            inspected = _inspect_json(
                bytearray(staged.read()) if staged is not None else None, path_hash, component
            )
        container_status: Literal["inspected", "deferred_depth_limit"] | None = None
        if nested:
            container_status = "inspected" if descend else "deferred_depth_limit"
            if not descend:
                state.warnings.add("A nested ZIP was hashed but exceeds the container-depth limit.")
        state.entries.append(
            InventoryEntry(
                path_sha256=path_hash,
                container_path_sha256=chain,
                component=component,
                size_bytes=declared,
                compressed_bytes=compressed,
                content_sha256=content_hash,
                relative_path=relative if state.include_paths else None,
                json_status=inspected.status if inspected else None,
                json_shape=inspected.shape if inspected else None,
                container_status=container_status,
            )
        )
        if inspected is not None:
            if inspected.conversation is not None:
                state.conversations.append(inspected.conversation)
            if inspected.warning is not None:
                state.warnings.add(inspected.warning)
        if descend and staged is not None:
            staged.seek(0)
            with zipfile.ZipFile(staged) as archive:
                _scan_zip(archive, (*chain, path_hash), state)


def _scan_zip(archive: zipfile.ZipFile, chain: tuple[str, ...], state: _InventoryState) -> None:
    members = archive.infolist()
    validated = _validated_members(members, state.limits)
    state.members += len(members)
    if state.members > state.limits.max_members:
        raise ArchiveInspectionError("Combined containers exceed the member-count limit.")
    if state.total_bytes + sum(info.file_size for info in members) > state.limits.max_total_bytes:
        raise ArchiveInspectionError(
            "Combined containers exceed the total uncompressed-size limit."
        )
    for info, relative in validated:
        if not info.is_dir():
            with archive.open(info) as stream:
                _scan_file(stream, relative, info.file_size, info.compress_size, chain, state)


def _logical_hash(inventory: list[InventoryEntry]) -> str:
    # Hash only immutable identity/content fields, independently of ZIP order/compression.
    logical = [
        {
            "path_sha256": entry.path_sha256,
            "size_bytes": entry.size_bytes,
            "content_sha256": entry.content_sha256,
        }
        for entry in sorted(inventory, key=lambda member: member.path_sha256)
    ]
    encoded = json.dumps(logical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _report(
    state: _InventoryState,
    namespace: str,
    source_kind: Literal["zip", "directory"],
    raw_hash: str | None,
    size_bytes: int,
) -> ReconnaissanceReport:
    if not state.conversations:
        state.warnings.add(
            "No conversation JSON candidate was structurally inspected; "
            "export coverage is incomplete."
        )
    return ReconnaissanceReport(
        generated_at=datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        source_namespace=namespace,
        source=SourceInventory(
            kind=source_kind,
            raw_sha256=raw_hash,
            logical_inventory_sha256=_logical_hash(state.entries),
            archive_size_bytes=size_bytes,
            members_including_directories=state.members,
            files=len(state.entries),
            uncompressed_bytes=state.total_bytes,
        ),
        limits=state.limits,
        privacy=InspectionPrivacy(
            member_paths="included_sensitive" if state.include_paths else "hashed"
        ),
        inventory=tuple(state.entries),
        chatgpt=ChatGPTCoverage(conversation_components=tuple(state.conversations)),
        warnings=tuple(sorted(state.warnings)),
    )


def inspect_zip(
    path: Path,
    *,
    source_namespace: str,
    limits: InspectionLimits | None = None,
    include_member_paths: bool = False,
) -> ReconnaissanceReport:
    """Inspect ZIPs/shards/nested containers with shared bounds and no extraction.

    Conversation components stream one graph at a time; unrelated large JSON remains
    deferred. Unsafe archives, changing sources, or container safety limits fail with
    content-free diagnostics. Temporary staged components are removed on exit.
    This blocking maintenance API must run off a realtime loop.
    """
    if not source_namespace.strip():
        raise ArchiveInspectionError(
            "A source namespace is required; account equivalence is not inferred."
        )
    state = _InventoryState(limits or InspectionLimits(), include_member_paths)
    try:
        with path.open("rb") as raw:
            before = os.fstat(raw.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ArchiveInspectionError("Input must be a regular ZIP file.")
            raw_hash = hashlib.file_digest(raw, "sha256").hexdigest()
            raw.seek(0)
            with zipfile.ZipFile(raw) as archive:
                _scan_zip(archive, (), state)
            after = os.fstat(raw.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ArchiveInspectionError(
                    "Source changed during inspection; no report is valid."
                )
    except ArchiveInspectionError:
        raise
    except (
        OSError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        RuntimeError,
        NotImplementedError,
        EOFError,
        zlib.error,
    ) as error:
        raise ArchiveInspectionError(
            "Cannot read a valid supported ZIP archive; check input and integrity."
        ) from error
    return _report(state, source_namespace, "zip", raw_hash, before.st_size)


def inspect_directory(
    path: Path,
    *,
    source_namespace: str,
    limits: InspectionLimits | None = None,
    include_member_paths: bool = False,
) -> ReconnaissanceReport:
    """Fingerprint an existing directory without following links or mutating files.

    Directory identity is the logical inventory, not a fictional raw ZIP hash.
    File identity/size/mtime and the complete directory inventory are checked again
    before a report is returned. Nested ZIPs use the same global resource limits.
    """
    if not source_namespace.strip():
        raise ArchiveInspectionError(
            "A source namespace is required; account equivalence is not inferred."
        )
    state = _InventoryState(limits or InspectionLimits(), include_member_paths)
    try:
        files, member_count = directory_files(path, state.limits.max_members)
        state.members = member_count
        signatures = {relative: file_signature(file) for file, relative in files}
        size_bytes = sum(signature[2] for signature in signatures.values())
        if size_bytes > state.limits.max_total_bytes:
            raise ArchiveInspectionError("Directory exceeds the total source-size limit.")
        for file, relative in files:
            before = signatures[relative]
            with file.open("rb") as stream:
                metadata = os.fstat(stream.fileno())
                if (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                ) != before:
                    raise ArchiveInspectionError(
                        "Source changed during inspection; no report is valid."
                    )
                _scan_file(stream, relative, before[2], before[2], (), state)
            if file_signature(file) != before:
                raise ArchiveInspectionError(
                    "Source changed during inspection; no report is valid."
                )
        final_files, final_count = directory_files(path, state.limits.max_members)
        if (
            final_files != files
            or final_count != member_count
            or any(file_signature(file) != signatures[relative] for file, relative in final_files)
        ):
            raise ArchiveInspectionError("Source changed during inspection; no report is valid.")
    except ArchiveInspectionError:
        raise
    except (
        OSError,
        DirectorySourceError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        RuntimeError,
        NotImplementedError,
        EOFError,
        zlib.error,
    ) as error:
        raise ArchiveInspectionError("Cannot inspect a safe supported directory source.") from error
    return _report(state, source_namespace, "directory", None, size_bytes)


def inspect_source(
    path: Path,
    *,
    source_namespace: str,
    limits: InspectionLimits | None = None,
    include_member_paths: bool = False,
) -> ReconnaissanceReport:
    """Dispatch read-only inspection for an existing ZIP file or ordinary directory."""
    inspect = inspect_directory if path.is_dir() else inspect_zip
    return inspect(
        path,
        source_namespace=source_namespace,
        limits=limits,
        include_member_paths=include_member_paths,
    )
