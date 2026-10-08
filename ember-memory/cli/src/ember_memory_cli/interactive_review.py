"""Human terminal decisions; model/source content never supplies prompt responses."""

import json
from typing import Literal, Protocol

from ember_memory_abi.common import CanonicalId
from ember_memory_abi.errors import InvalidPredicateError, UnsupportedWriteError
from ember_memory_abi.predicates import initial_predicates, validate_memory_write
from ember_memory_engine.library import KnowledgeSelection
from ember_memory_engine.processing_contracts import ReviewEntry, ReviewFile
from ember_memory_engine.processing_review import ReviewService


class IngestionCancelled(Exception):
    """Operator quit/EOF; committed review decisions remain available."""


class Console(Protocol):
    """Inject terminal I/O in tests, retaining real engine authority checks."""

    def read(self, prompt: str) -> str: ...
    def write(self, text: str) -> None: ...


class Terminal:
    """Private review display, never enabled for redirected stdout."""

    def read(self, prompt: str) -> str:
        """Read human input; EOF never becomes approval."""
        try:
            return input(prompt)
        except EOFError as error:
            raise IngestionCancelled() from error

    def write(self, text: str) -> None:
        """Display intended private review content to the operator."""
        print(text, flush=True)


def choice(
    console: Console, prompt: str, choices: tuple[str, ...], default: str | None = None
) -> str:
    """Accept declared human responses only; invalid input never authorizes a write."""
    while True:
        answer = console.read(prompt).strip().casefold()
        if answer in {"q", "quit"}:
            raise IngestionCancelled()
        if not answer and default is not None:
            return default
        if answer in choices:
            return answer
        console.write("Choose: " + ", ".join(choices) + ". Enter q to quit.")


def confirm(console: Console, prompt: str, *, default: bool = False) -> bool:
    """Require an affirmative response; no/EOF never grants authority."""
    suffix = " [Y/n/q]: " if default else " [y/N/q]: "
    answer = choice(console, prompt + suffix, ("y", "yes", "n", "no"), "y" if default else "n")
    return answer in {"y", "yes"}


def show_candidate(console: Console, entry: ReviewEntry, ordinal: int) -> None:
    """Escape controls so source text cannot redraw or forge terminal prompts."""
    content = entry.candidate.draft.content
    console.write(
        f"\nCandidate {ordinal}: "
        + json.dumps(entry.policy_decision.inputs.proposal.statement, ensure_ascii=True)
    )
    console.write("Kind/modality: " + json.dumps([content.kind, content.modality]))
    console.write(
        f"Effective confidence: {content.confidence:.2f}; "
        f"model estimate: {entry.policy_decision.inputs.proposal.confidence:.2f}; "
        f"importance: {content.importance:.2f}"
    )
    for quote in entry.quotes:
        console.write("Original evidence: " + json.dumps(quote, ensure_ascii=True))
    console.write("Policy: " + json.dumps(entry.policy_decision.verdict.reasons))
    console.write(f"Candidate ID: {entry.candidate.candidate_id}")


def review_candidates(
    service: ReviewService, job: CanonicalId, console: Console
) -> tuple[KnowledgeSelection, ...]:
    """Page review, persist decisions immediately and preserve prior decisions on restart."""
    mode = choice(
        console,
        "Review: [i] individual, [a] accept all pending, [r] reject all pending [i]: ",
        ("i", "a", "r"),
        "i",
    )
    bulk: Literal["approve", "reject"] | None = None
    if mode in {"a", "r"}:
        verb = "Accept" if mode == "a" else "Reject"
        if confirm(console, f"{verb} ALL pending candidates in this job?"):
            bulk = "approve" if mode == "a" else "reject"
    approved: list[KnowledgeSelection] = []
    offset, ordinal = 0, 0
    while True:
        page = service.export(job, offset=offset, limit=100, include_decisions=True)
        for entry in page.entries:
            ordinal += 1
            decision = entry.decision
            if decision == "pending":
                show_candidate(console, entry, ordinal)
                supported = True
                try:
                    validate_memory_write(entry.candidate.draft, predicates=initial_predicates())
                except (InvalidPredicateError, UnsupportedWriteError):
                    supported = False
                    console.write("Current memory semantics cannot accept this candidate.")
                selected = bulk
                if selected is None:
                    answer = choice(
                        console,
                        "[a] accept, [r] reject, [s] skip, [all] accept remaining [s]: ",
                        ("a", "r", "s", "all"),
                        "s",
                    )
                    if answer == "all":
                        if confirm(console, "Accept this and ALL remaining pending candidates?"):
                            bulk = "approve"
                            selected = bulk
                    elif answer in {"a", "r"}:
                        selected = "approve" if answer == "a" else "reject"
                if selected == "approve" and not supported:
                    console.write("Left pending; unsupported candidates are never force-written.")
                elif selected is not None:
                    changed = entry.model_copy(update={"decision": selected})
                    service.apply(ReviewFile(job_id=job, entries=(changed,)))
                    decision = selected
                    console.write("Saved decision: " + selected)
            if decision == "approve":
                approved.append(
                    KnowledgeSelection(
                        knowledge_id=entry.candidate.candidate_id,
                        revision=entry.candidate.revision,
                    )
                )
        if page.next_offset is None:
            break
        offset = page.next_offset
    return tuple(approved)
