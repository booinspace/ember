"""Bounded immutable source preservation and strict JSON parsing."""

import json
import math
from hashlib import sha256
from pathlib import Path
from typing import NoReturn

from pydantic import JsonValue, TypeAdapter, ValidationError

from .contracts import SourceImportError

MAXIMUM_SOURCE_BYTES = 64 * 1024 * 1024


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SourceImportError("Duplicate JSON fields prevent safe normalization.")
        result[key] = value
    return result


def _constant(_: str) -> NoReturn:
    raise SourceImportError("Nonfinite JSON numbers are unsupported.")


def reject_links(path: Path) -> None:
    """Reject linked/reparse ancestors as well as the final file before local source I/O."""
    for item in (path.absolute(), *path.absolute().parents):
        if item.is_symlink() or (
            item.exists() and getattr(item.stat(), "st_file_attributes", 0) & 0x400
        ):
            raise SourceImportError("Source/normalization paths cannot contain links.")


def _finite(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise SourceImportError("Nonfinite JSON numbers are unsupported.")
    return result


def preserve_json(path: Path, vault: Path) -> tuple[JsonValue, str]:
    """Retain exact bytes before normalization; reject links, changes, oversized and bad JSON."""
    reject_links(path)
    reject_links(vault)
    # Source may be a user-selected NAS file; the vault is trusted local composition.
    if path.is_symlink() or not path.is_file():
        raise SourceImportError("Source must be a regular JSON file.")
    before = path.stat()
    if getattr(before, "st_file_attributes", 0) & 0x400:
        raise SourceImportError("Source links/reparse points are unsupported.")
    try:
        with path.open("rb") as source:
            raw = source.read(MAXIMUM_SOURCE_BYTES + 1)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise SourceImportError("Source changed while being read.")
        if len(raw) > MAXIMUM_SOURCE_BYTES:
            raise SourceImportError("JSON normalization source exceeds the 64 MiB bound.")
        digest = sha256(raw).hexdigest()
        vault.mkdir(parents=True, exist_ok=True)
        preserved = vault / (digest + ".json")
        reject_links(preserved)
        if preserved.exists():
            if (
                preserved.stat().st_size != len(raw)
                or sha256(preserved.read_bytes()).hexdigest() != digest
            ):
                raise SourceImportError("Preserved source failed integrity verification.")
        else:
            with preserved.open("xb") as output:
                output.write(raw)
        parsed: object = json.loads(
            raw, object_pairs_hook=_unique, parse_constant=_constant, parse_float=_finite
        )
        adapter: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
        value = adapter.validate_python(parsed)
        return value, digest
    except (OSError, ValueError, RecursionError, ValidationError) as error:
        if isinstance(error, SourceImportError):
            raise
        raise SourceImportError("Source could not be parsed or preserved safely.") from error
