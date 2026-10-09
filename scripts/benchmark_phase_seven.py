"""Isolated diverse corpus and paired Phase 7 profile/validation performance evidence."""

import argparse
import asyncio
import json
import sqlite3
from collections.abc import AsyncGenerator, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid4

from pydantic import Field

from ember_agent.character.core import CharacterCore, compact_pin, prompt_cost
from ember_agent.inference.contracts import GatewayEvent, GatewayRequest, GenerationUsage
from ember_memory_abi.archive import (
    ContentPart,
    Document,
    ImportCursor,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.common import CanonicalId, ContractModel, timestamp_from_datetime
from ember_memory_abi.evidence import (
    Evidence,
    EvidenceAssertion,
    SourceRef,
    make_evidence_span,
    source_text_sha256,
)
from ember_memory_abi.interfaces import ContextRequest, MemoryQuery, RetrievalContext
from ember_memory_abi.memory import (
    CandidateMemory,
    EpisodePayload,
    FactPayload,
    Memory,
    MemoryContent,
    MemoryDraft,
    StringValue,
)
from ember_memory_abi.related import RelatedRequest, RelatedResponse
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.character import SqliteMemoryStore
from ember_memory_engine.database import load_model
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.index_builder import IndexBuilder
from ember_memory_engine.library import (
    ApprovalRequest,
    KnowledgeSelection,
    LibraryService,
    SeedRequest,
)
from ember_memory_engine.profile import select
from ember_memory_engine.related import certificate, validate


class Arguments(ContractModel):
    root: Path
    encoder: Path
    records: int = Field(default=100000, ge=1000, le=200000)
    samples: int = Field(default=40, ge=20, le=100)
    output: Path
    reuse: bool = False


class SyntheticMarker(ContractModel):
    """Narrow persisted fixture identity before reuse; never accept an arbitrary data root."""

    version: str
    records: int
    character: CanonicalId


class Measurement(ContractModel):
    samples: int
    median_ms: float
    p95_ms: float


def measure(operation: Callable[[], object], samples: int) -> Measurement:
    values: list[float] = []
    for _ in range(5):
        operation()
    for _ in range(samples):
        start = perf_counter()
        operation()
        values.append((perf_counter() - start) * 1000)
    values.sort()
    return Measurement(
        samples=samples, median_ms=median(values), p95_ms=values[(samples * 95 + 99) // 100 - 1]
    )


def corpus(engine: CanonicalEngine, count: int) -> CanonicalId:
    """Construct only explicitly synthetic data in a new root; never import real sources."""
    character = engine.create_character().store_id
    subject = ensure_subject(engine, character)
    author = MemoryAuthoringService(
        engine, subject, authority=MutationAuthority(mode="confirmed_user")
    )
    for predicate, value in (
        ("user.preferred_name", "Rowan"),
        ("user.pronouns", "they/them"),
        ("user.communication_style", "concise replies"),
        ("user.timezone", "Europe/London"),
    ):
        author.create(
            NewMemoryInput(
                idempotency_key=predicate,
                text="Synthetic confirmed observation: " + value,
                payload=FactPayload(
                    subject=subject,
                    predicate=predicate,
                    definition_version="0.1.0",
                    value=StringValue(value=value),
                ),
                modality="PREFERENCE"
                if predicate.endswith("communication_style")
                else "FACTUAL_ASSERTION",
            )
        )
    archive = engine.archive(character)
    store = engine.character(character)
    topics = (
        "NAS DNS Tailscale resolver",
        "router VLAN firewall",
        "ambient music sequencer",
        "software compiler debugging",
        "network NAS AdGuard names",
        "travel train itinerary",
        "VR headset tracking",
        "speaker audio mixer",
    )
    for offset in range(0, count, 500):
        documents: list[Document] = []
        drafts: list[MemoryDraft] = []
        stamp = timestamp_from_datetime(engine.clock.now())
        for first in range(offset, min(count, offset + 500), 50):
            lines = [
                "Synthetic project "
                + str(n)
                + ": "
                + topics[n % len(topics)]
                + " configuration experiment "
                + str(n)
                + "."
                for n in range(first, min(count, first + 50))
            ]
            text = "\n".join(lines)
            part_id = uuid4()
            source = SourceRef(
                source_store_id=character,
                record_kind="document",
                record_id=uuid4(),
                record_revision=1,
                part_id=part_id,
            )
            document = Document(
                source=source.model_copy(update={"part_id": None}),
                recorded_at=stamp,
                parts=(
                    ContentPart(
                        part_id=part_id,
                        kind="text",
                        status="available",
                        text=text,
                        content_sha256=source_text_sha256(text),
                    ),
                ),
            )
            documents.append(document)
            start = 0
            for line in lines:
                evidence = Evidence(
                    evidence_id=uuid4(),
                    source=source,
                    span=make_evidence_span(text, start, start + len(line)),
                )
                drafts.append(
                    MemoryDraft(
                        content=MemoryContent(
                            kind="episode",
                            payload=EpisodePayload(event=line, participants=(subject,)),
                            modality="FACTUAL_ASSERTION",
                            confidence=1.0,
                            importance=0.7,
                            evidence=(
                                EvidenceAssertion(
                                    evidence=evidence,
                                    relation="supports",
                                    modality="FACTUAL_ASSERTION",
                                    authority="synthetic_confirmed_fixture",
                                    verdict="supported",
                                ),
                            ),
                        )
                    )
                )
                start += len(line) + 1
        batch = NormalizedArchiveBatch(
            source_store_id=character,
            source_namespace=archive.namespace,
            idempotency_key="fixture:" + str(offset),
            processing_spec=ProcessingSpecRef(
                name="ember.synthetic-benchmark", version="0.1.0", specification_sha256="a" * 64
            ),
            records=tuple(documents),
            resume_cursor=ImportCursor(
                adapter="synthetic_benchmark",
                adapter_version="0.1.0",
                logical_inventory_sha256="b" * 64,
                component_sha256="c" * 64,
                next_record_index=offset + len(drafts),
            ),
        )
        archive.write_batch(batch)

        def write(connection: sqlite3.Connection, drafts: list[MemoryDraft] = drafts) -> None:
            with store.database.transaction(connection):
                recorded, _ = store.database.recorded_stamp(connection)
                for draft in drafts:
                    # Fixture construction uses known exact generated spans. This
                    # maintenance benchmark is never an adoption path for user data.
                    store.state.create_memory(connection, draft, recorded)

        store.database.write(write)
        if offset % 10000 == 0:
            print("Synthetic accepted memories prepared:", offset + len(drafts), flush=True)
    return character


class TracedClient(InProcessAsyncMemoryClient):
    """Count actual public calls and bounded work, never infer counters from design."""

    def __init__(self, store: SqliteMemoryStore) -> None:
        super().__init__(store)
        self.context_calls = 0
        self.validation_calls = 0
        self.expansion_calls = 0
        self.rows_examined = 0
        self.source_calls = 0
        self.max_metadata_bytes = 0
        self.max_references = 0

    async def retrieve_context(self, request: ContextRequest) -> RetrievalContext:
        self.context_calls += 1
        return await super().retrieve_context(request)

    async def lookup_related(self, request: RelatedRequest) -> RelatedResponse:
        if request.intent == "validate":
            self.validation_calls += 1
        else:
            self.expansion_calls += 1
        if request.dependencies:
            refs = request.dependencies
            self.max_references = max(self.max_references, len(refs.memories) + len(refs.sources))
            self.max_metadata_bytes = max(
                self.max_metadata_bytes, len(refs.model_dump_json().encode())
            )
        response = await super().lookup_related(request)
        self.rows_examined += response.rows_examined
        self.source_calls += response.source_calls
        return response


class SyntheticStream:
    """Measure every delivery validation around progress/text/end without GPU contention."""

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        for sequence in range(100):
            yield GatewayEvent(
                request_id=request.request_id,
                context_id=request.context_id,
                sequence=sequence,
                kind="progress",
            )
        for sequence in range(100, 120):
            yield GatewayEvent(
                request_id=request.request_id,
                context_id=request.context_id,
                sequence=sequence,
                kind="text",
                text="sample ",
            )
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=120,
            kind="end",
            usage=GenerationUsage(
                prompt_tokens=1,
                output_tokens=20,
                total_seconds=0.1,
                first_visible_seconds=0.01,
                tokens_per_second=200.0,
            ),
        )


def imported_refresh_fixture(engine: CanonicalEngine, character: CanonicalId) -> None:
    """Add one explicitly reviewed foreign-source belief to exercise grant-scoped KNN."""
    library = engine.create_library("synthetic-refresh:" + uuid4().hex).store_id
    text = "Synthetic NAS DNS resolver configuration baseline."
    part_id = uuid4()
    stamp = timestamp_from_datetime(engine.clock.now())
    source = SourceRef(
        source_store_id=library,
        record_kind="document",
        record_id=uuid4(),
        record_revision=1,
        part_id=part_id,
    )
    document = Document(
        source=source.model_copy(update={"part_id": None}),
        recorded_at=stamp,
        parts=(
            ContentPart(
                part_id=part_id,
                kind="text",
                status="available",
                text=text,
                content_sha256=source_text_sha256(text),
            ),
        ),
    )
    writer = engine.archive(library)
    writer.write_batch(
        NormalizedArchiveBatch(
            source_store_id=library,
            source_namespace=writer.namespace,
            idempotency_key="fixture",
            processing_spec=ProcessingSpecRef(
                name="ember.synthetic-benchmark", version="0.1.0", specification_sha256="a" * 64
            ),
            records=(document,),
            resume_cursor=ImportCursor(
                adapter="synthetic_benchmark",
                adapter_version="0.1.0",
                logical_inventory_sha256="b" * 64,
                component_sha256="c" * 64,
                next_record_index=1,
            ),
        )
    )
    evidence = Evidence(
        evidence_id=uuid4(), source=source, span=make_evidence_span(text, 0, len(text))
    )
    draft = MemoryDraft(
        content=MemoryContent(
            kind="episode",
            payload=EpisodePayload(event=text),
            modality="FACTUAL_ASSERTION",
            confidence=1.0,
            importance=0.7,
            evidence=(
                EvidenceAssertion(
                    evidence=evidence,
                    relation="supports",
                    modality="FACTUAL_ASSERTION",
                    authority="synthetic_confirmed_fixture",
                    verdict="supported",
                ),
            ),
        )
    )
    service = LibraryService(engine, authority=MutationAuthority(mode="confirmed_user"))
    approved = service.approve(
        library,
        ApprovalRequest(
            idempotency_key="approve",
            candidate=CandidateMemory(
                candidate_id=uuid4(),
                owner_store_id=library,
                revision=1,
                draft=draft,
                recorded_at=stamp,
            ),
        ),
    )
    service.seed(
        character,
        SeedRequest(
            idempotency_key="seed:" + str(library),
            library_id=library,
            selections=(KnowledgeSelection(knowledge_id=approved.knowledge_id, revision=1),),
        ),
    )


def main() -> int:
    """Refuse existing roots/output; use local CPU embeddings and aggregate-only reports."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--records", type=int, default=100000)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--reuse", action="store_true", help="Only a marked synthetic root created by this script"
    )
    args = Arguments.model_validate(vars(parser.parse_args()))
    marker = args.root / "synthetic-benchmark.json"
    if (
        (args.root.exists() and not args.reuse)
        or args.output.exists()
        or (args.reuse and not marker.is_file())
    ):
        raise ValueError("Benchmark needs a new root and output.")
    encoder = BgeEncoder(args.encoder)
    with CanonicalEngine(args.root, encoder=encoder) as engine:
        if args.reuse:
            metadata = SyntheticMarker.model_validate_json(marker.read_text(encoding="utf-8"))
            if (
                metadata.version != "ember.synthetic-benchmark@0.1.0"
                or metadata.records != args.records
            ):
                raise ValueError("Synthetic benchmark marker mismatch.")
            character = metadata.character
        else:
            character = corpus(engine, args.records)
            marker.write_text(
                json.dumps(
                    {
                        "version": "ember.synthetic-benchmark@0.1.0",
                        "records": args.records,
                        "character": str(character),
                    }
                ),
                encoding="utf-8",
            )
        # Fixture-only adjustment for an unreleased schema-5 benchmark root created
        # during implementation. Production schema-4 migration creates this index.
        fixture_store = engine.character(character)

        def prepare_fixture_index(connection: sqlite3.Connection) -> None:
            connection.execute(
                "CREATE INDEX IF NOT EXISTS memory_recent_order ON memory_revisions("
                "lifecycle,recorded_at_us DESC,memory_id,revision,kind,modality,importance,"
                "valid_from_us,valid_until_us)"
            )

        fixture_store.database.write(prepare_fixture_index)
        print("Building diverse CPU search generation...", flush=True)
        build_started = perf_counter()
        if not args.reuse:
            IndexBuilder(engine, encoder).rebuild(character)
        elif engine.indexes[character].active()[0].layout != "ember.search-layout@0.5.0":
            engine.update_index(character, encoder)
        build_ms = (perf_counter() - build_started) * 1000
        store = engine.character(character)
        queries = (
            "How does my NAS resolve DNS?",
            "Explain a VLAN firewall.",
            "How do audio mixers work?",
            "What does VR tracking do?",
        )
        cold: list[float] = []
        for query in queries:
            start = perf_counter()
            store.retrieve_context(
                ContextRequest(
                    memory_query=MemoryQuery(text=query),
                    memory_token_budget=4096,
                    include_profile=True,
                )
            )
            cold.append((perf_counter() - start) * 1000)
        on = ContextRequest(
            memory_query=MemoryQuery(text=queries[0]),
            memory_token_budget=4096,
            include_profile=True,
        )
        off = on.model_copy(update={"include_profile": False})
        cold_off: list[float] = []
        for query in queries:
            start = perf_counter()
            store.retrieve_context(off.model_copy(update={"memory_query": MemoryQuery(text=query)}))
            cold_off.append((perf_counter() - start) * 1000)

        def profile_path() -> object:
            def read(connection: sqlite3.Connection) -> object:
                selection, memories = select(store.state, connection)
                refs = certificate(
                    store.state, connection, memories, profile_epoch=selection.selection_epoch
                )
                checked = validate(
                    store.state, connection, RelatedRequest(intent="validate", dependencies=refs)
                )
                if not checked.valid:
                    raise ValueError("Unexpected fixture validation failure.")
                return json.dumps([compact_pin(m) for m in memories], ensure_ascii=False)

            return store.database.read(read)

        measurements = {
            "profile_selection_validation_format": measure(profile_path, args.samples),
            "retrieve_profile_enabled": measure(lambda: store.retrieve_context(on), args.samples),
            "retrieve_profile_disabled": measure(lambda: store.retrieve_context(off), args.samples),
        }
        matched = {}
        candidates = store.database.read(
            lambda c: tuple(
                load_model(r, Memory)
                for r in c.execute(
                    "SELECT * FROM memory_revisions WHERE kind='episode' "
                    "ORDER BY memory_id LIMIT 128"
                )
            )
        )
        for count in (6, 24, 128):
            members = candidates[:count]
            cert = store.database.read(
                lambda c, members=members: certificate(store.state, c, members)
            )

            serial_requests = tuple(
                RelatedRequest(
                    intent="validate",
                    dependencies=store.database.read(
                        lambda c, member=member: certificate(store.state, c, (member,))
                    ),
                    row_budget=512,
                )
                for member in members
            )

            def serial(requests: tuple[RelatedRequest, ...] = serial_requests) -> object:
                return all(store.lookup_related(request).valid for request in requests)

            request = RelatedRequest(intent="validate", dependencies=cert, row_budget=512)
            matched[str(count)] = {
                "serial": measure(serial, args.samples).model_dump(),
                "batch": measure(
                    lambda request=request: store.lookup_related(request), args.samples
                ).model_dump(),
                "serial_calls": count,
                "batch_calls": 1,
                "references": len(cert.memories) + len(cert.sources),
                "metadata_bytes": len(cert.model_dump_json().encode()),
            }

        async def stream_measurement() -> dict[str, object]:
            client = TracedClient(store)
            core = CharacterCore(client, SyntheticStream(), model_tag="synthetic-benchmark")
            start = perf_counter()
            turn = await core.prepare(queries[0])
            prepared_ms = (perf_counter() - start) * 1000
            stream_start = perf_counter()
            events = [event async for event in core.stream(turn)]
            streamed_ms = (perf_counter() - stream_start) * 1000
            await client.aclose()
            return {
                "prepare_ms": prepared_ms,
                "stream_delivery_validation_ms": streamed_ms,
                "events": len(events),
                "progress_events": 100,
                "text_events": 20,
                "retrieve_context_calls": client.context_calls,
                "validate_calls": client.validation_calls,
                "resolve_expand_calls": client.expansion_calls,
                "canonical_rows_examined": client.rows_examined,
                "external_source_calls": client.source_calls,
                "maximum_references": client.max_references,
                "maximum_metadata_bytes": client.max_metadata_bytes,
                "serialized_prompt_guard": prompt_cost(turn.request.messages),
                "retained_pins": turn.retained_pins,
                "ordinary_memories": turn.retained_memories,
            }

        stream_report = asyncio.run(stream_measurement())
        complete_turn = measure(lambda: asyncio.run(stream_measurement()), 20)
        before_refresh = store.profile_settings()
        refresh: dict[str, Measurement] = {}
        for enabled in (False, True):
            trusted = engine.character(
                character, authority=MutationAuthority(mode="confirmed_user")
            )
            trusted.configure_profile(before_refresh.model_copy(update={"enabled": enabled}))
            refresh[str(enabled)] = measure(lambda: store.retrieve_context(on), args.samples)
        imported_refresh_fixture(engine, character)
        refresh_values: list[float] = []
        refresh_started = perf_counter()
        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ember-benchmark-refresh"
        ) as pool:
            future = pool.submit(engine.update_index, character, encoder)
            while not future.done():
                start = perf_counter()
                store.retrieve_context(on)
                refresh_values.append((perf_counter() - start) * 1000)
            generation = future.result()
        refresh_ms = (perf_counter() - refresh_started) * 1000
        foreign_scope_measurement = measure(lambda: store.retrieve_context(on), args.samples)
        output = {
            "version": "ember.phase-seven-benchmark@0.1.0",
            "corpus": "diverse synthetic accepted episodes; four explicit profile facts",
            "accepted_memories": store.database.read(
                lambda c: c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            ),
            "embedding": encoder.specification.model_dump(mode="json"),
            "index_build_ms": build_ms,
            "cold_retrieve_ms": cold,
            "profile_disabled_initial_pass_ms": cold_off,
            "cold_protocol": (
                "First process pass with enabled then disabled; first encoder load is not symmetric"
            ),
            "measurements": {key: value.model_dump() for key, value in measurements.items()},
            "matched_validation": matched,
            "streamed_turn": stream_report,
            "complete_prepared_streamed_turn": complete_turn.model_dump(),
            "index_refresh": {
                "duration_ms": refresh_ms,
                "foreground_samples_ms": refresh_values,
                "scope": "one reviewed imported belief added during immutable index copy",
                "published_generation": str(generation.generation),
            },
            "foreign_grant_scope_retrieve": foreign_scope_measurement.model_dump(),
            "refresh_measurements": {key: value.model_dump() for key, value in refresh.items()},
            "serial_baseline": (
                "Identical exact-revision/policy/time/source/grant validation, "
                "one memory per public request"
            ),
            "ordinary_related_expansion_calls": stream_report["resolve_expand_calls"],
            "counting_mode": "conservative_utf8_guard",
            "gates": {
                "profile_warm_p95_le_10ms": measurements[
                    "profile_selection_validation_format"
                ].p95_ms
                <= 10,
                "overall_warm_p95_le_150ms": max(
                    measurements["retrieve_profile_enabled"].p95_ms,
                    foreign_scope_measurement.p95_ms,
                )
                <= 150,
            },
            "limits": (
                "Single Windows CPU/SSD run; not a multi-platform, million-memory "
                "or real-user capacity certification."
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(output, stream, indent=2)
        print(json.dumps(output["gates"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
