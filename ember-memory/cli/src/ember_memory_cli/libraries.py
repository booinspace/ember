"""Typed operator-only Phase 4 maintenance commands; no model/harness entry point."""

import argparse
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, TypeAdapter, ValidationError

from ember_memory_abi.archive import Message
from ember_memory_abi.common import CanonicalId, ContractModel, timestamp_from_datetime
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.interfaces import ForgetRequest
from ember_memory_abi.memory import CandidateMemory, Entity, EntityRef
from ember_memory_engine import CanonicalEngine, MutationAuthority, SourceGrant
from ember_memory_engine.bundles import BundleService, restore_bundles
from ember_memory_engine.library import (
    ApprovalRequest,
    KnowledgeSelection,
    LibraryService,
    SeedRequest,
)
from ember_memory_importers.normalization.extraction import propose_message

COMMANDS = {
    "library-entity",
    "library-propose",
    "library-approve",
    "library-show",
    "seed",
    "grant",
    "revoke",
    "forget-preview",
    "forget",
    "bundle",
}


class _Root(ContractModel):
    root: Path


class _LibraryEntity(_Root):
    command: Literal["library-entity"]
    library: str
    input: Path
    yes: bool


class _LibraryPropose(_Root):
    command: Literal["library-propose"]
    library: str
    subject: str
    record: str
    output: Path


class _LibraryApprove(_Root):
    command: Literal["library-approve"]
    library: str
    input: Path
    key: str
    yes: bool


class _LibraryShow(_Root):
    command: Literal["library-show"]
    library: str
    knowledge: str
    revision: int


class _Seed(_Root):
    command: Literal["seed"]
    character: str
    library: str
    knowledge: list[str]
    key: str
    yes: bool


class _Grant(_Root):
    command: Literal["grant", "revoke"]
    character: str
    input: Path
    yes: bool


class _ForgetPreview(_Root):
    command: Literal["forget-preview"]
    character: str
    memory: str


class _Forget(_Root):
    command: Literal["forget"]
    character: str
    memory: str
    revision: int
    reason: str
    key: str
    yes: bool


class _BundleSource(_Root):
    command: Literal["bundle"]
    action: Literal["source"]
    library: str
    output: Path


class _BundleCharacter(_Root):
    command: Literal["bundle"]
    action: Literal["character"]
    character: str
    output: Path
    self_contained: bool
    memory: list[str] | None


class _BundleRestore(_Root):
    command: Literal["bundle"]
    action: Literal["restore"]
    bundle: list[Path]


type _Action = Annotated[
    _LibraryEntity
    | _LibraryPropose
    | _LibraryApprove
    | _LibraryShow
    | _Seed
    | _Grant
    | _ForgetPreview
    | _Forget,
    Field(discriminator="command"),
]
type _Bundle = Annotated[
    _BundleSource | _BundleCharacter | _BundleRestore, Field(discriminator="action")
]


def parse_id(value: str) -> CanonicalId:
    """Validate canonical UUID4 operator input at the maintenance boundary."""
    adapter: TypeAdapter[CanonicalId] = TypeAdapter(CanonicalId)
    return adapter.validate_python(UUID(value))


def _read(path: Path) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Input bound exceeded.")
    return data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ember-memory")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in sorted(COMMANDS - {"bundle"}):
        sub = commands.add_parser(name)
        sub.add_argument("--root", type=Path, default=Path(".local/data"))
        if name.startswith("library-") or name == "seed":
            sub.add_argument("--library", required=True)
        if name in {"seed", "grant", "revoke", "forget-preview", "forget"}:
            sub.add_argument("--character", required=True)
        if name in {"library-entity", "library-approve", "grant", "revoke"}:
            sub.add_argument("--input", type=Path, required=True)
        if name in {"library-entity", "library-approve", "seed", "grant", "revoke", "forget"}:
            sub.add_argument("--yes", action="store_true", required=True)
        if name in {"library-approve", "seed", "forget"}:
            sub.add_argument("--key", required=True)
        if name == "library-propose":
            sub.add_argument("--subject", required=True)
            sub.add_argument("--record", required=True)
            sub.add_argument("--output", type=Path, required=True)
        if name == "library-show":
            sub.add_argument("--knowledge", required=True)
            sub.add_argument("--revision", required=True, type=int)
        if name == "seed":
            sub.add_argument(
                "--knowledge",
                action="append",
                required=True,
                help="KNOWLEDGE_UUID@REVISION; repeat to select multiple",
            )
        if name in {"forget", "forget-preview"}:
            sub.add_argument("--memory", required=True)
        if name == "forget":
            sub.add_argument("--revision", required=True, type=int)
            sub.add_argument("--reason", required=True)
    bundle = commands.add_parser("bundle", help="Exclusive offline backup/export/restore")
    actions = bundle.add_subparsers(dest="action", required=True)
    source = actions.add_parser("source", help="Full source backup, including verbatim raw files")
    source.add_argument("--library", required=True)
    source.add_argument("--output", type=Path, required=True)
    character = actions.add_parser("character", help="Linked state or selected evidence closure")
    character.add_argument("--character", required=True)
    character.add_argument("--output", type=Path, required=True)
    character.add_argument("--self-contained", action="store_true")
    character.add_argument(
        "--memory", action="append", help="Select memory UUIDs; requires --self-contained"
    )
    restore = actions.add_parser("restore", help="Validate dependencies and restore to a new root")
    restore.add_argument("--bundle", action="append", type=Path, required=True)
    for sub in actions.choices.values():
        sub.add_argument("--root", type=Path, default=Path(".local/data"))
    return parser


