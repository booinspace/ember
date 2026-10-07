"""Run read-only source reconnaissance without a model, database, or services."""

import argparse
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ember_memory_importers.base.archive import ArchiveInspectionError, inspect_zip
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


class ReportWriteError(OSError):
    """The requested report could not be written; its message omits private paths."""


def _arguments(argv: list[str] | None) -> _InspectionArguments:
    parser = argparse.ArgumentParser(prog="ember-memory")
    parser.add_argument("--version", action="version", version="%(prog)s 0.0.0 (phase -1)")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Read-only source inventory and reconnaissance")
    adapters = inspect.add_subparsers(dest="adapter", required=True)
    chatgpt = adapters.add_parser(
        "chatgpt", help="Inspect a ZIP without extraction or normalization"
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
        help="Parser limit; larger JSON is hashed and reported deferred",
    )
    arguments = _InspectionArguments.model_validate(vars(parser.parse_args(argv)))
    if arguments.max_json_mib <= 0:
        parser.error("--max-json-mib must be positive")
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
    arguments = _arguments(argv)
    if arguments.output.exists() or arguments.output.resolve() == arguments.archive.resolve():
        print("Report output must be a new file separate from the source archive.", file=sys.stderr)
        return 2
    try:
        report = inspect_zip(
            arguments.archive,
            source_namespace=arguments.source_namespace,
            limits=InspectionLimits(max_json_bytes=arguments.max_json_mib * 1024**2),
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
