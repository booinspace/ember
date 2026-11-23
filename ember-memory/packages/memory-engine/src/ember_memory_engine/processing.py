"""Durable resumable extraction; completed window/candidates/checkpoint commit atomically."""

import asyncio
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from functools import partial
from hashlib import sha256
from uuid import UUID, uuid4

from ember_memory_abi.archive import Document, Message
from ember_memory_abi.common import CanonicalId, canonical_object_sha256, timestamp_from_datetime
from ember_memory_abi.errors import InvalidPredicateError, MemoryError, UnsupportedWriteError
from ember_memory_abi.evidence import Evidence, EvidenceAssertion, make_evidence_span
from ember_memory_abi.memory import (
    CandidateMemory,
    Entity,
    EntityRef,
    EntityValue,
    EpisodePayload,
    FactPayload,
    FactValue,
    GoalPayload,
    MemoryContent,
    MemoryDraft,
    MemoryPayload,
    PlanPayload,
    ProcessingProvenance,
    StringValue,
)
from ember_memory_abi.predicates import (
    initial_predicates,
    phase_seven_predicates,
    validate_memory_write,
)
from ember_memory_abi.temporal import TemporalBounds

from .activation import evaluate
from .activation_spec import POLICY
from .archive import source_text
from .authoring import ensure_subject
from .database import load_model, model_bytes, payload_hash, row_int, row_text
from .engine import CanonicalEngine
from .processing_contracts import (
    AtomicClaim,
    ClaimOutput,
    DatedFactClaim,
    DatedOtherClaim,
    ExtractionModel,
    FailedStep,
    JobDefinition,
    JobRequest,
    JobStatus,
    ModelWindow,
    PolicyInputs,
    ProcessingDeferred,
    ProcessingError,
    WindowRef,
    batch_type_for,
)
from .processing_windows import resolve_window, windows
from .source_access import source_keys

_WINDOW_OUTPUT_FAILURES = frozenset(
    {
        "quote_missing_or_ambiguous",
        "quote_not_exact",
        "date_quote_missing_or_ambiguous",
        "date_expression_not_in_quote",
        "date_passage_not_linked_to_claim",
        "unsupported_completed_event",
        "fact_value_missing",
        "entity_mention_unverified",
        "model_step_invalid",
        "gateway_structured_output_invalid",
        "extraction_output_too_large",
        "unexpected_extraction_tool_output",
    }
)
_WINDOW_SOURCE_FAILURES = frozenset({"source_suppressed", "unsupported_source_window"})


def _subject(engine: CanonicalEngine, owner: CanonicalId) -> EntityRef:
    info, _ = engine.maintenance_store(owner)
    if info.kind == "character":
        return ensure_subject(engine, owner)
    identity = UUID(
        bytes=sha256((str(owner) + ":extraction-subject-v1").encode()).digest()[:16], version=4
    )
    reference = EntityRef(store_id=owner, entity_id=identity)
    engine.put_owned_entity(owner, Entity(reference=reference, revision=1, name="User"))
    return reference


def fingerprint(claim: AtomicClaim, subject: EntityRef) -> str:
    """Exact normalized proposal identity; similarity does not merge events or resolve conflicts."""
    return canonical_object_sha256(
        {
            "subject": subject.model_dump(mode="json"),
            "kind": claim.kind,
            "statement": claim.statement.casefold() if claim.kind != "fact" else None,
            "predicate": claim.predicate,
            "value": claim.value.casefold() if claim.value else None,
            "entity_name": claim.entity_name.casefold() if claim.entity_name else None,
            "modality": claim.modality,
            "polarity": claim.polarity,
        }
    )


