"""PrecisionMemBench source-only mapping into native library approvals and seeds.

Approved statements are exact source `content` episodes, with one entity identity
per row (`canonical_name` and `aliases` verbatim). Type/status map to modality:
preferences, assertions, inferred beliefs, and uncertain/open questions. Both
lanes explicitly request only current assertion/preference/belief modalities;
open questions are not guidance hits (AMB scores its own open-question tier).
Resolved or superseded rows remain exact source documents and PAST_STATE
approvals, never active character seeds. Unknown types/statuses stay source-only.

`created_at` dates the source snapshot, not the underlying claim. No observation,
validity, replacement edge, or predecessor/successor dates are inferred. Exclude
why_it_matters, pins, confidence/importance hints, reinforcement, expertise,
relation links, and editorial metadata. Scoped lanes use source scope tags.
Fixture approval is synthetic setup in disposable stores, not past human review,
a model extraction result, or a policy for adopting private records.
"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from scripts.benchmark_memory_amb_archive import Retrieval

from ember_memory_abi.archive import (
    ContentPart,
    Document,
    ImportCursor,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.common import canonical_object_sha256, timestamp_from_datetime
from ember_memory_abi.evidence import Evidence, EvidenceAssertion, SourceRef, make_evidence_span
from ember_memory_abi.interfaces import MemoryQuery, RequestOptions
from ember_memory_abi.memory import (
    CandidateMemory,
    Entity,
    EntityRef,
    EpisodePayload,
    MemoryContent,
    MemoryDraft,
)
from ember_memory_abi.temporal import SourceTimestamp
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.identity_recall import CURRENT_BELIEF, CURRENT_MODALITIES, IDENTITY_RECALL
from ember_memory_engine.library import (
    ApprovalRequest,
    KnowledgeSelection,
    LibraryService,
    SeedRequest,
)
from ember_memory_engine.models import MutationAuthority

type BeliefLane = Literal["seeded-belief", "explicit-scope", "current-belief"]
ADAPTER = "ember.pmb-fixture-beliefs"
SPEC = ProcessingSpecRef(
    name=ADAPTER,
    version="0.1.0",
    specification_sha256=canonical_object_sha256({"adapter": ADAPTER, "layout": "statement-v1"}),
)
_TYPES = frozenset({"preference", "decision", "entity", "relation", "open_question"})
_STATUSES = frozenset({"active", "inferred", "exploratory", "superseded"})


class SeedBelief(BaseModel):
    """Allowlisted public seed fields; fixture `_id` is used only for attribution."""

    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    belief_id: str = Field(alias="_id", min_length=1)
    user_id: str = Field(min_length=1)
    type: str
    canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...]
    content: str = Field(min_length=1, repr=False)
    epistemic_status: str
    created_at: str | None = None
    resolved_at: str | None = None
    superseded_by: str | None = None
    scope: tuple[str, ...] = ()

    @property
    def modality(self) -> str | None:
        """Translate explicit source state, never choose winners by source recency."""
        if self.type not in _TYPES or self.epistemic_status not in _STATUSES:
            return None
        if self.epistemic_status == "superseded" or self.resolved_at or self.superseded_by:
            return "PAST_STATE"
        if self.type == "open_question" or self.epistemic_status == "exploratory":
            return "UNCERTAINTY"
        if self.epistemic_status == "inferred":
            return "BELIEF"
        return "PREFERENCE" if self.type == "preference" else "FACTUAL_ASSERTION"


def load_seed(path: Path) -> list[SeedBelief]:
    """Read validated source rows; caller verifies pinned fixture hash first."""
    return TypeAdapter(list[SeedBelief]).validate_json(path.read_bytes())


def _source_time(raw: str | None) -> SourceTimestamp:
    if raw is None:
        return SourceTimestamp()
    try:
        parsed = datetime.fromisoformat(raw)
        value = timestamp_from_datetime(parsed)
    except ValueError:
        return SourceTimestamp(raw_value=raw, ambiguity_reasons=("unusable_source_timestamp",))
    return SourceTimestamp(value=value, raw_value=raw, basis="source_snapshot_creation")


@dataclass
class _Unit:
    store: SqliteMemoryStore
    tags: dict[UUID, frozenset[str]]
    accepted: dict[UUID, str]


class BeliefBenchmark:
    """Native seeded character per source user, with no archive grants or query-time writes."""

    def __init__(
        self, engine: CanonicalEngine, seeds: Sequence[SeedBelief], lane: BeliefLane, limit: int
    ) -> None:
        if len({item.belief_id for item in seeds}) != len(seeds):
            raise ValueError("Duplicate PrecisionMemBench source ID.")
        self.lane = lane
        self.limit = limit
        self.units: dict[str, _Unit] = {}
        self.setup: Counter[str] = Counter(approved=0, seeded=0, history_only=0, source_only=0)
        groups: dict[tuple[str, frozenset[str]], list[SeedBelief]] = defaultdict(list)
        for row in seeds:
            tags = frozenset(row.scope if lane != "seeded-belief" else ())
            groups[row.user_id, tags].append(row)
        service = LibraryService(engine, authority=MutationAuthority(mode="confirmed_user"))
        for index, ((user, tags), rows) in enumerate(groups.items()):
            if user not in self.units:
                character = engine.create_character().store_id
                self.units[user] = _Unit(engine.character(character), tags={}, accepted={})
            unit = self.units[user]
            library = engine.create_library(f"pmb-fixture-beliefs-{index}").store_id
            unit.tags[library] = tags
            self._seed_library(engine, service, unit, library, rows)

    def _seed_library(
        self,
        engine: CanonicalEngine,
        service: LibraryService,
        unit: _Unit,
        library: UUID,
        rows: Sequence[SeedBelief],
    ) -> None:
        stamp = timestamp_from_datetime(engine.clock.now())
        docs: list[Document] = []
        for row in rows:
            span = make_evidence_span(row.content, 0, len(row.content))
            docs.append(
                Document(
                    source=SourceRef(
                        source_store_id=library,
                        record_kind="document",
                        record_id=uuid4(),
                        record_revision=1,
                    ),
                    recorded_at=stamp,
                    created_time=_source_time(row.created_at),
                    parts=(
                        ContentPart(
                            part_id=uuid4(),
                            kind="text",
                            status="available",
                            text=row.content,
                            content_sha256=span.source_content_sha256,
                        ),
                    ),
                )
            )
        writer = engine.archive(library)
        inventory = canonical_object_sha256([[row.content, row.created_at] for row in rows])
        # ponytail: pinned corpus fits one bounded archive/seed batch per library.
        writer.write_batch(
            NormalizedArchiveBatch(
                source_store_id=library,
                source_namespace=writer.namespace,
                idempotency_key=inventory,
                processing_spec=SPEC,
                records=tuple(docs),
                resume_cursor=ImportCursor(
                    logical_inventory_sha256=inventory,
                    component_sha256=inventory,
                    next_record_index=len(docs),
                    adapter=ADAPTER,
                    adapter_version=SPEC.version,
                ),
            ),
            keys=(),
        )
        selections: list[KnowledgeSelection] = []
        exact_sources: dict[tuple[UUID, str, UUID, int, UUID | None], str] = {}
        for row, doc in zip(rows, docs, strict=True):
            modality = row.modality
            if modality is None:
                self.setup["source_only"] += 1
                continue
            reference = EntityRef(store_id=library, entity_id=uuid4())
            service.put_entity(
                library,
                Entity(
                    reference=reference, revision=1, name=row.canonical_name, aliases=row.aliases
                ),
            )
            source = doc.source.model_copy(update={"part_id": doc.parts[0].part_id})
            approved = service.approve(
                library,
                ApprovalRequest(
                    idempotency_key=f"approve-{source.record_id}",
                    candidate=CandidateMemory(
                        candidate_id=source.record_id,
                        owner_store_id=library,
                        revision=1,
                        recorded_at=stamp,
                        draft=MemoryDraft(
                            content=MemoryContent(
                                kind="episode",
                                payload=EpisodePayload(
                                    event=row.content, participants=(reference,)
                                ),
                                modality=modality,
                                confidence=0.5,
                                importance=0.5,
                                evidence=(
                                    EvidenceAssertion(
                                        evidence=Evidence(
                                            evidence_id=uuid4(),
                                            source=source,
                                            span=make_evidence_span(
                                                row.content, 0, len(row.content)
                                            ),
                                        ),
                                        relation="supports",
                                        modality=modality,
                                        authority="synthetic_public_fixture",
                                        verdict="supported",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            )
            self.setup["approved"] += 1
            if modality == "PAST_STATE":
                self.setup["history_only"] += 1
                continue
            selections.append(
                KnowledgeSelection(knowledge_id=approved.knowledge_id, revision=approved.revision)
            )
            exact_sources[source.identity_key()] = row.belief_id
        if not selections:
            return
        receipt = service.seed(
            unit.store.character_id,
            SeedRequest(
                idempotency_key=f"seed-{library}", library_id=library, selections=tuple(selections)
            ),
        )
        for accepted in receipt.affected:
            memory = unit.store.get(accepted.memory_id, revision=accepted.revision)
            (assertion,) = memory.content.evidence
            origin = memory.origin
            if (
                origin is None
                or origin.origin_source_store_id != library
                or origin.origin_memory_id != assertion.evidence.source.record_id
                or origin.origin_memory_id == memory.memory_id
                or memory.lifecycle != "active"
            ):
                raise ValueError("Expected independent native seed with original exact evidence.")
            unit.accepted[memory.memory_id] = exact_sources.pop(
                assertion.evidence.source.identity_key()
            )
        if exact_sources:
            raise ValueError("Native seeds did not cover all selected source rows.")
        self.setup["seeded"] += len(receipt.affected)

    def retrieve(
        self, text: str, user: str | None, budget: int, *, scope: tuple[str, ...] = ()
    ) -> Retrieval:
        """Search with fixed user binding, optionally narrow by explicit caller scope tags."""
        limit = min(self.limit, budget)
        if not text.strip() or limit == 0:
            return Retrieval((), 0.0, "skipped")
        unit = self.units.get(user) if user is not None else None
        if unit is None:
            return Retrieval((), 0.0, "no_unit")
        origins = (
            [str(library) for library, tags in unit.tags.items() if tags.intersection(scope)]
            if self.lane != "seeded-belief" and scope
            else None
        )
        identity_options: dict[str, JsonValue] = (
            {}
            if origins is None
            else {
                "origin_store_ids": list[JsonValue](origins),
            }
        )
        current = self.lane == "current-belief"
        request = MemoryQuery(
            mode="structured",
            text=text,
            limit=limit,
            modalities=() if current else CURRENT_MODALITIES,
            options=RequestOptions(
                extensions={(CURRENT_BELIEF if current else IDENTITY_RECALL): identity_options}
            ),
        )
        started = perf_counter()
        response = unit.store.search(request)
        ids: list[str] = []
        for result in response.results:
            memory = result.memory
            if memory.character_id != unit.store.character_id or (
                origins is not None
                and (
                    memory.origin is None
                    or str(memory.origin.origin_source_store_id) not in origins
                )
            ):
                raise ValueError(
                    "Native belief retrieval returned a hit outside its authorized unit."
                )
            ids.append(unit.accepted[memory.memory_id])
        return Retrieval(
            tuple(dict.fromkeys(ids)),
            (perf_counter() - started) * 1000,
            response.coverage.status,
            response.coverage.reason_codes,
        )
