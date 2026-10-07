"""Bounded read-only ZIP inventory. Never extracts, fetches, or normalizes sources."""

import hashlib
import json
import math
import os
import stat
import zipfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Never

from pydantic import ConfigDict, JsonValue, TypeAdapter, ValidationError

from ..chatgpt.reconnaissance import field_shapes, inspect_conversations
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


class ArchiveInspectionError(ValueError):
    """Inspection failed with a content-free diagnostic suitable for CLI output."""


class _SourceJsonError(ValueError):
    pass


@dataclass(frozen=True)
class _MemberContent:
    size_bytes: int
    content_sha256: str
    json_bytes: bytearray | None


@dataclass(frozen=True)
class _JsonInspection:
    status: Literal["deferred_size_limit", "structurally_inspected", "invalid_or_ambiguous_json"]
    shape: dict[str, str] | None = None
    conversation: ConversationComponent | None = None
    warning: str | None = None


@dataclass(frozen=True)
class _MemberInspection:
    entry: InventoryEntry
    conversation: ConversationComponent | None
    warning: str | None


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
    if basename == "conversations.json":
        return "conversation_json_candidate"
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
    }:
        return "asset_candidate"
    return "other"


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    relative: str,
    limits: InspectionLimits,
    remaining_bytes: int,
) -> _MemberContent:
    is_json = PurePosixPath(relative).suffix.lower() == ".json"
    payload = bytearray() if is_json and info.file_size <= limits.max_json_bytes else None
    content_hash = hashlib.sha256()
    read_bytes = 0
    with archive.open(info) as stream:
        while block := stream.read(1024 * 1024):
            read_bytes += len(block)
            if read_bytes > limits.max_member_bytes or read_bytes > remaining_bytes:
                raise ArchiveInspectionError("Actual decompressed data exceeds configured limits.")
            if read_bytes > info.file_size:
                raise ArchiveInspectionError("Member data exceeds its declared size.")
            content_hash.update(block)
            if payload is not None:
                if read_bytes > limits.max_json_bytes:
                    raise ArchiveInspectionError("JSON data exceeds its declared inspection bound.")
                payload.extend(block)
    if read_bytes != info.file_size:
        raise ArchiveInspectionError("Member data does not match its declared size.")
    return _MemberContent(read_bytes, content_hash.hexdigest(), payload)


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


def _inspect_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    relative: str,
    limits: InspectionLimits,
    remaining_bytes: int,
    include_member_paths: bool,
) -> _MemberInspection:
    content = _read_member(archive, info, relative, limits, remaining_bytes)
    path_hash = hashlib.sha256(relative.encode("utf-8")).hexdigest()
    component = _component(relative)
    json_inspection = (
        _inspect_json(content.json_bytes, path_hash, component)
        if PurePosixPath(relative).suffix.lower() == ".json"
        else None
    )
    entry = InventoryEntry(
        path_sha256=path_hash,
        component=component,
        size_bytes=content.size_bytes,
        compressed_bytes=info.compress_size,
        content_sha256=content.content_sha256,
        relative_path=relative if include_member_paths else None,
        json_status=json_inspection.status if json_inspection else None,
        json_shape=json_inspection.shape if json_inspection else None,
    )
    return _MemberInspection(
        entry,
        json_inspection.conversation if json_inspection else None,
        json_inspection.warning if json_inspection else None,
    )


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


def inspect_zip(
    path: Path,
    *,
    source_namespace: str,
    limits: InspectionLimits | None = None,
    include_member_paths: bool = False,
) -> ReconnaissanceReport:
    """Inspect a ZIP without extraction, mutation, remote access, or model calls.

    Invalid/deferred JSON is an explicit coverage outcome. Unsafe paths, corruption,
    breached limits, and changing sources raise content-free ArchiveInspectionError.
    This blocking maintenance API must run off a realtime loop.
    """
    limits = limits or InspectionLimits()
    if not source_namespace.strip():
        raise ArchiveInspectionError(
            "A source namespace is required; account equivalence is not inferred."
        )
    inventory: list[InventoryEntry] = []
    conversations: list[ConversationComponent] = []
    warnings: set[str] = set()
    total_read = 0
    try:
        with path.open("rb") as raw:
            before = os.fstat(raw.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ArchiveInspectionError("Input must be a regular ZIP file.")
            raw_hash = hashlib.file_digest(raw, "sha256").hexdigest()
            raw.seek(0)
            with zipfile.ZipFile(raw) as archive:
                members = archive.infolist()
                for info, relative in _validated_members(members, limits):
                    if info.is_dir():
                        continue
                    inspected = _inspect_member(
                        archive,
                        info,
                        relative,
                        limits,
                        limits.max_total_bytes - total_read,
                        include_member_paths,
                    )
                    inventory.append(inspected.entry)
                    total_read += inspected.entry.size_bytes
                    if inspected.conversation is not None:
                        conversations.append(inspected.conversation)
                    if inspected.warning is not None:
                        warnings.add(inspected.warning)
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
        # Third-party filenames and paths must not escape through exception strings.
        raise ArchiveInspectionError(
            "Cannot read a valid supported ZIP archive; check input and integrity."
        ) from error
    if not conversations:
        warnings.add(
            "No conversation JSON candidate was structurally inspected; "
            "export coverage is incomplete."
        )
    return ReconnaissanceReport(
        generated_at=datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        source_namespace=source_namespace,
        source=SourceInventory(
            raw_sha256=raw_hash,
            logical_inventory_sha256=_logical_hash(inventory),
            archive_size_bytes=before.st_size,
            members_including_directories=len(members),
            files=len(inventory),
            uncompressed_bytes=total_read,
        ),
        limits=limits,
        privacy=InspectionPrivacy(
            member_paths="included_sensitive" if include_member_paths else "hashed"
        ),
        inventory=tuple(inventory),
        chatgpt=ChatGPTCoverage(conversation_components=tuple(conversations)),
        warnings=tuple(sorted(warnings)),
    )
