"""Original synthetic sources and trusted bindings for canonical-engine acceptance."""

from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from ember_memory_abi.archive import (
    ActorAttribution,
    BranchSelection,
    ContentPart,
    Conversation,
    GraphNode,
    ImportCursor,
    Message,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
    SourceLink,
)
from ember_memory_abi.common import CanonicalId
from ember_memory_abi.evidence import (
    Evidence,
    EvidenceAssertion,
    SourceRef,
    make_evidence_span,
    source_text_sha256,
)
from ember_memory_abi.memory import (
    Entity,
    EntityRef,
    FactPayload,
    MemoryContent,
    MemoryDraft,
    StringValue,
)
from ember_memory_abi.temporal import TemporalBounds
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.engine import CanonicalEngine
from ember_memory_engine.models import MutationAuthority, SourceGrant, SourceKey

TEXT = "My primary GPU is SYNTHETIC_A; café 🍰 is a synthetic example."
STAMP = "2026-10-07T12:00:00.000000Z"


class TestClock:
    """Controllable aware clock; the engine independently enforces recorded monotonicity."""

    __test__ = False

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 7, 12, tzinfo=UTC)

    def now(self) -> datetime:
        """Return the injected current instant."""
        return self.value

    def advance(self, seconds: int = 1) -> None:
        """Advance observation of wall time deterministically."""
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> TestClock:
    """Create one deterministic clock for each isolated acceptance scenario."""
    return TestClock()


@pytest.fixture
def engine(tmp_path: Path, clock: TestClock) -> Generator[CanonicalEngine]:
    """Create and close canonical stores inside pytest's temporary directory only."""
    with CanonicalEngine(tmp_path / "canonical", clock=clock) as instance:
        yield instance


@dataclass
class SourceFixture:
    """Typed synthetic store/snapshot references, with no physical/provider identity in ABI."""

    batch: NormalizedArchiveBatch
    message: Message
    evidence_ref: SourceRef
    character_id: CanonicalId
    subject: EntityRef


@pytest.fixture
def source_fixture(engine: CanonicalEngine) -> SourceFixture:
    """Import a complete synthetic graph and grant only explicitly selected snapshots."""
    library = engine.create_library("synthetic-account")
    writer = engine.archive(library.store_id)
    keys = writer.prepare_keys(
        tuple(
            SourceKey(adapter="synthetic", record_kind=kind, external_id="synthetic-" + kind)
            for kind in ("conversation", "graph_node", "message")
        )
    )
    refs = {
        assignment.key.record_kind: SourceRef(
            source_store_id=library.store_id,
            record_kind=assignment.key.record_kind,
            record_id=assignment.record_id,
            record_revision=1,
        )
        for assignment in keys
    }
    part = ContentPart(
        part_id=uuid4(),
        kind="text",
        status="available",
        text=TEXT,
        content_sha256=source_text_sha256(TEXT),
    )
    message = Message(
        source=refs["message"],
        recorded_at=STAMP,
        conversation=refs["conversation"],
        actor=ActorAttribution(role="user"),
        parts=(part,),
    )
    node = GraphNode(
        source=refs["graph_node"],
        recorded_at=STAMP,
        conversation=refs["conversation"],
        message=refs["message"],
        children=(SourceLink(status="missing", raw_reference_sha256="f" * 64),),
    )
    conversation = Conversation(
        source=refs["conversation"],
        recorded_at=STAMP,
        selection=BranchSelection(
            status="ambiguous", selected_node=refs["graph_node"], reason_codes=("missing_child",)
        ),
    )
    batch = NormalizedArchiveBatch(
        source_store_id=library.store_id,
        source_namespace=library.namespace,
        idempotency_key="initial-import",
        processing_spec=ProcessingSpecRef(
            name="ember.synthetic", version="0.1.0", specification_sha256="a" * 64
        ),
        records=(conversation, node, message),
        resume_cursor=ImportCursor(
            logical_inventory_sha256="b" * 64,
            component_sha256="c" * 64,
            next_record_index=3,
            adapter="synthetic",
            adapter_version="0.1.0",
        ),
    )
    writer.write_batch(batch, keys=keys)
    character = engine.create_character()
    subject = EntityRef(store_id=character.store_id, entity_id=uuid4())
    engine.put_entity(
        character.store_id, Entity(reference=subject, revision=1, name="Synthetic user")
    )
    engine.grant_source(
        character.store_id,
        SourceGrant(
            references=(message.source,),
            capabilities=frozenset({"evidence_read", "archive_search"}),
        ),
    )
    evidence_ref = SourceRef.model_validate(
        {**message.source.model_dump(), "part_id": part.part_id}
    )
    return SourceFixture(batch, message, evidence_ref, character.store_id, subject)


@pytest.fixture
def draft(source_fixture: SourceFixture) -> MemoryDraft:
    """Create synthetic confirmed-user input; importing it never activates knowledge."""
    evidence = Evidence(
        evidence_id=uuid4(),
        source=source_fixture.evidence_ref,
        span=make_evidence_span(TEXT, 0, len(TEXT)),
    )
    return MemoryDraft(
        content=MemoryContent(
            kind="fact",
            payload=FactPayload(
                subject=source_fixture.subject,
                predicate="hardware.primary_gpu",
                definition_version="0.1.0",
                value=StringValue(value="SYNTHETIC_A"),
            ),
            modality="FACTUAL_ASSERTION",
            confidence=0.95,
            importance=0.7,
            evidence=(
                EvidenceAssertion(
                    evidence=evidence,
                    relation="supports",
                    modality="FACTUAL_ASSERTION",
                    authority="source_claim_not_trusted",
                    verdict="unvalidated",
                ),
            ),
        ),
        temporal=TemporalBounds(
            valid_from="2026-01-01T00:00:00.000000Z", basis="explicit_open_end"
        ),
    )


@pytest.fixture
def store(engine: CanonicalEngine, source_fixture: SourceFixture) -> SqliteMemoryStore:
    """Explicit trusted confirmed-user composition; model claims cannot construct this binding."""
    return engine.character(
        source_fixture.character_id, authority=MutationAuthority(mode="confirmed_user")
    )
