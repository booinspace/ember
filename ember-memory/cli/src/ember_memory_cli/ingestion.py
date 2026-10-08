"""One explicit interactive import/extract/review/seed/index maintenance owner."""

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path
from uuid import UUID, uuid4

from ember_memory_abi.common import CanonicalId, canonical_object_sha256
from ember_memory_abi.errors import MemoryError
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.errors import StoreOwnershipError
from ember_memory_engine.library import LibraryService, SeedRequest
from ember_memory_engine.models import StoreInfo
from ember_memory_engine.processing import ProcessingService
from ember_memory_engine.processing_contracts import (
    ExtractionSpec,
    JobRequest,
    JobStatus,
    ProcessingError,
)
from ember_memory_engine.processing_gateway import GatewayExtractionModel
from ember_memory_engine.processing_review import ReviewService
from ember_memory_importers.normalization.contracts import SourceImportError
from ember_memory_importers.normalization.memory_summary import import_summary
from ember_memory_importers.normalization.zip_pipeline import import_zip

from .ingestion_settings import IngestionArguments, IngestionSettings, SourceKind, settings_for
from .interactive_review import (
    Console,
    IngestionCancelled,
    Terminal,
    choice,
    confirm,
    review_candidates,
)
from .maintenance import EngineImportTarget


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ember-memory ingest",
        description="Interactive source ingestion, review, character seeding and indexing.",
        epilog="Requires a terminal. Stop archive/chat writers; gateway-only may stay running.",
    )
    parser.add_argument(
        "source", type=Path, nargs="?", help="Summary JSON/text/Markdown or ChatGPT ZIP"
    )
    parser.add_argument("--config", type=Path, help="Deployment TOML v1 maintenance settings")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--store", type=UUID, help="Existing source library")
    parser.add_argument("--job", type=UUID, help="Existing job; requires --store")
    parser.add_argument("--character", type=UUID)
    parser.add_argument("--source-namespace")
    parser.add_argument("--format", choices=("auto", "text", "json", "chatgpt-zip"), default="auto")
    parser.add_argument("--text-field", default="text")
    parser.add_argument("--source-kind", choices=("summary", "archive", "all"), default="all")
    parser.add_argument("--key")
    parser.add_argument("--model")
    parser.add_argument("--digest")
    parser.add_argument("--gateway")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--encoder", type=Path)
    parser.add_argument("--window-attempts", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--max-windows", type=int)
    parser.add_argument("--skip-index", action="store_true")
    return parser


def _select_store(stores: tuple[StoreInfo, ...], console: Console, label: str) -> CanonicalId:
    if not stores:
        raise ProcessingError("ingest_no_" + label + "_stores")
    for number, store in enumerate(stores, 1):
        console.write(f"{number}. {store.store_id} ({store.namespace!r})")
    answer = choice(
        console, f"Choose {label} number: ", tuple(str(i) for i in range(1, len(stores) + 1))
    )
    return stores[int(answer) - 1].store_id


def _source(
    engine: CanonicalEngine, args: IngestionArguments, console: Console
) -> tuple[CanonicalId, SourceKind, str | None]:
    if args.job is not None and args.store is None:
        raise ProcessingError("ingest_job_requires_store")
    if args.source is not None and args.store is not None:
        raise ProcessingError("ingest_source_and_store_are_exclusive")
    if args.store is not None:
        info, _ = engine.maintenance_store(args.store)
        if info.kind != "library":
            raise ProcessingError("ingest_requires_library")
        return args.store, args.source_kind, None
    source = args.source
    if source is None:
        mode = choice(console, "[1] import a file, [2] existing library: ", ("1", "2"))
        if mode == "2":
            stores = tuple(info for info in engine.stores() if info.kind == "library")
            return _select_store(stores, console, "library"), args.source_kind, None
        raw = console.read("Source file path: ").strip().strip('"')
        if not raw:
            raise ProcessingError("ingest_source_path_missing")
        source = Path(raw)
    format = args.format
    if format == "auto":
        format = (
            "chatgpt-zip"
            if source.suffix.casefold() == ".zip"
            else "json"
            if source.suffix.casefold() == ".json"
            else "text"
        )
    default = "chatgpt-archive" if format == "chatgpt-zip" else "chatgpt-generated-summary"
    namespace = args.source_namespace
    if namespace is None:
        namespace = console.read(f"Source namespace [{default}]: ").strip() or default
    library = next(
        (
            info
            for info in engine.stores()
            if info.kind == "library" and info.namespace == namespace
        ),
        None,
    )
    library = library or engine.create_library(namespace)
    directory = engine.root / "libraries" / str(library.store_id)
    target = EngineImportTarget(engine.archive(library.store_id))
    if format == "chatgpt-zip":
        report = import_zip(
            source,
            target=target,
            raw_vault=directory / "rawsource",
            manifests=directory / "manifests",
        )
        kind: SourceKind = "archive"
    else:
        report = import_summary(
            source,
            target=target,
            raw_vault=directory / "rawsource",
            manifests=directory / "manifests",
            format="json" if format == "json" else "text",
            text_field=args.text_field,
        )
        kind = "summary"
    console.write(f"Source library: {library.store_id}; records written: {report.records_written}")
    return library.store_id, kind, report.source_sha256


def _job(
    service: ProcessingService,
    args: IngestionArguments,
    settings: IngestionSettings,
    console: Console,
    source_kind: SourceKind,
    source_hash: str | None,
) -> CanonicalId:
    if args.job is not None:
        service.definition(args.job)
        return args.job
    if args.key is None:
        offset = 0
        while True:
            definitions = service.definitions(offset=offset, limit=21)
            if not definitions:
                break
            for number, definition in enumerate(definitions[:20], 1):
                console.write(
                    f"{number}. {definition.job_id} "
                    f"({definition.request.source_kind}, "
                    f"{definition.request.specification.extractor}, {definition.created_at})"
                )
            choices = tuple(str(i) for i in range(1, min(len(definitions), 20) + 1)) + ("new",)
            if len(definitions) > 20:
                choices += ("next",)
            answer = choice(console, "Existing snapshot: job number, new, or next: ", choices)
            if answer == "new":
                break
            if answer == "next":
                offset += 20
                continue
            return definitions[int(answer) - 1].job_id
    model = settings.model or console.read("Exact Ollama model tag: ").strip()
    digest = settings.digest or console.read("Exact Ollama model digest: ").strip().removeprefix(
        "sha256:"
    )
    spec = ExtractionSpec(model=model, model_digest=digest)
    key = args.key or (
        "interactive:"
        + canonical_object_sha256(
            {
                "source": source_hash or str(uuid4()),
                "spec": spec.model_dump(mode="json"),
                "source_kind": source_kind,
                "format": args.format,
                "text_field": args.text_field,
            }
        )
    )
    return service.create(
        JobRequest(
            idempotency_key=key,
            owner_store_id=service.owner,
            specification=spec,
            source_kind=source_kind,
        )
    ).definition.job_id


async def _extract(
    service: ProcessingService,
    job: CanonicalId,
    settings: IngestionSettings,
    args: IngestionArguments,
    console: Console,
) -> JobStatus:
    token_file = settings.token_file
    if token_file is None:
        token_file = Path(console.read("Background token file path: ").strip().strip('"'))
    with token_file.open("r", encoding="utf-8") as stream:
        token = stream.read(129).strip()
    if not 32 <= len(token) <= 128:
        raise ProcessingError("background_credentials_invalid")
    model = GatewayExtractionModel(
        settings.gateway, token, service.definition(job).request.specification
    )
    try:
        return await service.run(
            job,
            model,
            max_windows=args.max_windows,
            retry_failed=True,
            window_attempts=args.window_attempts,
            continue_on_error=True,
            on_progress=lambda state: console.write(
                f"Windows: {state.completed_windows}/{state.total_windows}; "
                f"candidates: {state.candidates}; unresolved: {state.failed_windows}"
            ),
        )
    finally:
        await model.aclose()


def run_workflow(args: IngestionArguments, settings: IngestionSettings, console: Console) -> int:
    """Own one root through human decisions; interruption retains durable checkpoints."""
    with CanonicalEngine(settings.root) as engine:
        character = settings.character
        if character is None:
            stores = tuple(info for info in engine.stores() if info.kind == "character")
            character = _select_store(stores, console, "character")
        info, _ = engine.maintenance_store(character)
        if info.kind != "character":
            raise ProcessingError("ingest_target_not_character")
        console.write(f"Target character: {character}")
        store, kind, source_hash = _source(engine, args, console)
        service = ProcessingService(engine, store)
        job = _job(service, args, settings, console, kind, source_hash)
        status = service.status(job)
        console.write(
            f"Job: {job}; {status.completed_windows}/{status.total_windows} windows; "
            f"{status.candidates} candidates."
        )
        if status.status not in {"complete", "cancelled"} and confirm(
            console, "Run/resume extraction before review?", default=source_hash is not None
        ):
            status = asyncio.run(_extract(service, job, settings, args, console))
            if status.run_outcome == "stopped_on_error":
                raise ProcessingError("ingest_extraction_stopped_check_job_status")
        if status.candidates == 0:
            console.write(
                "No candidates available. Resume extraction to produce reviewable memories."
            )
            return 0
        if status.status != "complete" and not confirm(
            console, "Extraction is incomplete. Review the available validated candidates?"
        ):
            return 0
        authority = MutationAuthority(mode="confirmed_user")
        approved = review_candidates(ReviewService(service, authority=authority), job, console)
        console.write(f"Approved snapshots available: {len(approved)}.")
        if not approved or not confirm(
            console, f"Seed ALL {len(approved)} approved snapshots into character {character}?"
        ):
            console.write("Approved knowledge retained; no character memories were seeded.")
            return 0
        library = LibraryService(engine, authority=authority)
        applied = skipped = 0
        for selection in approved:
            if library.has_seed(character, store, selection.knowledge_id):
                skipped += 1
                continue
            key = "interactive-seed:" + canonical_object_sha256(
                {
                    "character": str(character),
                    "library": str(store),
                    "selection": selection.model_dump(mode="json"),
                }
            )
            library.seed(
                character,
                SeedRequest(
                    idempotency_key=key,
                    library_id=store,
                    selections=(selection,),
                ),
            )
            applied += 1
        console.write(f"Seeded: {applied}; already seeded (left unchanged): {skipped}.")
        if args.skip_index or settings.encoder is None:
            console.write("Memories saved. Indexing skipped; configure local embedding assets.")
        else:
            encoder = BgeEncoder(settings.encoder)
            engine.update_index(character, encoder)
            console.write("Character index updated. Start the same character to test recall.")
    return 0


def main(argv: list[str]) -> int:
    """Run only in a terminal; error diagnostics never expose private values."""
    values = vars(_parser().parse_args(argv))
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "ingest requires an interactive terminal; use summary/extract for scripts.",
            file=sys.stderr,
        )
        return 2
    try:
        args = IngestionArguments.model_validate(values)
        return run_workflow(args, settings_for(args), Terminal())
    except IngestionCancelled:
        print("Stopped. Completed extraction/review/seed steps remain saved.")
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted. Saved checkpoints and decisions are retained.", file=sys.stderr)
        return 130
    except StoreOwnershipError as error:
        print(str(error), file=sys.stderr)
        print("Stop other archive/chat writers; gateway-only may stay running.", file=sys.stderr)
        return 2
    except (ProcessingError, MemoryError, SourceImportError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, sqlite3.Error):
        print(
            "Interactive ingestion failed; check settings, local assets and source format.",
            file=sys.stderr,
        )
        return 2
