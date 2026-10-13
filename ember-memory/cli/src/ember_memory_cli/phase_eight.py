"""Offline origin/capture controls and explicitly reviewed temporal enrichment."""

import argparse
import sys
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError

from ember_memory_abi.common import CanonicalId, ContractModel
from ember_memory_abi.errors import MemoryError
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.capture import CaptureService, CaptureSettings
from ember_memory_engine.enrichment import (
    TemporalBatchReview,
    TemporalEnrichmentService,
    TemporalReview,
)
from ember_memory_engine.processing_contracts import DateSupport, ProcessingError

COMMANDS = frozenset({"recall-policy", "capture", "enrich"})


class EnrichmentInput(ContractModel):
    """Operator selects a character memory and its existing exact source reference."""

    memory_id: CanonicalId
    source: SourceRef
    support: DateSupport
    timezone: str = "Europe/London"


class PinnedEnrichmentInput(EnrichmentInput):
    """Batch discovery pins explicit character revisions; resume cannot reinterpret new text."""

    expected_revision: int = Field(ge=1)


class EnrichmentBatchInput(ContractModel):
    entries: tuple[PinnedEnrichmentInput, ...] = Field(min_length=1, max_length=64)


class Arguments(ContractModel):
    """Typed command boundary; file payloads are separately validated and byte bounded."""

    command: Literal["recall-policy", "capture", "enrich"]
    action: str
    character: CanonicalId
    root: Path
    input: Path | None = None
    output: Path | None = None
    proposal: CanonicalId | None = None
    yes: bool = False
    checkpoint: str | None = None


def _read(path: Path | None, *, limit: int = 65536) -> bytes:
    if path is None:
        raise ValueError("An input file is required.")
    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("Control/review input exceeds its byte limit.")
    return payload


def _write(path: Path | None, model: ContractModel) -> None:
    if path is None:
        raise ValueError("An output file is required.")
    payload = model.model_dump_json(indent=2) + "\n"
    if len(payload.encode("utf-8")) > 4 * 1024 * 1024:
        raise ValueError("Review output exceeds its byte limit.")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)


def main(argv: list[str]) -> int:
    """Use the canonical exclusive owner; import/capture/recall authority stay distinct."""
    command = argv[0]
    parser = argparse.ArgumentParser(prog="ember-memory " + command)
    parser.add_argument(
        "action",
        choices=("inspect", "configure", "retry")
        if command == "capture"
        else ("inspect", "configure")
        if command == "recall-policy"
        else ("propose", "review", "apply", "propose-batch", "apply-batch"),
    )
    parser.add_argument("--character", type=UUID, required=True)
    parser.add_argument("--root", type=Path, default=Path(".local/data"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--proposal", type=UUID)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--checkpoint")
    try:
        args = Arguments.model_validate({"command": command, **vars(parser.parse_args(argv[1:]))})
        authority = MutationAuthority(mode="confirmed_user")
        with CanonicalEngine(args.root) as engine:
            store = engine.character(args.character, authority=authority)
            if args.command == "recall-policy":
                policy = (
                    store.configure_recall(RecallSelection.model_validate_json(_read(args.input)))
                    if args.action == "configure"
                    else store.recall_selection()
                )
                print(policy.model_dump_json())
            elif args.command == "capture":
                capture = CaptureService(engine, args.character)
                if args.action == "retry":
                    if not args.yes or not args.checkpoint:
                        raise ValueError("Retry requires --checkpoint and --yes.")
                    capture.retry(args.checkpoint, authority)
                    print("queued")
                    return 0
                policy = (
                    capture.configure(
                        CaptureSettings.model_validate_json(_read(args.input)), authority
                    )
                    if args.action == "configure"
                    else capture.settings()
                )
                print(policy.model_dump_json())
                if args.action == "inspect":
                    for checkpoint in capture.pending():
                        print(checkpoint.model_dump_json())
            else:
                service = TemporalEnrichmentService(engine, args.character, authority=authority)
                if args.action == "propose-batch":
                    batch = EnrichmentBatchInput.model_validate_json(
                        _read(args.input, limit=1024 * 1024)
                    )
                    reviews = tuple(
                        service.propose(
                            entry.memory_id,
                            entry.source,
                            entry.support,
                            timezone=entry.timezone,
                            expected_revision=entry.expected_revision,
                        )
                        for entry in batch.entries
                    )
                    _write(args.output, TemporalBatchReview(reviews=reviews))
                elif args.action == "apply-batch":
                    if not args.yes:
                        raise ValueError("Use --yes to apply explicit batch review decisions.")
                    batch = TemporalBatchReview.model_validate_json(
                        _read(args.input, limit=4 * 1024 * 1024)
                    )
                    outcomes = service.apply_batch(batch)
                    for outcome in outcomes:
                        print(outcome.model_dump_json())
                    return 2 if any(item.outcome == "failed" for item in outcomes) else 0
                elif args.action == "propose":
                    value = EnrichmentInput.model_validate_json(_read(args.input))
                    review = service.propose(
                        value.memory_id, value.source, value.support, timezone=value.timezone
                    )
                    _write(args.output, review)
                    print(str(review.proposal.proposal_id))
                elif args.action == "review":
                    if args.proposal is None:
                        raise ValueError("A proposal ID is required.")
                    _write(args.output, service.get(args.proposal))
                else:
                    if not args.yes:
                        raise ValueError(
                            "Use --yes to apply the explicit recorded review decision."
                        )
                    review = TemporalReview.model_validate_json(_read(args.input))
                    receipt = service.apply(review)
                    if receipt is not None:
                        print(receipt.model_dump_json())
                    else:
                        print(review.decision)
        return 0
    except (MemoryError, ProcessingError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, ValidationError):
        print(
            "Control or enrichment input is invalid; private values are not logged.",
            file=sys.stderr,
        )
        return 2
