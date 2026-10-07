"""Versioned predicates and conservative write validation; no extraction or activation."""

from collections.abc import Sequence
from typing import Literal

from pydantic import JsonValue

from .common import ContractModel, WireString, unsupported_extension_names
from .errors import InvalidPredicateError, UnsupportedWriteError
from .memory import FactPayload, MemoryDraft, OpaquePayload

PREDICATE_DEFINITION_VERSION = "0.1.0"
KNOWN_MODALITIES = frozenset(
    {
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
    }
)


class PredicateDefinition(ContractModel):
    """Independent predicate semantics, including historical/replacement behavior."""

    name: WireString
    definition_version: WireString
    value_type: WireString
    cardinality: WireString
    temporal: bool
    replacement_meaningful: bool
    normalization_rules: dict[str, JsonValue]
    aliases: tuple[WireString, ...] = ()
    deprecated_aliases: tuple[WireString, ...] = ()
    allowed_modalities: tuple[WireString, ...]


def initial_predicates() -> tuple[PredicateDefinition, ...]:
    """Return fresh 0.1 definitions; purchases/intentions never imply replacement or ownership."""
    definitions: tuple[tuple[str, str, Literal["one", "many"], bool, tuple[str, ...]], ...] = (
        ("hardware.primary_gpu", "string", "one", True, ("FACTUAL_ASSERTION", "PAST_STATE")),
        (
            "hardware.owns_gpu",
            "string",
            "many",
            False,
            ("FACTUAL_ASSERTION", "PAST_STATE", "NEGATED_ASSERTION"),
        ),
        ("music.likes_genre", "string", "many", False, ("PREFERENCE", "NEGATED_ASSERTION")),
        (
            "person.knows",
            "entity",
            "many",
            False,
            ("FACTUAL_ASSERTION", "PAST_STATE", "NEGATED_ASSERTION"),
        ),
        ("entity.name", "string", "one", True, ("FACTUAL_ASSERTION", "PAST_STATE")),
        ("entity.alias", "string", "many", False, ("FACTUAL_ASSERTION", "PAST_STATE")),
    )
    return tuple(
        PredicateDefinition(
            name=name,
            definition_version=PREDICATE_DEFINITION_VERSION,
            value_type=value_type,
            cardinality=cardinality,
            temporal=True,
            replacement_meaningful=replacement,
            normalization_rules={
                "text": "preserve_original",
                "replacement": "requires_explicit_evidence",
            },
            allowed_modalities=modalities,
        )
        for name, value_type, cardinality, replacement, modalities in definitions
    )


def validate_memory_write(
    draft: MemoryDraft,
    *,
    predicates: Sequence[PredicateDefinition],
    supported_extension_names: frozenset[str] = frozenset(),
) -> None:
    """Reject unsupported semantics before persistence; this never approves or activates a claim.

    Only supplied predicate definitions and supported namespaced extensions acquire
    write semantics. Unknown kinds/modalities remain readable through contracts.
    Engine policy still validates evidence, authority, grants, conflicts and verdicts.
    """
    content = draft.content
    if isinstance(content.payload, OpaquePayload) or content.modality not in KNOWN_MODALITIES:
        raise UnsupportedWriteError()

    if unsupported_extension_names(draft, supported_extension_names):
        raise UnsupportedWriteError()
    if not isinstance(content.payload, FactPayload):
        expected_modality = {"episode": "FACTUAL_ASSERTION", "goal": "GOAL", "plan": "PLAN"}
        if content.modality != expected_modality[content.kind]:
            raise UnsupportedWriteError()
        return
    payload = content.payload
    definitions = [definition for definition in predicates if definition.name == payload.predicate]
    if not definitions:
        raise UnsupportedWriteError()
    if len(definitions) != 1:
        raise InvalidPredicateError()
    definition = definitions[0]
    if (
        payload.definition_version != definition.definition_version
        or payload.value.type != definition.value_type
        or content.modality not in definition.allowed_modalities
        or payload.polarity not in {"positive", "negative"}
        or definition.cardinality not in {"one", "many"}
    ):
        raise InvalidPredicateError()
    if (payload.polarity == "negative") != (content.modality == "NEGATED_ASSERTION"):
        raise InvalidPredicateError()
