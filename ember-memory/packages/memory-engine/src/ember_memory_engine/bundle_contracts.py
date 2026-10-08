"""Versioned maintenance bundle format, independent of ABI/archive/schema versions."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from ember_memory_abi.common import (
    CanonicalId,
    ContractModel,
    NonnegativeInteger,
    Sha256,
    UtcTimestamp,
)
from ember_memory_abi.evidence import SourceRef

from .models import StoreInfo

BUNDLE_VERSION = "0.1.0"


class BundleFile(ContractModel):
    """Relative POSIX member name with exact byte integrity; never a deployment path."""

    path: str
    size: NonnegativeInteger
    sha256: Sha256

    @model_validator(mode="after")
    def _relative(self) -> Self:
        from pathlib import PurePosixPath

        path = PurePosixPath(self.path)
        if (
            not self.path
            or path.is_absolute()
            or "\\" in self.path
            or ":" in self.path
            or any(part in {".", ".."} for part in self.path.split("/"))
            or str(path) != self.path
            or self.path == "manifest.json"
        ):
            raise ValueError("Unsafe bundle member.")
        return self


class StoreSnapshot(ContractModel):
    """Coverage distinguishes complete canonical stores from exact-span projections."""

    store: StoreInfo
    coverage: Literal["full", "evidence_only", "selected_character"]


class BundleManifest(ContractModel):
    """A published bundle is complete only after every listed member verifies.

    Linked dependencies carry logical store IDs. Evidence-only libraries cannot
    claim complete archive coverage. Restoration enables no external providers.
    """

    bundle_version: Literal["0.1.0"] = BUNDLE_VERSION
    bundle_id: CanonicalId
    kind: Literal["source", "character"]
    mode: Literal["linked", "self_contained"]
    created_at: UtcTimestamp
    stores: Annotated[tuple[StoreSnapshot, ...], Field(min_length=1, max_length=1000)]
    dependencies: tuple[CanonicalId, ...] = ()
    selected_memory_ids: tuple[CanonicalId, ...] = ()
    evidence_closure: tuple[SourceRef, ...] = ()
    providers_disabled: Literal[True] = True
    files: Annotated[tuple[BundleFile, ...], Field(max_length=100000)]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        if len({item.path for item in self.files}) != len(self.files):
            raise ValueError("Duplicate bundle members.")
        ids = {item.store.store_id for item in self.stores}
        if len(ids) != len(self.stores) or len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("Duplicate bundle stores/dependencies.")
        if ids.intersection(self.dependencies):
            raise ValueError("Materialized stores cannot also be external dependencies.")
        if len(set(self.selected_memory_ids)) != len(self.selected_memory_ids):
            raise ValueError("Duplicate selected memories.")
        characters = tuple(item for item in self.stores if item.store.kind == "character")
        if self.kind == "source":
            if (
                len(self.stores) != 1
                or characters
                or self.stores[0].coverage != "full"
                or self.dependencies
                or self.selected_memory_ids
                or self.evidence_closure
                or self.mode != "linked"
            ):
                raise ValueError("Source bundles contain one complete library.")
        elif len(characters) != 1:
            raise ValueError("Character bundles contain one character.")
        elif self.mode == "linked":
            if (
                len(self.stores) != 1
                or characters[0].coverage != "full"
                or self.selected_memory_ids
                or self.evidence_closure
            ):
                raise ValueError("Linked bundles contain complete character state.")
        elif (
            self.dependencies
            or characters[0].coverage != "selected_character"
            or any(
                item.coverage != "evidence_only"
                for item in self.stores
                if item.store.kind == "library"
            )
        ):
            raise ValueError("Self-contained bundles carry selected character/evidence state.")
        return self
