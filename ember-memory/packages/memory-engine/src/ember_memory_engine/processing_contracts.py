"""Provider-independent processing contracts; model claims never carry write authority."""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from ember_memory_abi.archive import ProcessingSpecRef
from ember_memory_abi.common import CanonicalId, Confidence, ContractModel, Sha256, UtcTimestamp
from ember_memory_abi.evidence import SourceRef
from ember_memory_abi.memory import CandidateMemory, EntityRef, ValidationVerdict

from .activation_spec import POLICY
from .processing_prompt import INSTRUCTIONS


class ProcessingError(RuntimeError):
    """Safe processing failure code; never include prompts, model output or private paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Memory processing: " + code + ".")


class ExtractionSpec(ContractModel):
    """Pinned processing identity; changed model/prompt/policy requires an explicit new job."""

    model: str
    model_digest: Sha256
    extractor: Literal[
        "ember.atomic-claims@0.1.0",
        "ember.atomic-claims@0.1.1",
        "ember.atomic-claims@0.1.2",
        "ember.atomic-claims@0.2.0",
        "ember.atomic-claims@0.3.0",
    ] = "ember.atomic-claims@0.1.2"
    policy: Literal["activation.conservative@0.1.0"] = "activation.conservative@0.1.0"
    policy_sha256: Sha256 = POLICY.reference().specification_sha256
    prompt_sha256: Sha256 = sha256(INSTRUCTIONS.encode()).hexdigest()
    output_schema_sha256: Sha256 = Field(default_factory=lambda: _schema_digest())
    temporal_timezone: str | None = Field(default=None, exclude_if=lambda value: value is None)
    window_bytes: Annotated[int, Field(ge=256, le=2400)] = 1800
    overlap_codepoints: Annotated[int, Field(ge=0, le=128)] = 64
    workload: Literal["P3", "P4"] = "P4"
    output_tokens: Annotated[int, Field(ge=128, le=1192)] = 1192

    def reference(self) -> ProcessingSpecRef:
        """Canonical specification identity, independent of local provider paths."""
        from ember_memory_abi.common import canonical_object_sha256

        return ProcessingSpecRef(
            name=self.extractor.split("@")[0],
            version=self.extractor.partition("@")[2],
            specification_sha256=canonical_object_sha256(self),
        )


class WindowRef(ContractModel):
    """Immutable input coordinates/hash; no duplicated source body or generated reasoning."""

    source: SourceRef
    source_sha256: Sha256
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]
    text_sha256: Sha256
    role: str
    authority: Literal["direct_user", "assistant", "summary", "observation"]
    branch: Literal["selected", "alternative", "ambiguous", "not_applicable"]
    observed_at: UtcTimestamp | None = None


class ModelWindow(ContractModel):
    """Ephemeral exact text sent to the injected model, never checkpointed as a prompt."""

    reference: WindowRef
    text: str = Field(repr=False)


class ClaimBase(ContractModel):
    """Shared untrusted claim text and support metadata; no write authority."""

    statement: Annotated[str, Field(min_length=1, max_length=400)] = Field(repr=False)
    quote: Annotated[str, Field(min_length=1, max_length=500)] = Field(repr=False)
    quote_start: Annotated[int, Field(ge=0)] | None
    modality: Literal[
        "FACTUAL_ASSERTION",
        "BELIEF",
        "PREFERENCE",
        "GOAL",
        "PLAN",
        "INTENTION",
        "UNCERTAINTY",
        "HYPOTHETICAL",
        "PAST_STATE",
        "NEGATED_ASSERTION",
        "QUOTED_THIRD_PARTY_CLAIM",
    ]
    polarity: Literal["positive", "negative"] = "positive"
    steps: Annotated[tuple[str, ...], Field(max_length=5)] = ()
    confidence: Confidence
    importance: Confidence
    verdict: Literal["supported", "uncertain", "contradicted"]


class FactClaim(ClaimBase):
    """Fact-shaped output must name a supported predicate and a compatible value."""

    kind: Literal["fact"]
    predicate: Annotated[str, Field(min_length=1, max_length=120)]
    value: Annotated[str, Field(min_length=1, max_length=200)] | None
    entity_name: Annotated[str, Field(min_length=1, max_length=120)] | None

    @model_validator(mode="after")
    def validate_value_shape(self) -> FactClaim:
        """Require a scalar for known predicates or a quoted entity for person.knows."""
        if self.predicate == "person.knows":
            if not self.entity_name or self.value is not None:
                raise ValueError("person.knows requires entity_name and a null value")
        elif not self.value or self.entity_name is not None:
            raise ValueError("known scalar predicates require value and a null entity_name")
        return self


class OtherClaim(ClaimBase):
    """Episode, goal and plan output cannot carry fact-only fields."""

    kind: Literal["episode", "goal", "plan"]
    predicate: None
    value: None
    entity_name: None


type AtomicClaim = Annotated[FactClaim | OtherClaim, Field(discriminator="kind")]


class ClaimBatch(ContractModel):
    """Complete structured model output; at most four claims per bounded window."""

    claims: Annotated[tuple[AtomicClaim, ...], Field(max_length=4)]


class DateSupport(ContractModel):
    """Model supplies an exact date expression and quote, never authoritative timestamps."""

    expression: Annotated[str, Field(min_length=1, max_length=100)]
    quote: Annotated[str, Field(min_length=1, max_length=500)] = Field(repr=False)
    quote_start: Annotated[int, Field(ge=0)] | None = None
    status: Literal["completed", "current_state", "planned", "hypothetical"]


class DatedFactClaim(FactClaim):
    """New extraction profile adds optional exact date support without changing old schemas."""

    date: DateSupport | None = None


class DatedOtherClaim(OtherClaim):
    """An event occurrence remains different from a proposed future action."""

    date: DateSupport | None = None


class DatedClaimBatch(ContractModel):
    """Versioned temporal output; old jobs retain their original ClaimBatch contract."""

    claims: Annotated[
        tuple[Annotated[DatedFactClaim | DatedOtherClaim, Field(discriminator="kind")], ...],
        Field(max_length=4),
    ]


type ClaimOutput = ClaimBatch | DatedClaimBatch


def batch_type_for(extractor: str) -> type[ClaimBatch] | type[DatedClaimBatch]:
    """Select the immutable schema bound to each extraction version."""
    return DatedClaimBatch if extractor == "ember.atomic-claims@0.3.0" else ClaimBatch


class ExtractionModel(Protocol):
    """Injected model transport, with one immutable request context per source window."""

    @property
    def specification(self) -> ExtractionSpec: ...
    async def extract(self, window: ModelWindow) -> ClaimOutput: ...


class JobRequest(ContractModel):
    """Trusted source selection; no model-supplied job/library/character authority."""

    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    owner_store_id: CanonicalId
    specification: ExtractionSpec
    source_kind: Literal["summary", "archive", "native", "all"] = "summary"
    source_references: Annotated[
        tuple[SourceRef, ...],
        Field(max_length=64, exclude_if=lambda value: not value),
    ] = ()

    @model_validator(mode="after")
    def source_selection(self) -> JobRequest:
        """Exact incremental snapshots stay within the trusted owner and native profile."""
        if self.source_references and (
            self.source_kind != "native"
            or any(
                ref.source_store_id != self.owner_store_id
                or ref.record_kind != "message"
                or ref.part_id is not None
                for ref in self.source_references
            )
        ):
            raise ValueError(
                "Incremental native extraction requires owned whole-message references."
            )
        return self


class JobDefinition(ContractModel):
    """Snapshot watermark and stable subject; model/policy version cannot drift on resume."""

    job_id: CanonicalId
    request: JobRequest
    subject: EntityRef
    canonical_clock_us: int
    created_at: UtcTimestamp


class FailedStep(ContractModel):
    """Safe resumable-step diagnostic without private source/model content."""

    ordinal: Annotated[int, Field(ge=0)]
    attempts: Annotated[int, Field(ge=1)]
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class JobStatus(ContractModel):
    """Aggregate diagnostics; completion means every selected window was validated/committed."""

    definition: JobDefinition
    status: Literal["planning", "ready", "running", "paused", "failed", "complete", "cancelled"]
    total_windows: int
    completed_windows: int
    failed_windows: int
    candidates: int
    failures: Annotated[tuple[FailedStep, ...], Field(max_length=16)] = ()
    # Per-invocation outcome, not persisted job state or extraction identity.
    run_outcome: Literal["finished_with_failures", "stopped_on_error"] | None = None


class PolicyInputs(ContractModel):
    """Host-established decision inputs and exact original model proposal, not model authority."""

    proposal: AtomicClaim
    source: WindowRef
    confidence: Confidence
    evidence_validated: bool
    ambiguity: bool
    duplicate: bool = False
    conflict: bool = False
    rejected_previously: bool = False
    supported_semantics: bool
    corroborating_sources: Annotated[int, Field(ge=0)] = 0


class PolicyDecision(ContractModel):
    """Versioned verdict plus reproducible inputs, retained independently of processing jobs."""

    inputs: PolicyInputs
    verdict: ValidationVerdict


class ReviewEntry(ContractModel):
    """Operator edits only decision; approved payload/evidence stay immutable and hash-checked."""

    candidate: CandidateMemory
    candidate_sha256: Sha256
    quotes: tuple[str, ...] = Field(repr=False)
    policy_decision: PolicyDecision
    decision: Literal["pending", "approve", "reject"] = "pending"


class ReviewFile(ContractModel):
    """Explicit private review export; no acceptance is inferred from model-generated flags."""

    version: Literal["ember.candidate-review@0.1.0"] = "ember.candidate-review@0.1.0"
    job_id: CanonicalId
    entries: Annotated[tuple[ReviewEntry, ...], Field(max_length=1000)]
    next_offset: int | None = None


class ReviewReceipt(ContractModel):
    """Aggregate applied decisions and exact approved knowledge selections for later seeding."""

    approved: tuple[CanonicalId, ...] = ()
    rejected: tuple[CanonicalId, ...] = ()


def _schema_digest() -> str:
    from ember_memory_abi.common import canonical_object_sha256

    return canonical_object_sha256(ClaimBatch.model_json_schema())


def native_specification(
    model: str, digest: str, *, workload: Literal["P3", "P4"] = "P4"
) -> ExtractionSpec:
    """Create a distinct immutable native profile without changing legacy defaults/hashes."""
    from .processing_prompt import NATIVE_INSTRUCTIONS

    return ExtractionSpec(
        model=model,
        model_digest=digest,
        extractor="ember.atomic-claims@0.2.0",
        prompt_sha256=sha256(NATIVE_INSTRUCTIONS.encode()).hexdigest(),
        workload=workload,
    )


def temporal_specification(
    model: str,
    digest: str,
    *,
    timezone: str = "Europe/London",
    workload: Literal["P3", "P4"] = "P4",
) -> ExtractionSpec:
    """Opt-in new job identity; legacy archive jobs and schemas remain unchanged."""
    from ember_memory_abi.common import canonical_object_sha256

    from .processing_prompt import TEMPORAL_INSTRUCTIONS

    return ExtractionSpec(
        model=model,
        model_digest=digest,
        extractor="ember.atomic-claims@0.3.0",
        temporal_timezone=timezone,
        workload=workload,
        prompt_sha256=sha256(TEMPORAL_INSTRUCTIONS.encode()).hexdigest(),
        output_schema_sha256=canonical_object_sha256(DatedClaimBatch.model_json_schema()),
    )