def _bundle(args: _Bundle) -> None:
    if isinstance(args, _BundleRestore):
        restored = restore_bundles(tuple(args.bundle), args.root)
        print(f"Restored {len(restored)} canonical stores; providers disabled.")
        return
    service = BundleService(args.root)
    if isinstance(args, _BundleSource):
        manifest = service.export_source(parse_id(args.library), args.output)
    else:
        manifest = service.export_character(
            parse_id(args.character),
            args.output,
            mode="self_contained" if args.self_contained else "linked",
            memory_ids=tuple(parse_id(item) for item in args.memory)
            if args.memory is not None
            else None,
        )
    print(manifest.model_dump_json(indent=2))


def _run(engine: CanonicalEngine, args: _Action) -> None:
    service = LibraryService(engine, authority=MutationAuthority(mode="confirmed_user"))
    if isinstance(args, _LibraryEntity):
        entity = Entity.model_validate_json(_read(args.input))
        service.put_entity(parse_id(args.library), entity)
        print("Library-owned entity registered.")
    elif isinstance(args, _LibraryPropose):
        library = parse_id(args.library)
        message = engine.archive(library).latest_record("message", parse_id(args.record))
        if not isinstance(message, Message):
            raise ValueError("Message unavailable.")
        subject = EntityRef(store_id=library, entity_id=parse_id(args.subject))
        drafts = propose_message(message, subject)
        if len(drafts) != 1:
            raise ValueError("No supported explicit claim.")
        candidate = CandidateMemory(
            candidate_id=uuid4(),
            owner_store_id=library,
            revision=1,
            draft=drafts[0],
            recorded_at=timestamp_from_datetime(engine.clock.now()),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            output.write(candidate.model_dump_json(indent=2))
        print("Unapproved library candidate written for review of exact evidence.")
    elif isinstance(args, _LibraryApprove):
        candidate = CandidateMemory.model_validate_json(_read(args.input))
        approved = service.approve(
            parse_id(args.library), ApprovalRequest(idempotency_key=args.key, candidate=candidate)
        )
        print(approved.model_dump_json(indent=2))
    elif isinstance(args, _LibraryShow):
        print(
            service.knowledge(
                parse_id(args.library),
                KnowledgeSelection(knowledge_id=parse_id(args.knowledge), revision=args.revision),
            ).model_dump_json(indent=2)
        )
    elif isinstance(args, _Seed):
        selections: list[KnowledgeSelection] = []
        for raw in args.knowledge:
            identity, revision = raw.rsplit("@", 1)
            selections.append(
                KnowledgeSelection(knowledge_id=parse_id(identity), revision=int(revision))
            )
        print(
            service.seed(
                parse_id(args.character),
                SeedRequest(
                    idempotency_key=args.key,
                    library_id=parse_id(args.library),
                    selections=tuple(selections),
                ),
            ).model_dump_json()
        )
    elif isinstance(args, _Grant):
        grant = SourceGrant.model_validate_json(_read(args.input))
        if args.command == "grant":
            engine.grant_source(parse_id(args.character), grant)
        else:
            engine.revoke_grant(parse_id(args.character), grant)
        print("Selected source policy updated; existing beliefs retained.")
    elif isinstance(args, _ForgetPreview):
        print(
            engine.character(parse_id(args.character))
            .preview_forget(parse_id(args.memory))
            .model_dump_json(indent=2)
        )
    else:
        request = ForgetRequest(
            idempotency_key=args.key,
            memory_id=parse_id(args.memory),
            expected_revision=args.revision,
            reason=args.reason,
        )
        print(
            engine.character(
                parse_id(args.character), authority=MutationAuthority(mode="confirmed_user")
            )
            .forget(request)
            .model_dump_json()
        )


def main(argv: list[str]) -> int:
    """Run operator-approved maintenance with safe failures and no runtime/model startup."""
    try:
        namespace = vars(_parser().parse_args(argv))
        if namespace["command"] == "bundle":
            adapter: TypeAdapter[_Bundle] = TypeAdapter(_Bundle)
            _bundle(adapter.validate_python(namespace))
        else:
            action: TypeAdapter[_Action] = TypeAdapter(_Action)
            args = action.validate_python(namespace)
            with CanonicalEngine(args.root) as engine:
                _run(engine, args)
        return 0
    except MemoryError as error:
        print(str(error))
        return 2
    except (OSError, ValueError, ValidationError):
        print("Maintenance input or local state is invalid; no input values are logged.")
        return 2
