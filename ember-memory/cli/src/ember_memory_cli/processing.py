"""Operator-owned summary import and resumable extraction/review commands."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError

from ember_memory_abi.common import CanonicalId, ContractModel
from ember_memory_abi.errors import MemoryError
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.library import KnowledgeSelection, LibraryService, SeedRequest
from ember_memory_engine.processing import ProcessingService
from ember_memory_engine.processing_contracts import (
    ExtractionSpec,
    JobRequest,
    JobStatus,
    ProcessingError,
    ReviewFile,
)
from ember_memory_engine.processing_review import ReviewService
from ember_memory_importers.normalization.contracts import SourceImportError
from ember_memory_importers.normalization.memory_summary import import_summary

from .maintenance import EngineImportTarget


class _Root(ContractModel):
    root: Path


class _Summary(_Root):
    operation: Literal["summary"]
    source: Path
    source_namespace: str
    format: Literal["text", "json"]
    text_field: str


class _Stores(_Root):
    operation: Literal["stores"]


class _Bound(_Root):
    store: CanonicalId


class _Create(_Bound):
    operation: Literal["create"]
    key: str
    model: str
    digest: str
    source_kind: Literal["summary", "archive", "native", "all"]
    workload: Literal["P3", "P4"]
    window_bytes: int


class _Job(_Bound):
    job: CanonicalId


class _Status(_Job):
    operation: Literal["status", "cancel", "reevaluate"]


class _Run(_Job):
    operation: Literal["run"]
    gateway: str
    token_file: Path
    max_windows: int | None
    retry_failed: bool
    window_attempts: Annotated[int, Field(ge=1, le=3)]
    continue_on_error: bool


class _Review(_Job):
    operation: Literal["review"]
    output: Path
    offset: int
    limit: int


class _Apply(_Bound):
    operation: Literal["apply"]
    input: Path
    yes: bool


class _Seed(_Bound):
    operation: Literal["seed"]
    input: Path
    character: CanonicalId
    key: str
    yes: bool


type _Arguments = Annotated[
    _Summary | _Stores | _Create | _Status | _Run | _Review | _Apply | _Seed,
    Field(discriminator="operation"),
]


def _parser(summary: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ember-memory summary" if summary else "ember-memory extract"
    )
    if summary:
        parser.add_argument("operation", choices=("import",))
        parser.add_argument("source", type=Path)
        parser.add_argument("--source-namespace", required=True)
        parser.add_argument("--format", choices=("text", "json"), default="text")
        parser.add_argument("--text-field", default="text")
        parser.add_argument("--root", type=Path, default=Path(".local/data"))
        return parser
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in (
        "stores",
        "create",
        "run",
        "status",
        "cancel",
        "reevaluate",
        "review",
        "apply",
        "seed",
    ):
        sub = commands.add_parser(name)
        sub.add_argument("--root", type=Path, default=Path(".local/data"))
        if name != "stores":
            sub.add_argument("--store", type=UUID, required=True)
        if name in {"run", "status", "cancel", "reevaluate", "review"}:
            sub.add_argument("--job", type=UUID, required=True)
        if name == "create":
            sub.add_argument("--key", required=True)
            sub.add_argument("--model", required=True)
            sub.add_argument("--digest", required=True)
            sub.add_argument(
                "--source-kind", choices=("summary", "archive", "native", "all"), default="summary"
            )
            sub.add_argument("--workload", choices=("P3", "P4"), default="P4")
            sub.add_argument("--window-bytes", type=int, default=1800)
        if name == "run":
            sub.add_argument("--gateway", default="http://127.0.0.1:11435")
            sub.add_argument("--token-file", type=Path, required=True)
            sub.add_argument("--max-windows", type=int)
            sub.add_argument(
                "--retry-failed",
                action="store_true",
                help="Retry previously failed windows at startup; completed windows are retained.",
            )
            sub.add_argument(
                "--window-attempts",
                type=int,
                choices=(1, 2, 3),
                default=1,
                help="Maximum attempts per selected window for invalid model outputs.",
            )
            sub.add_argument(
                "--continue-on-error",
                action="store_true",
                help="Continue past bad windows; exit 0 with gaps reported.",
            )
        if name == "review":
            sub.add_argument("--output", type=Path, required=True)
            sub.add_argument("--offset", type=int, default=0)
            sub.add_argument("--limit", type=int, default=1000)
        if name in {"apply", "seed"}:
            sub.add_argument("--input", type=Path, required=True)
            sub.add_argument("--yes", action="store_true", required=True)
        if name == "seed":
            sub.add_argument("--character", type=UUID, required=True)
            sub.add_argument("--key", required=True)
    return parser


def _review_file(path: Path) -> ReviewFile:
    with path.open("rb") as stream:
        payload = stream.read(16 * 1024 * 1024 + 1)
    if len(payload) > 16 * 1024 * 1024:
        raise ProcessingError("review_file_too_large")
    return ReviewFile.model_validate_json(payload)


async def _run(service: ProcessingService, args: _Run) -> JobStatus:
    from ember_memory_engine.processing_gateway import GatewayExtractionModel

    spec = service.definition(args.job).request.specification
    with args.token_file.open("r", encoding="utf-8") as stream:
        token = stream.read(129).strip()
    if not 32 <= len(token) <= 128:
        raise ProcessingError("background_credentials_invalid")
    model = GatewayExtractionModel(args.gateway, token, spec)
    try:
        status = await service.run(
            args.job,
            model,
            max_windows=args.max_windows,
            retry_failed=args.retry_failed,
            window_attempts=args.window_attempts,
            continue_on_error=args.continue_on_error,
            on_progress=lambda progress: print(
                f"Completed windows: {progress.completed_windows}/{progress.total_windows}; "
                f"candidates: {progress.candidates}; failed windows: {progress.failed_windows}",
                flush=True,
            ),
        )
        if status.run_outcome == "finished_with_failures":
            print(
                f"Extraction finished with {status.failed_windows} unresolved windows; "
                "valid candidates are saved. Retry unresolved windows separately.",
                flush=True,
            )
        print(status.model_dump_json())
        return status
    finally:
        await model.aclose()


def main(argv: list[str]) -> int:
    """Run explicit maintenance with aggregate stdout; never process personal input implicitly."""
    summary = argv[0] == "summary"
    values = vars(_parser(summary).parse_args(argv[1:]))
    if summary:
        values["operation"] = "summary"
    try:
        adapter: TypeAdapter[_Arguments] = TypeAdapter(_Arguments)
        args = adapter.validate_python(values)
        with CanonicalEngine(args.root) as engine:
            if isinstance(args, _Summary):
                store = next(
                    (
                        store
                        for store in engine.stores()
                        if store.kind == "library" and store.namespace == args.source_namespace
                    ),
                    None,
                )
                store = store or engine.create_library(args.source_namespace)
                directory = engine.root / "libraries" / str(store.store_id)
                report = import_summary(
                    args.source,
                    target=EngineImportTarget(engine.archive(store.store_id)),
                    raw_vault=directory / "rawsource",
                    manifests=directory / "manifests",
                    format=args.format,
                    text_field=args.text_field,
                )
                print(report.model_dump_json())
                return 0
            if isinstance(args, _Stores):
                for store in engine.stores():
                    print(store.model_dump_json())
                return 0
            service = ProcessingService(engine, args.store)
            if isinstance(args, _Create):
                request = JobRequest(
                    idempotency_key=args.key,
                    owner_store_id=args.store,
                    specification=ExtractionSpec(
                        model=args.model,
                        model_digest=args.digest.removeprefix("sha256:"),
                        workload=args.workload,
                        window_bytes=args.window_bytes,
                    ),
                    source_kind=args.source_kind,
                )
                print(service.create(request).model_dump_json())
            elif isinstance(args, _Run):
                status = asyncio.run(_run(service, args))
                if status.status in {"failed", "cancelled"} and not (
                    args.continue_on_error and status.run_outcome == "finished_with_failures"
                ):
                    return 2
            elif isinstance(args, _Status):
                if args.operation == "reevaluate":
                    print(
                        "Appended policy decisions: "
                        + str(ReviewService(service).reevaluate(args.job))
                    )
                    return 0
                status = (
                    service.cancel(args.job)
                    if args.operation == "cancel"
                    else service.status(args.job)
                )
                print(status.model_dump_json())
            elif isinstance(args, _Review):
                review = ReviewService(service).export(
                    args.job, offset=args.offset, limit=args.limit
                )
                with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(review.model_dump_json(indent=2) + "\n")
                print(
                    "Review entries: "
                    + str(len(review.entries))
                    + ". Next offset: "
                    + str(review.next_offset)
                )
            elif isinstance(args, _Apply):
                receipt = ReviewService(
                    service, authority=MutationAuthority(mode="confirmed_user")
                ).apply(_review_file(args.input))
                print(receipt.model_dump_json())
            else:
                review = _review_file(args.input)
                selections = tuple(
                    KnowledgeSelection(
                        knowledge_id=entry.candidate.candidate_id, revision=entry.candidate.revision
                    )
                    for entry in review.entries
                    if entry.decision == "approve"
                )
                if not selections or service.info.kind != "library":
                    raise ProcessingError("seed_requires_approved_library_selections")
                receipt = LibraryService(
                    engine, authority=MutationAuthority(mode="confirmed_user")
                ).seed(
                    args.character,
                    SeedRequest(
                        idempotency_key=args.key, library_id=args.store, selections=selections
                    ),
                )
                print(receipt.model_dump_json())
        return 0
    except (ProcessingError, MemoryError, SourceImportError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except FileExistsError:
        print(
            "Output already exists. Open the existing file or choose a new --output filename.",
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError, ValidationError):
        print(
            "Processing input or local state is invalid; private values are not logged.",
            file=sys.stderr,
        )
        return 2
