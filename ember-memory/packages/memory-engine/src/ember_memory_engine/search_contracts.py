"""Engine-private versioned embedding/index contracts; no provider types in the ABI."""

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import Field

from ember_memory_abi.common import CanonicalId, ContractModel, Sha256, UtcTimestamp


class EmbeddingSpec(ContractModel):
    """Exact reproducible preprocessing and runtime identity, independently versioned."""

    model: str = "BAAI/bge-small-en-v1.5"
    model_revision: str
    tokenizer_revision: str
    model_sha256: Sha256
    tokenizer_sha256: Sha256
    dimensions: Literal[384] = 384
    model_limit: Literal[512] = 512
    pooling: Literal["cls"] = "cls"
    dtype: Literal["float32"] = "float32"
    normalization: Literal["l2"] = "l2"
    query_prefix: str = "Represent this sentence for searching relevant passages: "
    document_prefix: str = ""
    preprocessing: str = "original_unicode_with_role_attribution"
    truncation_policy: Literal["reject_or_explicit_chunks"] = "reject_or_explicit_chunks"
    template_version: str = "ember.attribution@0.1.0"
    encoder_version: str = "ember.onnx-cpu-cls@0.1.0"


class Encoder(Protocol):
    """CPU tokenizer/encoder boundary; every input is bounded without implicit truncation."""

    @property
    def specification(self) -> EmbeddingSpec: ...
    def offsets(self, text: str) -> tuple[tuple[int, int], ...]: ...
    def token_count(self, text: str) -> int: ...
    def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[bytes, ...]: ...


class IndexGeneration(ContractModel):
    """Published derived snapshot; no physical paths or canonical authority."""

    version: Literal["0.1.0"] = "0.1.0"
    generation: CanonicalId
    store_id: CanonicalId
    canonical_clock_us: int
    outbox_sequence: int
    embedding: EmbeddingSpec
    layout: (
        Literal[
            "ember.search-layout@0.2.0", "ember.search-layout@0.3.0", "ember.search-layout@0.4.0"
        ]
        | None
    ) = None
    header_projection: Literal["ember.canonical-headers@0.1.0"] | None = None
    distance_metric: Literal["cosine", "l2"] = "cosine"
    chunker: str = "ember.graph-attributed-chunks@0.1.0"
    ranker: str = "ember.rrf-policy@0.1.0"
    documents: int = Field(ge=0)
    vectors: int = Field(ge=0)
    created_at: UtcTimestamp
    file_sha256: Sha256
