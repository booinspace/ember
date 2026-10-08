"""Explicit owned index maintenance; diagnostics omit corpus text and credentials."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from ember_memory_abi.common import CanonicalId, ContractModel
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.interfaces import ArchiveQuery, MemoryQuery
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.embedding import BgeEncoder, file_sha256
from ember_memory_engine.errors import IndexError
from ember_memory_engine.index_storage import connect


class _Arguments(ContractModel):
    command: Literal["index"]
    operation: Literal["status", "rebuild", "update", "search", "archive"]
    root: Path
    store: CanonicalId
    encoder: Path | None = None
    query: str = ""
    mode: Literal["hybrid", "lexical", "semantic", "literal"] = "hybrid"
    output: Path | None = None


def main(argv: list[str]) -> int:
    """Acquire exclusive canonical ownership for maintenance; never approve source beliefs."""
    parser = argparse.ArgumentParser(prog="ember-memory index")
    parser.add_argument("command", choices=("index",))
    parser.add_argument("operation", choices=("status", "rebuild", "update", "search", "archive"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--store", type=UUID, required=True)
    parser.add_argument("--encoder", type=Path)
    parser.add_argument("--query", default="")
    parser.add_argument(
        "--mode", choices=("hybrid", "lexical", "semantic", "literal"), default="hybrid"
    )
    parser.add_argument("--output", type=Path, help="New private result file; never overwritten")
    try:
        args = _Arguments.model_validate(vars(parser.parse_args(argv)))
        encoder = BgeEncoder(args.encoder) if args.encoder is not None else None
        with CanonicalEngine(args.root, encoder=encoder) as engine:
            engine.maintenance_store(args.store)
            if args.operation in {"rebuild", "update"}:
                if encoder is None:
                    parser.error(
                        "rebuild/update require --encoder pointing to installed local assets"
                    )
                generation = (
                    engine.update_index(args.store, encoder)
                    if args.operation == "update"
                    else engine.rebuild_index(args.store, encoder)
                )
                print(generation.model_dump_json())
            elif args.operation == "status":
                generation, path = engine.indexes[args.store].active()
                if file_sha256(path) != generation.file_sha256:
                    raise IndexError()
                with closing(connect(path, immutable=True)) as connection:
                    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise IndexError()
                print(generation.model_dump_json())
            else:
                store = engine.character(args.store)
                response = (
                    store.search_archive(ArchiveQuery(text=args.query, mode=args.mode))
                    if args.operation == "archive"
                    else store.search(
                        MemoryQuery(
                            text=args.query,
                            mode="lexical" if args.mode == "literal" else args.mode,
                        )
                    )
                )
                print(response.coverage.model_dump_json())
                print("Results: " + str(len(response.results)))
                if args.output is not None:
                    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                        stream.write(response.model_dump_json(indent=2) + "\n")
        return 0
    except (MemoryError, OSError, sqlite3.Error, ValidationError):
        print("Index operation failed; check ownership, installed assets and local index status.")
        return 2
