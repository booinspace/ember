"""Graph-compatible grouping and tokenizer-bounded chunks with exact original spans."""

import re
from dataclasses import dataclass
from uuid import UUID, uuid5

from ember_memory_abi.archive import ProcessingSpecRef
from ember_memory_abi.common import canonical_object_sha256
from ember_memory_abi.evidence import Evidence, SourceRef, make_evidence_span
from ember_memory_abi.memory import ArchiveChunk, ChunkSpan

from .errors import EmbeddingError
from .search_contracts import Encoder

_NAMESPACE = UUID("8f40f1b7-5e58-5a45-9dea-4370ef1a246a")
_EVIDENCE_NAMESPACE = UUID("5753d438-e380-4a33-a3a8-e3e9baac11ed")


@dataclass(frozen=True)
class Unit:
    """Visible source part on one known path; ambiguous/alternative units never combine."""

    source: SourceRef
    text: str
    role: str
    conversation: str
    branch: str
    position: int
    source_at_us: int | None


@dataclass(frozen=True)
class Fragment:
    unit: Unit
    start: int
    end: int


def fragments(unit: Unit, encoder: Encoder) -> tuple[Fragment, ...]:
    """Prefer paragraph/sentence endings, then exact token offsets; retain 64-token overlap."""
    text = unit.text
    if not text:
        return ()
    offsets = encoder.offsets(text)
    if not offsets:
        return (Fragment(unit, 0, len(text)),)
    boundaries = tuple(match.end() for match in re.finditer(r"\n\s*\n|[.!?](?:\s+|$)", text))
    result: list[Fragment] = []
    token = 0
    while token < len(offsets):
        last = min(token + 400, len(offsets))
        start = 0 if token == 0 else offsets[token][0]
        end = len(text) if last == len(offsets) else offsets[last - 1][1]
        if last < len(offsets):
            choices = [
                value
                for value in boundaries
                if start < value <= end and value >= offsets[min(token + 200, last - 1)][1]
            ]
            if choices:
                end = choices[-1]
                while last > token and offsets[last - 1][1] > end:
                    last -= 1
        if end <= start or last <= token:
            raise EmbeddingError()
        result.append(Fragment(unit, start, end))
        if end == len(text):
            break
        token = max(token + 1, last - 64)
    return tuple(result)


def compatible(previous: Unit, following: Unit) -> bool:
    """Group direct selected-path adjacency with bounded gap/topic continuation evidence."""
    if (
        previous.conversation != following.conversation
        or previous.branch != "selected"
        or following.branch != "selected"
    ):
        return False
    if following.position != previous.position + 1:
        return False
    if previous.source_at_us is None or following.source_at_us is None:
        return True
    gap = following.source_at_us - previous.source_at_us
    if 0 <= gap <= 600_000_000:
        return True
    words = set(re.findall(r"\w{4,}", previous.text.casefold()))
    continuing = following.text.casefold().startswith(("also ", "and ", "so ", "yes "))
    topic = bool(words.intersection(re.findall(r"\w{4,}", following.text.casefold())))
    return 0 <= gap <= 1_200_000_000 and (continuing or topic)


def attributed_text(parts: tuple[Fragment, ...]) -> str:
    """Render the exact embedding template before checking its actual token budget."""
    return "\n\n".join(
        (item.unit.role if item.unit.role in {"user", "assistant", "system", "tool"} else "other")
        + ": "
        + item.unit.text[item.start : item.end]
        for item in parts
    )


def make_chunk(parts: tuple[Fragment, ...], encoder: Encoder) -> ArchiveChunk:
    """Pack attributed text; source mappings cover only original spans, never labels/glue."""
    text = ""
    mappings: list[ChunkSpan] = []
    for fragment in parts:
        role = (
            fragment.unit.role
            if fragment.unit.role in {"user", "assistant", "system", "tool"}
            else "other"
        )
        prefix = ("\n\n" if text else "") + role + ": "
        text += prefix
        start = len(text)
        original = fragment.unit.text[fragment.start : fragment.end]
        text += original
        source = fragment.unit.source
        span = make_evidence_span(fragment.unit.text, fragment.start, fragment.end)
        identity = canonical_object_sha256(
            {"source": source.model_dump(mode="json"), "span": span.model_dump(mode="json")}
        )
        # Evidence ABI requires UUID4. Derive stable UUID bytes, fixing UUID4 bits,
        # without substituting this derived span for any accepted canonical belief.
        evidence_id = UUID(bytes=uuid5(_EVIDENCE_NAMESPACE, identity).bytes, version=4)
        evidence = Evidence(evidence_id=evidence_id, source=source, span=span)
        mappings.append(
            ChunkSpan(start_codepoint=start, end_codepoint=len(text), evidence=evidence)
        )
    if encoder.token_count(text) > 512:
        raise EmbeddingError()
    specification = ProcessingSpecRef(
        name="ember.graph_attributed_chunks",
        version="0.1.0",
        specification_sha256=canonical_object_sha256(
            {
                "tokenizer": encoder.specification.tokenizer_sha256,
                "target": 400,
                "maximum": 480,
                "overlap": 64,
                "template": "role_attribution_v1",
                "gap_seconds": 600,
            }
        ),
    )
    chunk_id = uuid5(
        _NAMESPACE,
        canonical_object_sha256(
            {
                "spec": specification.model_dump(mode="json"),
                "text": text,
                "spans": [span.model_dump(mode="json") for span in mappings],
            }
        ),
    )
    return ArchiveChunk(
        chunk_id=chunk_id, specification=specification, text=text, source_spans=tuple(mappings)
    )


def group_chunks(units: tuple[Unit, ...], encoder: Encoder) -> tuple[ArchiveChunk, ...]:
    """Pack whole compatible fragments, target 400/max 480 content tokens; preserve overlap."""
    result: list[ArchiveChunk] = []
    current: list[Fragment] = []
    previous: Unit | None = None
    for unit in units:
        for fragment in fragments(unit, encoder):
            if current and (
                previous is None
                or not compatible(previous, unit)
                or encoder.token_count(attributed_text((*current, fragment))) > 480
            ):
                result.append(make_chunk(tuple(current), encoder))
                # Whole short final units overlap only within a compatible path.
                last = current[-1]
                current = (
                    [last]
                    if compatible(last.unit, unit)
                    and encoder.token_count(last.unit.text[last.start : last.end]) <= 64
                    else []
                )
            current.append(fragment)
            previous = unit
            if encoder.token_count(make_chunk(tuple(current), encoder).text) >= 400:
                result.append(make_chunk(tuple(current), encoder))
                current = []
    if current:
        result.append(make_chunk(tuple(current), encoder))
    return tuple(result)
