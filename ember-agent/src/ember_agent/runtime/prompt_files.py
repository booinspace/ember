"""Operator-owned Markdown, bounded reads and compare-and-swap file publication."""

import os
import tempfile
from contextlib import ExitStack, closing
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from ..character.core import Personality
from ..character.prompts import (
    DEFAULT_PERSONALITY,
    DEFAULT_SYSTEM,
    MAX_FILE_BYTES,
    PromptDocument,
    PromptDocuments,
    PromptEdit,
    PromptSnapshot,
    compile_prompts,
    digest,
)
from ..inference.contracts import GatewayError
from .config import RuntimeModel
from .processes import RuntimeLease


class Migration(RuntimeModel):
    """Character display metadata and the legacy profile that provisioned its prompt."""

    name: str
    profile: str
    legacy_revision: str
    complete: bool = False


class PromptFiles:
    """Synchronous file operations; composition submits them to its existing worker."""

    def __init__(
        self,
        system: Path,
        character_config: Path,
        character_id: UUID,
        profile: str,
        legacy: Personality,
        legacy_exists: bool,
        input_ceiling: int,
    ) -> None:
        self.system = system
        self.personality = character_config / "personality.md"
        self.metadata = character_config / "personality-migration.json"
        if system.absolute().is_relative_to(character_config.parent.parent.absolute()):
            raise GatewayError("prompt_path_invalid")
        self.character_id = character_id
        self.input_ceiling = min(input_ceiling, 24576)
        self.name = legacy.name
        self._initialize(profile, legacy, legacy_exists)

    def _locks(self) -> ExitStack:
        stack = ExitStack()
        try:
            for path in sorted({self.system, self.personality}):
                self._safe(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                stack.enter_context(closing(RuntimeLease(path.parent, name=path.name + ".lock")))
        except (OSError, ValueError) as error:
            stack.close()
            raise GatewayError("prompt_files_unavailable") from error
        except BaseException:
            stack.close()
            raise
        return stack

    @staticmethod
    def _safe(path: Path) -> None:
        for part in (path, *path.parents):
            if part.is_symlink() or (
                part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400
            ):
                raise GatewayError("prompt_path_invalid")

    @classmethod
    def _raw(cls, path: Path) -> bytes:
        cls._safe(path)
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise GatewayError("prompt_file_too_large")
        return raw

    @classmethod
    def _document(cls, path: Path) -> PromptDocument:
        try:
            raw = cls._raw(path)
        except FileNotFoundError:
            return PromptDocument(markdown="", revision=digest(""), error="prompt_file_missing")
        try:
            return PromptDocument(markdown=raw.decode("utf-8"), revision=sha256(raw).hexdigest())
        except UnicodeError:
            return PromptDocument(
                markdown=raw.decode("utf-8", errors="replace"),
                revision=sha256(raw).hexdigest(),
                error="prompt_invalid_utf8",
            )

    @classmethod
    def _publish(cls, path: Path, raw: bytes, *, backup: bool = True) -> None:
        cls._safe(path)
        if backup and path.exists():
            cls._publish(path.with_name(path.name + ".previous"), cls._raw(path), backup=False)
        descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _initialize(self, profile: str, legacy: Personality, legacy_exists: bool) -> None:
        try:
            with self._locks():
                revision = digest(legacy.instructions)
                metadata = None
                if self.metadata.exists():
                    metadata = Migration.model_validate_json(self._raw(self.metadata))
                    self.name = metadata.name
                    if (
                        legacy_exists
                        and metadata.profile != profile
                        and metadata.legacy_revision != revision
                    ):
                        raise GatewayError("prompt_legacy_profile_conflict")
                    if metadata.complete and not self.personality.exists():
                        return  # A lost authored file needs repair, never a reset from legacy JSON.
                elif not self.personality.exists():
                    # Record migration intent before publishing either document.
                    metadata = Migration(
                        name=legacy.name, profile=profile, legacy_revision=revision
                    )
                    self._publish(self.metadata, metadata.model_dump_json().encode(), backup=False)
                system = (
                    self._document(self.system).markdown if self.system.exists() else DEFAULT_SYSTEM
                )
                personality = (
                    self._document(self.personality).markdown
                    if self.personality.exists()
                    else DEFAULT_PERSONALITY
                    if legacy == Personality()
                    else legacy.instructions
                )
                # Preserve existing invalid documents for operator repair.
                if not self.system.exists() or not self.personality.exists():
                    compile_prompts(system, personality, self.input_ceiling)
                    if not self.system.exists():
                        self._publish(self.system, system.encode(), backup=False)
                    if not self.personality.exists():
                        self._publish(self.personality, personality.encode(), backup=False)
                if metadata is not None and not metadata.complete:
                    self._publish(
                        self.metadata,
                        metadata.model_copy(update={"complete": True}).model_dump_json().encode(),
                        backup=False,
                    )
        except (OSError, ValueError, ValidationError) as error:
            raise GatewayError("prompt_files_unavailable") from error

    def _read(self) -> PromptDocuments:
        system, personality = self._document(self.system), self._document(self.personality)
        snapshot = None
        error = system.error or personality.error
        if error is None:
            try:
                snapshot = compile_prompts(
                    system.markdown, personality.markdown, self.input_ceiling
                )
            except GatewayError as failure:
                error = failure.code
        return PromptDocuments(
            character_id=self.character_id,
            system=system,
            personality=personality,
            snapshot=snapshot,
            error=error,
            instruction_limit=min(4096, self.input_ceiling // 4),
        )

    def read(self) -> PromptDocuments:
        """Read the exact current pair, retaining invalid-but-bounded text for repair."""
        try:
            with self._locks():
                return self._read()
        except OSError as error:
            raise GatewayError("prompt_files_unavailable") from error

    def load(self) -> PromptSnapshot:
        """Fail closed for generation while keeping editor reads available."""
        value = self.read()
        if value.snapshot is None:
            raise GatewayError(value.error or "prompt_files_unavailable")
        return value.snapshot

    def edit(self, value: PromptEdit, *, save: bool) -> PromptDocuments:
        """Validate one candidate pair and atomically publish only the selected document."""
        try:
            with self._locks():
                current = self._read()
                if (
                    value.system_revision != current.system.revision
                    or value.personality_revision != current.personality.revision
                ):
                    raise GatewayError("prompt_revision_conflict")
                other = current.personality if value.kind == "system" else current.system
                if other.error:
                    raise GatewayError(other.error)
                system = value.markdown if value.kind == "system" else current.system.markdown
                personality = (
                    value.markdown if value.kind == "personality" else current.personality.markdown
                )
                snapshot = compile_prompts(system, personality, self.input_ceiling)
                if save:
                    self._publish(
                        self.system if value.kind == "system" else self.personality,
                        value.markdown.encode(),
                    )
                return current.model_copy(
                    update={
                        value.kind: PromptDocument(
                            markdown=value.markdown, revision=digest(value.markdown)
                        ),
                        "snapshot": snapshot,
                        "error": None,
                    }
                )
        except (OSError, UnicodeError) as error:
            raise GatewayError("prompt_write_failed") from error