class ProcessingService:
    """Trusted store-bound maintenance; no model-supplied owner or activation authority."""

    def __init__(self, engine: CanonicalEngine, owner: CanonicalId) -> None:
        self.engine = engine
        self.owner = owner
        self.info, self.database = engine.maintenance_store(owner)

    def create(self, request: JobRequest) -> JobStatus:
        """Persist/replay a pinned source snapshot, then plan bounded metadata batches."""
        request = JobRequest.model_validate_json(request.model_dump_json())
        if request.specification.extractor not in {
            "ember.atomic-claims@0.1.2",
            "ember.atomic-claims@0.2.0",
            "ember.atomic-claims@0.3.0",
            "ember.atomic-claims@0.3.1",
        }:
            raise ProcessingError("processing_extractor_changed")
        if request.specification.extractor == "ember.atomic-claims@0.2.0" and (
            self.info.kind != "character" and request.source_kind == "native"
        ):
            raise ProcessingError("native_profile_requires_character_sources")
        if request.specification.extractor == "ember.atomic-claims@0.3.1" and (
            self.info.kind != "character" or request.source_kind != "native"
        ):
            raise ProcessingError("native_profile_requires_character_sources")
        from .processing_prompt import instructions_for

        if request.specification.prompt_sha256 != sha256(
            instructions_for(request.specification.extractor).encode()
        ).hexdigest() or request.specification.output_schema_sha256 != canonical_object_sha256(
            batch_type_for(request.specification.extractor).model_json_schema()
        ):
            raise ProcessingError("processing_prompt_or_schema_changed")
        if request.owner_store_id != self.owner:
            raise ProcessingError("job_owner_mismatch")
        if request.specification.policy_sha256 != POLICY.reference().specification_sha256:
            raise ProcessingError("processing_policy_changed")
        if request.specification.extractor in {
            "ember.atomic-claims@0.3.0",
            "ember.atomic-claims@0.3.1",
        }:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                if request.specification.temporal_timezone is None:
                    raise ValueError("Missing temporal timezone.")
                ZoneInfo(request.specification.temporal_timezone)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise ProcessingError("temporal_timezone_unavailable") from error
        digest = canonical_object_sha256(request)
        existing = self.database.read(
            lambda connection: connection.execute(
                "SELECT * FROM processing_jobs WHERE idempotency_key=?", (request.idempotency_key,)
            ).fetchone()
        )
        if existing is not None:
            if row_text(existing, "request_sha256") != digest:
                raise ProcessingError("job_key_conflict")
            definition = load_model(existing, JobDefinition)
        else:
            subject = _subject(self.engine, self.owner)
            watermark = self.database.read(
                lambda connection: row_int(
                    connection.execute("SELECT recorded_clock_us FROM store_metadata").fetchone(),
                    "recorded_clock_us",
                )
            )
            definition = JobDefinition(
                job_id=uuid4(),
                request=request,
                subject=subject,
                canonical_clock_us=watermark,
                created_at=timestamp_from_datetime(self.engine.clock.now()),
            )

            def write(connection: sqlite3.Connection) -> None:
                with self.database.transaction(connection):
                    policy_payload = model_bytes(POLICY)
                    connection.execute(
                        "INSERT OR IGNORE INTO processing_specs VALUES(?,?,?)",
                        (
                            POLICY.reference().specification_sha256,
                            policy_payload,
                            payload_hash(policy_payload),
                        ),
                    )
                    spec_payload = model_bytes(request.specification)
                    connection.execute(
                        "INSERT OR IGNORE INTO processing_specs VALUES(?,?,?)",
                        (
                            request.specification.reference().specification_sha256,
                            spec_payload,
                            payload_hash(spec_payload),
                        ),
                    )
                    payload = model_bytes(definition)
                    connection.execute(
                        "INSERT INTO processing_jobs VALUES(?,?,?,?,?,'planning',0)",
                        (
                            str(definition.job_id),
                            request.idempotency_key,
                            digest,
                            payload,
                            payload_hash(payload),
                        ),
                    )

            self.database.write(write)
        status = self.status(definition.job_id)
        if status.status == "planning":
            self._plan(definition)
        return self.status(definition.job_id)

    def _plan(self, definition: JobDefinition) -> None:
        with closing(sqlite3.connect(self.database.path.as_uri() + "?mode=ro", uri=True)) as source:
            source.row_factory = sqlite3.Row
            batch: list[tuple[int, WindowRef]] = []

            def commit() -> None:
                entries = tuple(batch)

                def write(connection: sqlite3.Connection) -> None:
                    with self.database.transaction(connection):
                        for ordinal, reference in entries:
                            payload = model_bytes(reference)
                            connection.execute(
                                "INSERT OR IGNORE INTO processing_windows "
                                "VALUES(?,?,?,?,?,'pending',0,NULL)",
                                (
                                    str(definition.job_id),
                                    ordinal,
                                    canonical_object_sha256(reference),
                                    payload,
                                    payload_hash(payload),
                                ),
                            )

                self.database.write(write)
                batch.clear()

            for ordinal, reference in enumerate(windows(source, definition)):
                batch.append((ordinal, reference))
                if len(batch) >= 128:
                    commit()
            if batch:
                commit()
        self.database.write(
            lambda connection: connection.execute(
                "UPDATE processing_jobs SET status='ready' WHERE job_id=?",
                (str(definition.job_id),),
            )
        )

    def definition(self, job_id: CanonicalId) -> JobDefinition:
        """Read a verified immutable job definition, rejecting another store's job ID."""

        def read(connection: sqlite3.Connection) -> JobDefinition:
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE job_id=?", (str(job_id),)
            ).fetchone()
            if row is None:
                raise ProcessingError("job_not_found")
            return load_model(row, JobDefinition)

        return self.database.read(read)

    def definitions(self, *, offset: int = 0, limit: int = 20) -> tuple[JobDefinition, ...]:
        """List a bounded newest-first page of verified jobs owned by this store."""
        if offset < 0 or not 1 <= limit <= 100:
            raise ProcessingError("processing_job_page_invalid")
        return self.database.read(
            lambda connection: tuple(
                load_model(row, JobDefinition)
                for row in connection.execute(
                    "SELECT * FROM processing_jobs ORDER BY rowid DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            )
        )

    def status(self, job_id: CanonicalId) -> JobStatus:
        """Return aggregate progress, never model/source text or exception payloads."""
        definition = self.definition(job_id)

        def read(connection: sqlite3.Connection) -> JobStatus:
            row = connection.execute(
                "SELECT * FROM processing_jobs WHERE job_id=?", (str(job_id),)
            ).fetchone()
            counts = connection.execute(
                "SELECT COUNT(*) AS total,SUM(status='complete') AS complete,"
                "SUM(status='failed') AS failed FROM processing_windows WHERE job_id=?",
                (str(job_id),),
            ).fetchone()
            candidates = row_int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM processing_candidates WHERE job_id=?",
                    (str(job_id),),
                ).fetchone(),
                "total",
            )
            return JobStatus.model_validate(
                {
                    "definition": definition,
                    "status": row_text(row, "status"),
                    "total_windows": row_int(counts, "total"),
                    "completed_windows": counts["complete"] or 0,
                    "failed_windows": counts["failed"] or 0,
                    "candidates": candidates,
                    "failures": tuple(
                        FailedStep(
                            ordinal=row_int(step, "ordinal"),
                            attempts=row_int(step, "attempts"),
                            code=row_text(step, "error_code"),
                        )
                        for step in connection.execute(
                            "SELECT ordinal,attempts,error_code FROM processing_windows "
                            "WHERE job_id=? AND status='failed' ORDER BY ordinal LIMIT 16",
                            (str(job_id),),
                        ).fetchall()
                    ),
                }
            )

        return self.database.read(read)

    def cancel(self, job_id: CanonicalId) -> JobStatus:
        """Cancel pending maintenance; already running pure model steps are never claimed undone."""
        self.definition(job_id)
        self.database.write(
            lambda connection: connection.execute(
                "UPDATE processing_jobs SET status='cancelled' WHERE job_id=?", (str(job_id),)
            )
        )
        return self.status(job_id)

    def _input(self, definition: JobDefinition, reference: WindowRef) -> ModelWindow:
        record = self.engine.resolve(reference.source)
        if not isinstance(record, (Message, Document)):
            raise ProcessingError("unsupported_source_window")
        part_text = source_text(record, reference.source)

        def check(connection: sqlite3.Connection) -> None:
            keys = source_keys(reference.source)
            if connection.execute(
                "SELECT 1 FROM suppression_targets WHERE target_kind='source' AND "
                "target_key IN (?,?)",
                keys,
            ).fetchone():
                raise ProcessingError("source_suppressed")

        self.database.read(check)
        return resolve_window(part_text, reference)

    def _draft(
        self, definition: JobDefinition, window: ModelWindow, claim: AtomicClaim
    ) -> tuple[MemoryDraft, Entity | None]:
        reference = window.reference
        # Models are poor offset calculators. A unique literal quote determines
        # its own exact position; repeated quotes require a verified supplied hint.
        first = window.text.find(claim.quote)
        unique = first >= 0 and window.text.find(claim.quote, first + 1) < 0
        position = first if unique else claim.quote_start
        if position is None or position < 0:
            raise ProcessingError("quote_missing_or_ambiguous")
        if window.text[position : position + len(claim.quote)] != claim.quote:
            raise ProcessingError("quote_not_exact")
        original_record = self.engine.resolve(reference.source)
        original = source_text(original_record, reference.source)
        span = make_evidence_span(
            original, reference.start + position, reference.start + position + len(claim.quote)
        )
        evidence = Evidence(evidence_id=uuid4(), source=reference.source, span=span)
        self.engine.validate(evidence)
        assertions = [
            EvidenceAssertion(
                evidence=evidence,
                relation="contradicts" if claim.verdict == "contradicted" else "supports",
                modality=claim.modality,
                authority="source_" + reference.authority,
                verdict="model_" + claim.verdict,
            )
        ]
        temporal = TemporalBounds(
            observed_at=reference.observed_at, basis="source_observation_not_validity"
        )
        if isinstance(claim, (DatedFactClaim, DatedOtherClaim)) and claim.date is not None:
            from .event_dates import interpretation_date, supported_position

            support = claim.date
            if claim.quote not in support.quote:
                raise ProcessingError("date_passage_not_linked_to_claim")
            position = supported_position(window.text, support.quote, support.quote_start)
            if claim.kind == "episode" and (
                support.status != "completed"
                or reference.authority != "direct_user"
                or claim.modality in {"PLAN", "GOAL", "INTENTION", "HYPOTHETICAL", "UNCERTAINTY"}
                or re.search(r"\b(will|would|planning|hope|if)\b", support.quote.casefold())
            ):
                raise ProcessingError("unsupported_completed_event")
            date_evidence = Evidence(
                evidence_id=uuid4(),
                source=reference.source,
                span=make_evidence_span(
                    original,
                    reference.start + position,
                    reference.start + position + len(support.quote),
                ),
            )
            self.engine.validate(date_evidence)
            assertions.append(
                EvidenceAssertion(
                    evidence=date_evidence,
                    relation="supports",
                    modality=claim.modality,
                    authority="source_" + reference.authority,
                    verdict="exact_date_passage",
                )
            )
            timezone = definition.request.specification.temporal_timezone
            if timezone is None:
                raise ProcessingError("temporal_timezone_missing")
            if not isinstance(original_record, (Message, Document)):
                raise ProcessingError("unsupported_source_window")
            temporal = interpretation_date(
                support,
                claim.kind,
                reference.observed_at,
                timezone,
                observed_precision=original_record.created_time.precision,
            )
            if (
                claim.kind == "episode"
                and temporal.valid_from is not None
                and reference.observed_at is not None
                and temporal.valid_from > reference.observed_at
            ):
                raise ProcessingError("unsupported_completed_event")
        entity = None
        value: FactValue
        payload: MemoryPayload
        if claim.kind == "fact":
            if not claim.predicate or (claim.value is None and claim.entity_name is None):
                raise ProcessingError("fact_value_missing")
            if claim.entity_name is not None:
                if claim.entity_name not in claim.quote:
                    raise ProcessingError("entity_mention_unverified")
                entity = Entity(
                    reference=EntityRef(store_id=self.owner, entity_id=uuid4()),
                    revision=1,
                    name=claim.entity_name,
                )
                value = EntityValue(value=entity.reference)
            else:
                value = StringValue(value=claim.value or "")
            payload = FactPayload(
                subject=definition.subject,
                predicate=claim.predicate,
                original_predicate=claim.predicate,
                definition_version="0.1.0",
                value=value,
                polarity=claim.polarity,
            )
        elif claim.kind == "episode":
            payload = EpisodePayload(event=claim.statement, participants=(definition.subject,))
        elif claim.kind == "goal":
            payload = GoalPayload(description=claim.statement)
        else:
            payload = PlanPayload(description=claim.statement, steps=claim.steps)
        confidence = (
            min(claim.confidence, 0.79) if reference.authority == "summary" else claim.confidence
        )
        draft = MemoryDraft(
            content=MemoryContent(
                kind=claim.kind,
                payload=payload,
                modality=claim.modality,
                confidence=confidence,
                importance=claim.importance,
                evidence=tuple(assertions),
                provenance=(
                    ProcessingProvenance(
                        run_id=definition.job_id,
                        specification=definition.request.specification.reference(),
                        processed_at=timestamp_from_datetime(self.engine.clock.now()),
                        input_sources=(reference.source,),
                    ),
                ),
            ),
            temporal=temporal,
        )
        return draft, entity

    def _finish(
        self, definition: JobDefinition, ordinal: int, window: ModelWindow, batch: ClaimOutput
    ) -> None:
        # Resolve/validate exact originals before entering the short commit transaction.
        prepared = tuple((claim, *self._draft(definition, window, claim)) for claim in batch.claims)
        self._input(definition, window.reference)

        def write(connection: sqlite3.Connection) -> None:
            with self.database.transaction(connection):
                state = row_text(
                    connection.execute(
                        "SELECT status FROM processing_jobs WHERE job_id=?",
                        (str(definition.job_id),),
                    ).fetchone(),
                    "status",
                )
                if state == "cancelled":
                    raise ProcessingError("job_cancelled")
                for claim, draft, entity in prepared:
                    if entity is not None:
                        payload = model_bytes(entity)
                        connection.execute(
                            "INSERT INTO entities VALUES(?,1,?,?)",
                            (str(entity.reference.entity_id), payload, payload_hash(payload)),
                        )
                        connection.execute(
                            "INSERT INTO entity_aliases VALUES(?,?)",
                            (str(entity.reference.entity_id), entity.name),
                        )
                    supported = True
                    try:
                        validate_memory_write(
                            draft,
                            predicates=(
                                *initial_predicates(),
                                *(
                                    phase_seven_predicates()
                                    if definition.request.specification.extractor
                                    in {"ember.atomic-claims@0.2.0", "ember.atomic-claims@0.3.1"}
                                    else ()
                                ),
                            ),
                        )
                    except (UnsupportedWriteError, InvalidPredicateError):
                        supported = False
                    key = fingerprint(claim, definition.subject)
                    if isinstance(claim, (DatedFactClaim, DatedOtherClaim)):
                        key = canonical_object_sha256(
                            {
                                "claim": key,
                                "temporal": draft.temporal.model_dump(mode="json"),
                            }
                        )
                    duplicate = (
                        connection.execute(
                            "SELECT 1 FROM processing_candidates WHERE fingerprint=? LIMIT 1",
                            (key,),
                        ).fetchone()
                        is not None
                    )
                    rejected = (
                        connection.execute(
                            "SELECT 1 FROM review_decisions WHERE fingerprint=? AND "
                            "decision='reject' LIMIT 1",
                            (key,),
                        ).fetchone()
                        is not None
                    )
                    conflict_key = None
                    value_key = None
                    if isinstance(draft.content.payload, FactPayload) and any(
                        d.name == draft.content.payload.predicate and d.cardinality == "one"
                        for d in initial_predicates()
                    ):
                        conflict_key = canonical_object_sha256(
                            {
                                "subject": definition.subject.model_dump(mode="json"),
                                "predicate": claim.predicate,
                            }
                        )
                        value_key = canonical_object_sha256(
                            {
                                "value": claim.value,
                                "entity": claim.entity_name,
                                "polarity": claim.polarity,
                            }
                        )
                    conflict = (
                        conflict_key is not None
                        and connection.execute(
                            "SELECT 1 FROM processing_candidates WHERE conflict_key=? AND "
                            "value_key<>? LIMIT 1",
                            (conflict_key, value_key),
                        ).fetchone()
                        is not None
                    )
                    inputs = PolicyInputs(
                        proposal=claim,
                        source=window.reference,
                        confidence=draft.content.confidence,
                        evidence_validated=True,
                        ambiguity=window.reference.branch in {"alternative", "ambiguous"},
                        duplicate=duplicate,
                        conflict=conflict,
                        rejected_previously=rejected,
                        supported_semantics=supported,
                    )
                    decision = evaluate(
                        inputs, evaluated_at=timestamp_from_datetime(self.engine.clock.now())
                    )
                    candidate = CandidateMemory(
                        candidate_id=uuid4(),
                        owner_store_id=self.owner,
                        revision=1,
                        draft=draft,
                        recorded_at=decision.verdict.evaluated_at,
                        verdicts=(decision.verdict,),
                    )
                    payload = model_bytes(candidate)
                    connection.execute(
                        "INSERT INTO candidates VALUES(?,1,?,?)",
                        (str(candidate.candidate_id), payload, payload_hash(payload)),
                    )
                    connection.execute(
                        "INSERT INTO processing_candidates VALUES(?,?,?,1,?,?,?)",
                        (
                            str(definition.job_id),
                            ordinal,
                            str(candidate.candidate_id),
                            key,
                            conflict_key,
                            value_key,
                        ),
                    )
                    payload = model_bytes(decision)
                    connection.execute(
                        "INSERT INTO activation_decisions VALUES(?,?,1,?,?)",
                        (str(uuid4()), str(candidate.candidate_id), payload, payload_hash(payload)),
                    )
                connection.execute(
                    "UPDATE processing_windows SET status='complete',error_code=NULL WHERE "
                    "job_id=? AND ordinal=?",
                    (str(definition.job_id), ordinal),
                )
                connection.execute(
                    "UPDATE processing_jobs SET completed_windows=completed_windows+1 WHERE "
                    "job_id=?",
                    (str(definition.job_id),),
                )

        self.database.write(write)

    def _set_status(self, job_id: CanonicalId, status: str) -> None:
        self.database.write(
            lambda connection: connection.execute(
                "UPDATE processing_jobs SET status=? WHERE job_id=? AND status<>'cancelled'",
                (status, str(job_id)),
            )
        )

    def _resume(self, job_id: CanonicalId, retry_failed: bool) -> None:
        def write(connection: sqlite3.Connection) -> None:
            with self.database.transaction(connection):
                connection.execute(
                    "UPDATE processing_windows SET status='pending' "
                    "WHERE job_id=? AND status='running'",
                    (str(job_id),),
                )
                if retry_failed:
                    connection.execute(
                        "UPDATE processing_windows SET status='pending',error_code=NULL "
                        "WHERE job_id=? AND status='failed'",
                        (str(job_id),),
                    )
                connection.execute(
                    "UPDATE processing_jobs SET status='running' WHERE job_id=?", (str(job_id),)
                )

        self.database.write(write)

    def _next(self, job_id: CanonicalId) -> sqlite3.Row | None:
        return self.database.read(
            lambda connection: connection.execute(
                "SELECT * FROM processing_windows WHERE job_id=? AND status='pending' "
                "ORDER BY ordinal LIMIT 1",
                (str(job_id),),
            ).fetchone()
        )

    def _mark_running(self, job_id: CanonicalId, ordinal: int) -> None:
        self.database.write(
            lambda connection: connection.execute(
                "UPDATE processing_windows SET status='running',attempts=attempts+1 "
                "WHERE job_id=? AND ordinal=?",
                (str(job_id), ordinal),
            )
        )

    def _pause_window(self, job_id: CanonicalId, ordinal: int) -> None:
        def write(connection: sqlite3.Connection) -> None:
            with self.database.transaction(connection):
                connection.execute(
                    "UPDATE processing_windows SET status='pending',attempts=attempts-1,"
                    "error_code=NULL WHERE job_id=? AND ordinal=? AND status='running'",
                    (str(job_id), ordinal),
                )
                connection.execute(
                    "UPDATE processing_jobs SET status='paused' "
                    "WHERE job_id=? AND status<>'cancelled'",
                    (str(job_id),),
                )

        self.database.write(write)

    def _failure(self, job_id: CanonicalId, ordinal: int, attempt: int, code: str) -> None:
        def write(connection: sqlite3.Connection) -> None:
            with self.database.transaction(connection):
                connection.execute(
                    "UPDATE processing_windows SET status='failed',error_code=? "
                    "WHERE job_id=? AND ordinal=?",
                    (code, str(job_id), ordinal),
                )
                connection.execute(
                    "INSERT INTO processing_failures VALUES(?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        str(job_id),
                        ordinal,
                        attempt,
                        code,
                        timestamp_from_datetime(self.engine.clock.now()),
                    ),
                )
                connection.execute(
                    "UPDATE processing_jobs SET status='failed' "
                    "WHERE job_id=? AND status<>'cancelled'",
                    (str(job_id),),
                )

        self.database.write(write)

    async def run(
        self,
        job_id: CanonicalId,
        model: ExtractionModel,
        *,
        max_windows: int | None = None,
        retry_failed: bool = False,
        window_attempts: int = 1,
        continue_on_error: bool = False,
        on_progress: Callable[[JobStatus], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> JobStatus:
        """Resume pure immutable steps; commit only validated complete window outputs.

        Filesystem/SQLite work stays on one owned bounded executor. Cancellation
        pauses at a durable boundary; native work keeps ownership through draining.
        Invalid model outputs can retry; continuation also skips unavailable
        window scopes. ProcessingDeferred pauses the current window and propagates
        without consuming an attempt. Unresolved gaps remain failed; run_outcome
        separates a finished batch from an early stop for global failures.
        """
        from pydantic import ValidationError

        from .workers import OwnedWorkers

        if max_windows is not None and max_windows < 1:
            raise ProcessingError("invalid_window_limit")
        if not 1 <= window_attempts <= 3:
            raise ProcessingError("invalid_window_attempts")
        release = self.engine.acquire_processing(job_id)
        workers = OwnedWorkers(1, max_pending=2, name="ember-processing-owner")

        async def offload[T](work: Callable[[], T]) -> T:
            return await asyncio.wrap_future(workers.submit(work))

        try:
            definition = await offload(partial(self.definition, job_id))
            if definition.request.specification != model.specification:
                raise ProcessingError("processing_model_specification_mismatch")
            if (
                definition.request.specification.policy_sha256
                != POLICY.reference().specification_sha256
            ):
                raise ProcessingError("processing_policy_changed")
            status = await offload(partial(self.status, job_id))
            if status.status in {"complete", "cancelled", "planning"}:
                return status
            if definition.request.specification.extractor not in {
                "ember.atomic-claims@0.1.2",
                "ember.atomic-claims@0.2.0",
                "ember.atomic-claims@0.3.0",
                "ember.atomic-claims@0.3.1",
            }:
                raise ProcessingError("processing_extractor_changed")
            await offload(partial(self._resume, job_id, retry_failed))
            processed = 0
            while max_windows is None or processed < max_windows:
                if should_continue is not None and not should_continue():
                    break
                row = await offload(partial(self._next, job_id))
                if row is None:
                    break
                reference = load_model(row, WindowRef)
                ordinal = row_int(row, "ordinal")
                for attempt in range(window_attempts):
                    await offload(partial(self._mark_running, job_id, ordinal))
                    try:
                        window = await offload(partial(self._input, definition, reference))
                        if should_continue is not None and not should_continue():
                            await offload(partial(self._pause_window, job_id, ordinal))
                            return await offload(partial(self.status, job_id))
                        try:
                            result = await model.extract(window)
                            result = batch_type_for(
                                definition.request.specification.extractor
                            ).model_validate_json(result.model_dump_json())
                        except ProcessingError:
                            raise
                        except (ValidationError, ValueError) as error:
                            raise ProcessingError("model_step_invalid") from error
                        except RuntimeError as error:
                            raise ProcessingError("model_step_failed") from error
                        await offload(partial(self._finish, definition, ordinal, window, result))
                    except ProcessingDeferred:
                        await offload(partial(self._pause_window, job_id, ordinal))
                        raise
                    except (ProcessingError, MemoryError) as error:
                        failure_code = (
                            error.code
                            if isinstance(error, ProcessingError)
                            else "canonical_processing_failed"
                        )
                        await offload(
                            partial(
                                self._failure,
                                job_id,
                                ordinal,
                                row_int(row, "attempts") + attempt + 1,
                                failure_code,
                            )
                        )
                        # Output rejection is safe to retry before commit. A
                        # suppressed/unsupported source is skipped without inference;
                        # global transport, integrity and authority failures stop.
                        retryable = failure_code in _WINDOW_OUTPUT_FAILURES
                        continuable = retryable or failure_code in _WINDOW_SOURCE_FAILURES
                        if retryable and attempt + 1 < window_attempts:
                            await offload(partial(self._set_status, job_id, "running"))
                            continue
                        if not (continuable and continue_on_error):
                            stopped = await offload(partial(self.status, job_id))
                            return (
                                stopped.model_copy(update={"run_outcome": "stopped_on_error"})
                                if continue_on_error
                                else stopped
                            )
                        await offload(partial(self._set_status, job_id, "running"))
                    break
                processed += 1
                if on_progress is not None:
                    on_progress(await offload(partial(self.status, job_id)))
            final = await offload(partial(self.status, job_id))
            target = (
                "complete"
                if final.completed_windows == final.total_windows
                else "failed"
                if final.failed_windows
                else "paused"
            )
            await offload(partial(self._set_status, job_id, target))
            finished = await offload(partial(self.status, job_id))
            return (
                finished.model_copy(update={"run_outcome": "finished_with_failures"})
                if continue_on_error and target == "failed"
                else finished
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                asyncio.wrap_future(workers.submit(partial(self._set_status, job_id, "paused")))
            )
            raise
        finally:
            try:
                await asyncio.to_thread(workers.shutdown)
            finally:
                release()
