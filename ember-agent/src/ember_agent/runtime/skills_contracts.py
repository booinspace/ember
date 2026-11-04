"""Provider-neutral skill metadata; packages and instructions confer no execution authority."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

SkillId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*/[A-Za-z0-9_./-]{1,180}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class SkillModel(BaseModel):
    """Strict bounded data shared by the operator API and installed provider seam."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class SkillSummary(SkillModel):
    """Metadata only; the complete procedure/resources are fetched separately."""

    id: SkillId
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(max_length=512)
    version: str = Field(max_length=128)
    source: str = Field(max_length=256)
    content_sha256: Digest
    enabled: bool
    requirements: tuple[str, ...] = Field(default=(), max_length=64)
    unavailable: tuple[str, ...] = Field(default=(), max_length=32)
    diagnostics: tuple[str, ...] = Field(default=(), max_length=32)


class SkillResource(SkillModel):
    """One portable package member; executable resources are data until a tool is granted."""

    path: str = Field(min_length=1, max_length=256)
    sha256: Digest
    size_bytes: int = Field(ge=0, le=1048576)


class SkillDocument(SkillModel):
    """Original Markdown/provider fields survive inspection and export unchanged."""

    skill: SkillSummary
    markdown: str = Field(max_length=65536, repr=False)
    metadata: dict[str, JsonValue]
    extensions: dict[str, JsonValue]
    resources: tuple[SkillResource, ...] = Field(max_length=128)


class SkillCatalog(SkillModel):
    """Selected harness inventory and compare-and-set enablement revision."""

    provider: str = Field(max_length=32)
    revision: str = Field(max_length=128)
    skills: tuple[SkillSummary, ...] = Field(default=(), max_length=256)
    unavailable: str | None = Field(default=None, max_length=128)


class SkillSelection(SkillModel):
    """Operator decision bound to the exact inspected package and catalog revision."""

    id: SkillId
    content_sha256: Digest
    enabled: bool
    revision: str = Field(min_length=1, max_length=128)


class SkillDependency(SkillModel):
    """A copied procedure remains bound to its provider, bytes and current enablement."""

    id: SkillId
    content_sha256: Digest


class SkillBundle(SkillModel):
    """Portable raw package bytes; the harness validates every member before installation."""

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    files: dict[str, str] = Field(min_length=1, max_length=128, repr=False)

    @model_validator(mode="after")
    def bound_wire(self) -> "SkillBundle":
        if sum(len(key.encode()) + len(value) for key, value in self.files.items()) > 196608:
            raise ValueError("Skill bundle exceeds the transport bound")
        return self
