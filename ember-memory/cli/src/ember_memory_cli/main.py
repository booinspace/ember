"""Source reconnaissance and explicit operator-owned memory maintenance commands."""

import argparse
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ember_memory_importers.base.archive import ArchiveInspectionError, inspect_source
from ember_memory_importers.report_models import InspectionLimits, ReconnaissanceReport


class _InspectionArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["inspect"]
    adapter: Literal["chatgpt"]
    archive: Path
    source_namespace: str
    output: Path
    include_member_paths: bool
    max_json_mib: int
    max_conversation_mib: int
    max_nested_depth: int


class ReportWriteError(OSError):
    """The requested report could not be written; its message omits private paths."""


def _arguments(argv: list[str] | None) -> _InspectionArguments:
    parser = argparse.ArgumentParser(
        prog="ember-memory",
        epilog="Maintenance: import, character, remember, recall, sources, propose, confirm. "
        "Phase 7: profile, native, related, consolidate. "
        "Phase 6: ingest (interactive), summary, extract. Phase 5: index. Phase 4: "
        "library-entity, library-propose, library-approve, library-show, "
        "seed, grant, revoke, forget-preview, forget, bundle. "
        "Use ember-memory COMMAND --help for their arguments.",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s 0.1.0 (phase 7 maintenance)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Read-only source inventory and reconnaissance")
    adapters = inspect.add_subparsers(dest="adapter", required=True)
    chatgpt = adapters.add_parser(
        "chatgpt", help="Inspect a ZIP or directory without extraction or normalization"
    )
    chatgpt.add_argument("archive", type=Path)
    chatgpt.add_argument(
        "--source-namespace", required=True, help="Explicit account/source namespace"
    )
    chatgpt.add_argument(
        "--output", type=Path, required=True, help="New report file; never overwritten"
    )
    chatgpt.add_argument(
        "--include-member-paths",
        action="store_true",
        help="Include potentially private filenames in the local report",
    )
    chatgpt.add_argument(
        "--max-json-mib",
        type=int,
        default=64,
        help="Non-conversation JSON limit; larger metadata is hashed and deferred",
    )
    chatgpt.add_argument(
        "--max-conversation-mib",
        type=int,
        default=64,
        help="Per-conversation streaming bound, independent of total JSON component size",
    )
    chatgpt.add_argument(
        "--max-nested-depth",
        type=int,
        default=2,
        help="Nested ZIP depth; deeper containers are hashed and reported deferred",
    )
    arguments = _InspectionArguments.model_validate(vars(parser.parse_args(argv)))
    if arguments.max_json_mib <= 0:
        parser.error("--max-json-mib must be positive")
    if arguments.max_conversation_mib <= 0:
        parser.error("--max-conversation-mib must be positive")
    if arguments.max_nested_depth < 0:
        parser.error("--max-nested-depth cannot be negative")
    return arguments


def _write_report(report: ReconnaissanceReport, output: Path) -> None:
    payload = report.model_dump_json(indent=2, exclude_none=True) + "\n"
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    except OSError as error:
        raise ReportWriteError("Cannot write the requested report file.") from error


def main(argv: list[str] | None = None) -> int:
    """Inspect a source and write a new report; return 2 for safe domain diagnostics.

    Argparse retains its standard help/usage exit behavior. Reports never overwrite
    an existing file or the source, and default diagnostics omit private source values.
    """
    actual = sys.argv[1:] if argv is None else argv
    if actual and actual[0] in {"recall-policy", "capture", "enrich"}:
        from .phase_eight import main as phase_eight

        return phase_eight(actual)
    if actual and actual[0] in {"profile", "native", "related", "consolidate"}:
        from .phase_seven import main as phase_seven

        return phase_seven(actual)
    if actual and actual[0] == "ingest":
        from .ingestion import main as ingest

        return ingest(actual[1:])
    if actual and actual[0] in {"summary", "extract"}:
        from .processing import main as process

        return process(actual)
    if actual and actual[0] == "index":
        from .indexes import main as index_main

        return index_main(actual)
    from .libraries import COMMANDS as LIBRARY_COMMANDS
    from .libraries import main as library_main

    if actual and actual[0] in LIBRARY_COMMANDS:
        return library_main(actual)
    from .operations import COMMANDS
    from .operations import main as maintenance_main

    if actual and actual[0] in COMMANDS:
        return maintenance_main(actual)
    arguments = _arguments(argv)
    if arguments.output.exists() or arguments.output.resolve() == arguments.archive.resolve():
        print("Report output must be a new file separate from the source archive.", file=sys.stderr)
        return 2
    if arguments.archive.is_dir() and arguments.output.resolve().is_relative_to(
        arguments.archive.resolve()
    ):
        print("Report output must be outside the source directory.", file=sys.stderr)
        return 2
    try:
        report = inspect_source(
            arguments.archive,
            source_namespace=arguments.source_namespace,
            limits=InspectionLimits(
                max_json_bytes=arguments.max_json_mib * 1024**2,
                max_conversation_bytes=arguments.max_conversation_mib * 1024**2,
                max_nested_depth=arguments.max_nested_depth,
            ),
            include_member_paths=arguments.include_member_paths,
        )
        _write_report(report, arguments.output)
    except (ArchiveInspectionError, ReportWriteError) as error:
        print(f"Inspection error: {error}", file=sys.stderr)
        return 2
    print(f"Inspected {report.source.files} files; report written locally.")
    print("No source extraction, normalization, model calls, or canonical writes occurred.")
    if report.warnings:
        print(f"Coverage warnings: {len(report.warnings)}; review the report before proceeding.")
    return 0
