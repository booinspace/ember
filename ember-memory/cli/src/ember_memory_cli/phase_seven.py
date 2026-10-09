"""Explicit character profile, native maintenance acceptance and related inspection."""

import argparse
import json
import sys
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from ember_memory_abi.common import CanonicalId, ContractModel
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.interfaces import ContextRequest, MemoryQuery
from ember_memory_abi.related import MemoryReference, RelatedRequest
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.consolidation import (
    ConsolidateRequest,
    NativeAcceptanceRequest,
    NativeAcceptanceService,
    consolidate,
)
from ember_memory_engine.database import row_text
from ember_memory_engine.errors import StoreOwnershipError
from ember_memory_engine.profile import FIELDS


class Arguments(ContractModel):
    """Narrow argparse input before using canonical IDs or local state."""

    command: Literal["profile", "native", "related", "consolidate"]
    action: str
    character: CanonicalId
    root: Path
    field: str | None = None
    subject: CanonicalId | None = None
    evidence: bool = False
    job: CanonicalId | None = None
    limit: int = 100
    offset: int = 0
    input: Path | None = None
    survivor: CanonicalId | None = None
    duplicate: CanonicalId | None = None
    survivor_revision: int = 1
    duplicate_revision: int = 1
    key: str | None = None


def main(argv: list[str]) -> int:
    """Maintenance needs the existing exclusive owner; diagnostics omit private values."""
    parser = argparse.ArgumentParser(prog="ember-memory " + argv[0])
    if argv[0] == "profile":
        parser.add_argument(
            "action", choices=("inspect", "enable", "disable", "exclude", "include", "bind")
        )
        parser.add_argument("--field", choices=FIELDS)
        parser.add_argument("--subject", type=UUID)
        parser.add_argument("--evidence", action="store_true")
    elif argv[0] == "native":
        parser.add_argument("action", choices=("accept",))
        parser.add_argument("--job", type=UUID, required=True)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--offset", type=int, default=0)
    elif argv[0] == "consolidate":
        parser.add_argument("action", choices=("apply",))
        parser.add_argument("--survivor", type=UUID, required=True)
        parser.add_argument("--duplicate", type=UUID, required=True)
        parser.add_argument("--survivor-revision", type=int, default=1)
        parser.add_argument("--duplicate-revision", type=int, default=1)
        parser.add_argument("--key", required=True)
    else:
        parser.add_argument("action", choices=("inspect",))
        parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--character", type=UUID, required=True)
    parser.add_argument("--root", type=Path, default=Path(".local/data"))
    try:
        args = Arguments.model_validate({"command": argv[0], **vars(parser.parse_args(argv[1:]))})
        if not 1 <= args.limit <= 100 or args.offset < 0:
            parser.error("Use a limit between 1 and 100 and a nonnegative offset.")
        with CanonicalEngine(args.root) as engine:
            store = engine.character(
                args.character, authority=MutationAuthority(mode="confirmed_user")
            )
            if args.command == "consolidate":
                if args.survivor is None or args.duplicate is None or args.key is None:
                    raise ValueError("Consolidation arguments are required.")
                print(
                    consolidate(
                        engine,
                        args.character,
                        ConsolidateRequest(
                            idempotency_key=args.key,
                            survivor=MemoryReference(
                                memory_id=args.survivor, revision=args.survivor_revision
                            ),
                            duplicate=MemoryReference(
                                memory_id=args.duplicate, revision=args.duplicate_revision
                            ),
                        ),
                        authority=MutationAuthority(mode="confirmed_user"),
                    ).model_dump_json()
                )
            elif args.command == "related":
                if args.input is None:
                    parser.error("--input is required")
                with args.input.open("rb") as stream:
                    data = stream.read(65537)
                if len(data) > 65536:
                    raise ValueError("Related request too large.")
                print(
                    store.lookup_related(RelatedRequest.model_validate_json(data)).model_dump_json()
                )
            elif args.command == "native":
                service = NativeAcceptanceService(engine, args.character)
                rows = store.database.read(
                    lambda connection: connection.execute(
                        (
                            "SELECT candidate_id FROM processing_candidates WHERE job_id=? "
                            "ORDER BY ordinal,candidate_id LIMIT ? OFFSET ?"
                        ),
                        (str(args.job), args.limit, args.offset),
                    ).fetchall()
                )
                for row in rows:
                    identity = UUID(row_text(row, "candidate_id"))
                    receipt = service.accept(
                        NativeAcceptanceRequest(
                            candidate_id=identity, idempotency_key="native-v1:" + str(identity)
                        )
                    )
                    print(receipt.model_dump_json())
            else:
                settings = store.profile_settings()
                if args.action == "inspect":
                    context = store.retrieve_context(
                        ContextRequest(
                            memory_query=MemoryQuery(mode="structured", limit=1),
                            memory_token_budget=1,
                            include_profile=True,
                            include_pinned=True,
                        )
                    )
                    value = context.model_dump(mode="json")
                    value["settings"] = settings.model_dump(mode="json")
                    if args.evidence:
                        value["evidence"] = [
                            item.model_dump(mode="json")
                            for result in context.memories
                            for item in store.get_sources(result.memory.memory_id)
                        ]
                    print(json.dumps(value, ensure_ascii=False))
                else:
                    changes: dict[str, object] = {}
                    if args.action in {"enable", "disable"}:
                        changes["enabled"] = args.action == "enable"
                    elif args.action == "bind":
                        if args.subject is None:
                            parser.error(
                                "bind requires --subject (the intended canonical user entity)"
                            )
                        changes["subject_id"] = args.subject
                    else:
                        if args.field is None:
                            parser.error("include/exclude requires --field")
                        fields = set(settings.excluded)
                        if args.action == "exclude":
                            fields.add(args.field)
                        else:
                            fields.discard(args.field)
                        changes["excluded"] = tuple(sorted(fields))
                    store.configure_profile(
                        type(settings).model_validate({**settings.model_dump(), **changes})
                    )
                    print(store.profile_settings().model_dump_json())
        return 0
    except StoreOwnershipError:
        print(
            "Memory maintenance needs the existing owner to release this root. "
            "Wait for the active ingest or character to finish, then retry.",
            file=sys.stderr,
        )
        return 2
    except (MemoryError, ValidationError, OSError, ValueError):
        print(
            "Phase 7 maintenance input or local state is invalid; private values are not logged.",
            file=sys.stderr,
        )
        return 2
