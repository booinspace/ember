"""Public schema registry for independently versioned ABI/archive contracts."""

from pydantic import JsonValue, TypeAdapter
from pydantic.json_schema import models_json_schema

from .archive import (
    ArchiveWriteReceipt,
    Asset,
    BranchSelection,
    Conversation,
    Document,
    GraphNode,
    ImportCursor,
    Message,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from .common import ABI_VERSION, ARCHIVE_VERSION, ContractModel
from .errors import ErrorInfo
from .evidence import Evidence, EvidenceAssertion, EvidenceSpan, SourceRef
from .interfaces import (
    ArchiveQuery,
    ArchiveSearchResponse,
    ContextRequest,
    DisableRequest,
    ForgetRequest,
    MemoryCapabilities,
    MemoryQuery,
    MutationReceipt,
    PageRequest,
    PinRequest,
    RecordAccessRequest,
    RememberRequest,
    RetrievalContext,
    SearchResponse,
    SupersedeRequest,
    UnpinRequest,
    UpdateRequest,
)
from .memory import (
    ApprovedKnowledge,
    ArchiveChunk,
    CandidateMemory,
    Entity,
    EpisodePayload,
    FactPayload,
    GoalPayload,
    Memory,
    MemorySource,
    PinnedMemory,
    PlanPayload,
    SessionSummary,
)
from .predicates import PredicateDefinition

CONTRACT_TYPES: tuple[type[ContractModel], ...] = (
    SourceRef,
    EvidenceSpan,
    Evidence,
    EvidenceAssertion,
    Memory,
    CandidateMemory,
    ApprovedKnowledge,
    FactPayload,
    EpisodePayload,
    GoalPayload,
    PlanPayload,
    Entity,
    MemorySource,
    Conversation,
    GraphNode,
    Message,
    Document,
    Asset,
    BranchSelection,
    ArchiveChunk,
    SessionSummary,
    PinnedMemory,
    MemoryQuery,
    ArchiveQuery,
    SearchResponse,
    ArchiveSearchResponse,
    RetrievalContext,
    ContextRequest,
    MemoryCapabilities,
    ErrorInfo,
    RememberRequest,
    UpdateRequest,
    SupersedeRequest,
    DisableRequest,
    ForgetRequest,
    PinRequest,
    UnpinRequest,
    RecordAccessRequest,
    MutationReceipt,
    PageRequest,
    NormalizedArchiveBatch,
    ArchiveWriteReceipt,
    ImportCursor,
    ProcessingSpecRef,
    PredicateDefinition,
)


def contract_schemas() -> dict[str, dict[str, JsonValue]]:
    """Return draft-2020-12 schemas without touching files or importing any backend.

    The ABI schema is a definitions catalog: select the appropriate $defs contract
    for each endpoint, rather than pretending all requests share one root shape.
    Version 0.1 contracts remain experimental until real-data/integration acceptance.
    """
    schema_adapter: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
    raw_catalog: object = models_json_schema(
        [(contract, "validation") for contract in CONTRACT_TYPES],
        title=f"Ember Memory ABI {ABI_VERSION}",
        description=(
            "Experimental backend-independent contract definitions; "
            "no engine implementation is implied."
        ),
    )[1]
    catalog = schema_adapter.validate_python(raw_catalog)
    catalog["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    catalog["$comment"] = (
        "Select an endpoint contract from $defs. Unknown extensions remain readable; "
        "unsupported writes fail."
    )
    archive = schema_adapter.validate_python(NormalizedArchiveBatch.model_json_schema())
    archive["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    predicate_adapter: TypeAdapter[tuple[PredicateDefinition, ...]] = TypeAdapter(
        tuple[PredicateDefinition, ...]
    )
    predicates = schema_adapter.validate_python(predicate_adapter.json_schema())
    predicates["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return {
        f"memory-abi-{ABI_VERSION}.schema.json": catalog,
        f"normalized-archive-{ARCHIVE_VERSION}.schema.json": archive,
        "predicate-definitions-0.1.0.schema.json": predicates,
    }
