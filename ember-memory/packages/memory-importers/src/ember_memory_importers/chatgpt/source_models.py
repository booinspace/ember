"""Provisional ChatGPT adapter shapes, inferred from public format observations.

These are private-source boundary models, not canonical archive or Memory ABI
contracts. They retain unknown JSON and absent/null distinctions for future review.
Instances can contain personal content or reasoning: never log, persist, or return
instances as diagnostics. The original raw archive remains the preservation source.
"""

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator


class SourceModel(BaseModel):
    """Validate known shapes without discarding unknown provider metadata.

    Use model_fields_set or model_dump(exclude_unset=True) to distinguish absent
    values from explicit null. External identifiers are never canonical identities.
    """

    model_config = ConfigDict(strict=True, extra="allow", frozen=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _validate_json_boundary(cls, value: object) -> object:
        if isinstance(value, cls):
            return value
        adapter = TypeAdapter(
            dict[str, JsonValue], config=ConfigDict(strict=True, allow_inf_nan=False)
        )
        return adapter.validate_python(value)


class SourceAuthor(SourceModel):
    """Observed actor attribution; role/name are claims, not resolved identities."""

    role: str | None = None
    name: str | None = Field(default=None, repr=False)
    metadata: dict[str, JsonValue] | None = Field(default=None, repr=False)


class SourceContent(SourceModel):
    """Preserve text/structured parts and unfamiliar content variants transiently."""

    content_type: str | None = None
    parts: list[JsonValue] | None = Field(default=None, repr=False)
    text: str | None = Field(default=None, repr=False)


class SourceMessage(SourceModel):
    """Raw message shape with unresolved timestamps, channels, and metadata."""

    id: str | None = Field(default=None, repr=False)
    author: SourceAuthor | None = None
    content: SourceContent | None = Field(default=None, repr=False)
    create_time: JsonValue = Field(default=None, repr=False)
    update_time: JsonValue = Field(default=None, repr=False)
    channel: str | None = None
    recipient: str | None = Field(default=None, repr=False)
    metadata: dict[str, JsonValue] | None = Field(default=None, repr=False)
    status: str | None = None
    end_turn: bool | None = None


class SourceNode(SourceModel):
    """Graph nodes exist independently of messages, including null-message roots."""

    id: str | None = Field(default=None, repr=False)
    parent: str | None = Field(default=None, repr=False)
    children: list[str] | None = Field(default=None, repr=False)
    message: SourceMessage | None = Field(default=None, repr=False)


class SourceConversation(SourceModel):
    """Keep every branch and provider key; current_node is only a selection hint."""

    id: str | None = Field(default=None, repr=False)
    conversation_id: str | None = Field(default=None, repr=False)
    title: str | None = Field(default=None, repr=False)
    create_time: JsonValue = Field(default=None, repr=False)
    update_time: JsonValue = Field(default=None, repr=False)
    mapping: dict[str, SourceNode] = Field(repr=False)
    current_node: str | None = Field(default=None, repr=False)
    is_archived: bool | None = None
    is_deleted: bool | None = None
