"""Explicit local maintenance entry points; source content never confers mutation authority."""

import argparse
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, TypeAdapter, ValidationError

from ember_memory_abi.common import CanonicalId, ContractModel, timestamp_from_datetime
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.interfaces import MemoryQuery, RememberRequest
from ember_memory_abi.memory import CandidateMemory, MemoryDraft, MemorySource
from ember_memory_engine import CanonicalEngine, MutationAuthority, SourceGrant
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_importers.normalization.contracts import SourceImportError
from ember_memory_importers.normalization.extraction import explicit_primary_gpu, propose_message

from .maintenance import import_source

COMMANDS = {"import", "character", "remember", "recall", "sources", "propose", "confirm"}


class _RootArguments(ContractModel):
    root: Path


class _ImportArguments(_RootArguments):
    command: Literal["import"]
    adapter: Literal["scraper-json", "chatgpt-mapping", "chatgpt-zip"]
    source: Path
    source_namespace: str


class _CharacterArguments(_RootArguments):
    command: Literal["character"]


class _BoundArguments(_RootArguments):
    character: str


class _RememberArguments(_BoundArguments):
    command: Literal["remember"]
    key: str
    text: str | None
    text_file: Path | None
    explicit_fact: bool


class _RecallArguments(_BoundArguments):
    command: Literal["recall"]
    query: str


class _SourcesArguments(_BoundArguments):
    command: Literal["sources"]
    memory: str


class _ProposeArguments(_BoundArguments):
    command: Literal["propose"]
    source_store: str
    record: str
    output: Path


class _ConfirmArguments(_BoundArguments):
    command: Literal["confirm"]
    input: Path
    yes: bool


type _Arguments = Annotated[
    _ImportArguments
    | _CharacterArguments
    | _RememberArguments
    | _RecallArguments
    | _SourcesArguments
    | _ProposeArguments
    | _ConfirmArguments,
    Field(discriminator="command"),
]


def _read(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Maintenance input exceeds its bound.")
    return data


def _id(value: str) -> CanonicalId:
    adapter: TypeAdapter[CanonicalId] = TypeAdapter(CanonicalId)
    return adapter.validate_python(UUID(value))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ember-memory")
    commands = parser.add_subparsers(dest="command", required=True)
    imported = commands.add_parser(
        "import", help="Preserve/normalize sources without activating memories"
    )
    imported.add_argument("adapter", choices=("scraper-json", "chatgpt-mapping", "chatgpt-zip"))
    imported.add_argument("source", type=Path)
    imported.add_argument("--source-namespace", required=True)
    for name in sorted(COMMANDS - {"import"}):
        sub = commands.add_parser(name)
        if name != "character":
            sub.add_argument("--character", required=True)
        if name == "remember":
            sub.add_argument("--key", required=True)
            text = sub.add_mutually_exclusive_group(required=True)
            text.add_argument("--text")
            text.add_argument("--text-file", type=Path)
            sub.add_argument("--explicit-fact", action="store_true")
        elif name == "recall":
            sub.add_argument("--query", default="")
        elif name == "sources":
            sub.add_argument("--memory", required=True)
        elif name == "propose":
            sub.add_argument("--source-store", required=True)
            sub.add_argument("--record", required=True)
            sub.add_argument("--output", type=Path, required=True)
        elif name == "confirm":
            sub.add_argument("--input", type=Path, required=True)
            sub.add_argument("--yes", action="store_true", required=True)
    for sub in commands.choices.values():
        sub.add_argument("--root", type=Path, default=Path(".local/data"))
    return parser


def main(argv: list[str]) -> int:
    """Run explicit operator actions; reject bad inputs with safe, content-free diagnostics."""
    adapter: TypeAdapter[_Arguments] = TypeAdapter(_Arguments)
    args = adapter.validate_python(vars(_parser().parse_args(argv)))
    try:
        if args.command == "import":
            report = import_source(
                args.root, args.source, args.source_namespace, args.adapter.replace("-", "_")
            )
            print(report.model_dump_json(indent=2))
            return 0
        with CanonicalEngine(args.root) as engine:
            if args.command == "character":
                character = engine.create_character()
                subject = ensure_subject(engine, character.store_id)
                print(subject.model_dump_json())
                return 0
            character = _id(args.character)
            subject = ensure_subject(engine, character)
            reader = engine.character(character)
            if args.command == "remember":
                text = _read(args.text_file, 65536).decode("utf-8") if args.text_file else args.text
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Empty memory text.")
                payload = explicit_primary_gpu(text, subject) if args.explicit_fact else None
                if args.explicit_fact and payload is None:
                    raise ValueError("Unsupported explicit fact.")
                author = MemoryAuthoringService(
                    engine, subject, authority=MutationAuthority(mode="confirmed_user")
                )
                print(
                    author.create(
                        NewMemoryInput(idempotency_key=args.key, text=text, payload=payload)
                    ).model_dump_json()
                )
            elif args.command == "recall":
                print(reader.search(MemoryQuery(text=args.query)).model_dump_json(indent=2))
            elif args.command == "sources":
                print(
                    TypeAdapter(tuple[MemorySource, ...])
                    .dump_json(reader.get_sources(_id(args.memory)))
                    .decode()
                )
            elif args.command == "propose":
                from ember_memory_abi.archive import Message

                message = engine.archive(_id(args.source_store)).latest_record(
                    "message", _id(args.record)
                )
                if not isinstance(message, Message):
                    raise ValueError("No message source.")
                drafts = propose_message(message, subject)
                if len(drafts) != 1:
                    raise ValueError("No supported whole-message explicit fact.")
                candidate = CandidateMemory(
                    candidate_id=uuid4(),
                    owner_store_id=character,
                    revision=1,
                    draft=drafts[0],
                    recorded_at=timestamp_from_datetime(engine.clock.now()),
                )
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("x", encoding="utf-8") as output:
                    output.write(candidate.model_dump_json(indent=2))
                print("One unapproved candidate written for explicit local review.")
            elif args.command == "confirm":
                candidate = CandidateMemory.model_validate_json(_read(args.input, 1024 * 1024))
                if candidate.owner_store_id != character:
                    raise ValueError("Candidate belongs to another character.")
                references = tuple(
                    item.evidence.source for item in candidate.draft.content.evidence
                )
                for reference in references:
                    engine.validate(
                        next(
                            item.evidence
                            for item in candidate.draft.content.evidence
                            if item.evidence.source == reference
                        )
                    )
                engine.grant_source(
                    character,
                    SourceGrant(references=references, capabilities=frozenset({"evidence_read"})),
                )
                draft = MemoryDraft.model_validate(candidate.draft.model_dump())
                store = engine.character(
                    character, authority=MutationAuthority(mode="confirmed_user")
                )
                print(
                    store.remember(
                        RememberRequest(
                            idempotency_key="confirm:" + str(candidate.candidate_id), draft=draft
                        )
                    ).model_dump_json()
                )
        return 0
    except (MemoryError, SourceImportError) as error:
        print(str(error))
        return 2
    except (OSError, ValueError, ValidationError):
        print("Maintenance input or local state is invalid; no input values are logged.")
        return 2
