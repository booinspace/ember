"""Conformance for conservative semantics, typed client authority, and public schema artifacts."""

import inspect
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ember_memory_abi.errors import (
    AccessDeniedError,
    DeadlineExceededError,
    InvalidEvidenceError,
    InvalidPredicateError,
    RevisionConflictError,
    SourceUnavailableError,
    UnsupportedWriteError,
)
from ember_memory_abi.interfaces import (
    ArchiveQuery,
    AsyncMemoryClient,
    DisableRequest,
    ForgetRequest,
    MemoryQuery,
    MemoryStore,
    PinRequest,
    RememberRequest,
    SupersedeRequest,
    UnpinRequest,
    UpdateRequest,
)
from ember_memory_abi.memory import MemoryContent, MemoryDraft, OpaquePayload
from ember_memory_abi.predicates import initial_predicates, validate_memory_write
from ember_memory_abi.schemas import contract_schemas

ROOT = Path(__file__).resolve().parents[4]


def _changed(draft: MemoryDraft, **changes: object) -> MemoryDraft:
    content = draft.content.model_dump()
    content.update(changes)
    return MemoryDraft(content=MemoryContent.model_validate(content))


def test_initial_predicate_cardinality_distinguishes_replacement_from_additional_ownership() -> (
    None
):
    definitions = {definition.name: definition for definition in initial_predicates()}
    assert definitions["hardware.primary_gpu"].cardinality == "one"
    assert definitions["hardware.primary_gpu"].replacement_meaningful
    assert definitions["hardware.owns_gpu"].cardinality == "many"
    assert not definitions["hardware.owns_gpu"].replacement_meaningful
    assert definitions["entity.alias"].cardinality == "many"
    assert len(definitions) == 6


def test_fact_write_accepts_supported_semantics_without_claiming_activation(
    draft: MemoryDraft,
) -> None:
    assert validate_memory_write(draft, predicates=initial_predicates()) is None
    assert draft.temporal.valid_from is None


@pytest.mark.parametrize("modality", ["INTENTION", "HYPOTHETICAL", "QUOTED_THIRD_PARTY_CLAIM"])
def test_primary_gpu_intention_or_quote_cannot_be_written_as_factual_hardware(
    draft: MemoryDraft, modality: str
) -> None:
    with pytest.raises(InvalidPredicateError):
        validate_memory_write(_changed(draft, modality=modality), predicates=initial_predicates())


def test_future_modality_is_readable_and_roundtrips_but_cannot_be_written(
    draft: MemoryDraft,
) -> None:
    future = _changed(draft, modality="FUTURE_MODALITY")
    assert MemoryDraft.model_validate_json(future.model_dump_json()) == future
    with pytest.raises(UnsupportedWriteError):
        validate_memory_write(future, predicates=initial_predicates())


def test_future_payload_kind_is_opaque_and_never_supported_automatically() -> None:
    future = MemoryDraft(
        content=MemoryContent(
            kind="future_kind",
            payload=OpaquePayload(original_kind="future_kind", data={"preserved": [1, None]}),
            modality="BELIEF",
            confidence=0.5,
            importance=0.5,
        )
    )
    assert MemoryDraft.model_validate_json(future.model_dump_json()) == future
    with pytest.raises(UnsupportedWriteError):
        validate_memory_write(future, predicates=initial_predicates())


@pytest.mark.parametrize("change", ["predicate", "version", "polarity"])
def test_predicate_unknowns_and_incompatible_definitions_are_conservative(
    draft: MemoryDraft, change: str
) -> None:
    payload = draft.content.payload.model_dump()
    payload[
        {"predicate": "predicate", "version": "definition_version", "polarity": "polarity"}[change]
    ] = {"predicate": "future.unknown", "version": "9.9.9", "polarity": "negative"}[change]
    content = _changed(draft, payload=payload)
    with pytest.raises(UnsupportedWriteError if change == "predicate" else InvalidPredicateError):
        validate_memory_write(content, predicates=initial_predicates())


def test_known_extension_payload_can_have_an_uninterpreted_extensions_key(
    draft: MemoryDraft,
) -> None:
    extended = MemoryDraft.model_validate(
        {
            **draft.model_dump(),
            "extensions": {
                "example.supported": {"extensions": {"opaque_unregistered_data_key": True}}
            },
        }
    )
    with pytest.raises(UnsupportedWriteError):
        validate_memory_write(extended, predicates=initial_predicates())
    validate_memory_write(
        extended,
        predicates=initial_predicates(),
        supported_extension_names=frozenset({"example.supported"}),
    )


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_confidence_is_bounded_finite_and_not_a_boolean(draft: MemoryDraft, score: object) -> None:
    with pytest.raises(ValidationError):
        _changed(draft, confidence=score)


@pytest.mark.parametrize("request_type", [MemoryQuery, ArchiveQuery])
def test_queries_cannot_choose_a_character_binding(request_type: type) -> None:
    with pytest.raises(ValidationError):
        request_type(character_id=uuid4())
    with pytest.raises(ValidationError):
        request_type(limit=101)


def test_remember_requires_idempotency_and_cannot_assign_character_ownership(
    draft: MemoryDraft,
) -> None:
    with pytest.raises(ValidationError):
        RememberRequest(draft=draft)
    with pytest.raises(ValidationError):
        RememberRequest(draft=draft, idempotency_key="synthetic", character_id=uuid4())
    assert RememberRequest(draft=draft, idempotency_key="synthetic").idempotency_key == "synthetic"


@pytest.mark.parametrize(
    "request_type",
    [UpdateRequest, SupersedeRequest, DisableRequest, ForgetRequest, PinRequest, UnpinRequest],
)
def test_existing_belief_mutations_require_revision_and_idempotency(request_type: type) -> None:
    required = {name for name, field in request_type.model_fields.items() if field.is_required()}
    assert {"memory_id", "expected_revision", "idempotency_key"} <= required
    assert "character_id" not in request_type.model_fields


def test_sync_and_async_client_surfaces_have_identical_typed_signatures() -> None:
    methods = {
        name
        for name, value in vars(MemoryStore).items()
        if callable(value) and not name.startswith("_")
    }
    assert methods == {
        name
        for name, value in vars(AsyncMemoryClient).items()
        if callable(value) and not name.startswith("_")
    }
    assert len(methods) == 16
    for name in methods:
        sync = getattr(MemoryStore, name)
        asynchronous = getattr(AsyncMemoryClient, name)
        assert not inspect.iscoroutinefunction(sync)
        assert inspect.iscoroutinefunction(asynchronous)
        assert inspect.signature(sync) == inspect.signature(asynchronous)


@pytest.mark.parametrize(
    "error",
    [
        AccessDeniedError,
        RevisionConflictError,
        InvalidEvidenceError,
        InvalidPredicateError,
        DeadlineExceededError,
        SourceUnavailableError,
    ],
)
def test_domain_errors_have_content_free_fixed_messages_and_typed_codes(error: type) -> None:
    failure = error()
    assert failure.info.code
    assert failure.info.details.record_id is None
    assert "PRIVATE_SENTINEL" not in str(failure)


def test_public_schema_artifacts_match_typed_contracts_and_contain_no_sample_source_data() -> None:
    for name, schema in contract_schemas().items():
        path = ROOT / "ember-memory/schemas" / name
        assert json.loads(path.read_text(encoding="utf-8")) == schema
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert "SENTINEL" not in json.dumps(schema)
    catalog = contract_schemas()["memory-abi-0.1.0.schema.json"]
    assert {"SourceRef", "EvidenceSpan", "RememberRequest", "Memory"} <= set(catalog["$defs"])
