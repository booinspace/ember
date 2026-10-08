"""Immutable independently versioned activation policy definition."""

from typing import Literal

from ember_memory_abi.archive import ProcessingSpecRef
from ember_memory_abi.common import Confidence, ContractModel, canonical_object_sha256


class ActivationPolicy(ContractModel):
    """Thresholds and conservative rules whose exact definition is pinned by each job."""

    name: Literal["activation.conservative"] = "activation.conservative"
    version: Literal["0.1.0"] = "0.1.0"
    confidence: Confidence = 0.90
    importance: Confidence = 0.50
    direct_user_supports: int = 1
    summary_independent_supports: int = 2
    automatic_modalities: tuple[str, ...] = ("FACTUAL_ASSERTION", "PREFERENCE", "GOAL", "PLAN")
    rules: tuple[str, ...] = (
        "verified_exact_evidence",
        "supported_verdict",
        "appropriate_authority",
        "compatible_semantics",
        "no_graph_ambiguity",
        "no_duplicates_or_conflicts",
        "respect_operator_rejections",
        "preserve_source_modality",
        "explicit_primary_use",
    )

    def reference(self) -> ProcessingSpecRef:
        """Return the exact policy identity retained with every decision."""
        return ProcessingSpecRef(
            name=self.name, version=self.version, specification_sha256=canonical_object_sha256(self)
        )


POLICY = ActivationPolicy()
