"""Immutable ZIP preservation and bounded access to inspected conversation components."""

import os
import zipfile
from collections.abc import Generator, Iterator
from contextlib import ExitStack, contextmanager
from hashlib import file_digest, sha256
from pathlib import Path
from tempfile import NamedTemporaryFile, SpooledTemporaryFile

from pydantic import JsonValue

from ..base.streams import BinaryReader
from ..chatgpt.streaming import iter_conversation_stream
from ..report_models import InventoryEntry, ReconnaissanceReport
from .contracts import SourceImportError
from .source_io import reject_links


def preserve_zip(path: Path, vault: Path, report: ReconnaissanceReport) -> Path:
    """Copy the exact inspected container once, using bounded memory and atomic publication."""
    reject_links(path)
    reject_links(vault)
    vault.mkdir(parents=True, exist_ok=True)
    expected = report.source.raw_sha256
    if expected is None or report.source.kind != "zip":
        raise SourceImportError("ZIP preservation requires an inspected container.")
    destination = vault / (expected + ".zip")
    reject_links(destination)
    if destination.exists():
        with destination.open("rb") as stream:
            if file_digest(stream, "sha256").hexdigest() != expected:
                raise SourceImportError("Preserved ZIP failed integrity verification.")
        return destination
    temporary: Path | None = None
    try:
        before = path.stat()
        with (
            path.open("rb") as source,
            NamedTemporaryFile(
                mode="wb", dir=vault, prefix="zip-", suffix=".partial", delete=False
            ) as output,
        ):
            temporary = Path(output.name)
            digest = sha256()
            copied = 0
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > report.source.archive_size_bytes:
                    raise SourceImportError("ZIP changed after inspection.")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
        after = path.stat()
        if (
            copied != report.source.archive_size_bytes
            or digest.hexdigest() != expected
            or (before.st_size, before.st_mtime_ns, before.st_ino)
            != (after.st_size, after.st_mtime_ns, after.st_ino)
        ):
            raise SourceImportError("ZIP changed after inspection.")
        temporary.replace(destination)
        return destination
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class _VerifiedReader:
    def __init__(self, stream: BinaryReader, entry: InventoryEntry) -> None:
        self.stream = stream
        self.entry = entry
        self.digest = sha256()
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.stream.read(size)
        self.count += len(chunk)
        if self.count > self.entry.size_bytes:
            raise SourceImportError("ZIP component exceeds its inspected size.")
        self.digest.update(chunk)
        return chunk

    def finish(self) -> None:
        if (
            self.count != self.entry.size_bytes
            or self.digest.hexdigest() != self.entry.content_sha256
        ):
            raise SourceImportError("ZIP component failed integrity verification.")


class ZipSource:
    """Open only inspected paths; stage nested containers privately without extracting a tree."""

    def __init__(self, path: Path, report: ReconnaissanceReport) -> None:
        self.path = path
        self.report = report
        self.entries = {entry.path_sha256: entry for entry in report.inventory}
        self.components = tuple(
            self.entries[component.path_sha256]
            for component in report.chatgpt.conversation_components
        )
        candidates = tuple(
            entry for entry in report.inventory if entry.component == "conversation_json_candidate"
        )
        if (
            not self.components
            or len(candidates) != len(self.components)
            or any(entry.json_status != "structurally_inspected" for entry in self.components)
        ):
            raise SourceImportError(
                "Conversation ZIP components have incomplete inspection coverage."
            )

    @contextmanager
    def _container(self, chain: tuple[str, ...]) -> Generator[zipfile.ZipFile, None, None]:
        with ExitStack() as owned:
            archive = owned.enter_context(zipfile.ZipFile(self.path))
            parent_chain: tuple[str, ...] = ()
            for identity in chain:
                entry = self.entries[identity]
                if entry.container_path_sha256 != parent_chain or entry.relative_path is None:
                    raise SourceImportError("Nested ZIP locator failed inspection binding.")
                staged = owned.enter_context(SpooledTemporaryFile(max_size=8 * 1024**2))
                with archive.open(entry.relative_path) as stream:
                    verified = _VerifiedReader(stream, entry)
                    while chunk := verified.read(1024 * 1024):
                        staged.write(chunk)
                    verified.finish()
                staged.seek(0)
                archive = owned.enter_context(zipfile.ZipFile(staged))
                parent_chain = (*parent_chain, identity)
            yield archive

    def graphs(self) -> Iterator[tuple[InventoryEntry, JsonValue]]:
        """Yield one guarded graph at a time, staging each containing ZIP once per pass."""
        chains = tuple(dict.fromkeys(entry.container_path_sha256 for entry in self.components))
        for chain in chains:
            with self._container(chain) as archive:
                for entry in self.components:
                    if entry.container_path_sha256 != chain:
                        continue
                    if entry.relative_path is None:
                        raise SourceImportError("ZIP component has no inspected locator.")
                    with archive.open(entry.relative_path) as stream:
                        verified = _VerifiedReader(stream, entry)
                        for graph in iter_conversation_stream(verified, self.report.limits):
                            yield entry, graph
                        verified.finish()
