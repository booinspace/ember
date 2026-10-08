"""Conservative host policy; confidence/verdict strings from models are not approval."""

import re

from ember_memory_abi.memory import ValidationVerdict

from .activation_spec import POLICY
from .processing_contracts import PolicyDecision, PolicyInputs


def evaluate(inputs: PolicyInputs, *, evaluated_at: str) -> PolicyDecision:
    """Return activation eligibility; actual acceptance remains a separate trusted operation."""
    claim = inputs.proposal
    reasons: list[str] = []
    if not inputs.evidence_validated:
        reasons.append("evidence_unverified")
    if inputs.confidence < POLICY.confidence:
        reasons.append("confidence_below_threshold")
    if claim.importance < POLICY.importance:
        reasons.append("importance_below_threshold")
    if claim.verdict != "supported":
        reasons.append("verdict_not_supported")
    if inputs.source.authority not in {"direct_user", "summary"}:
        reasons.append("authority_requires_review")
    if (
        inputs.source.authority == "summary"
        and inputs.corroborating_sources < POLICY.summary_independent_supports
    ):
        reasons.append("summary_requires_independent_support_or_confirmation")
    if claim.modality not in POLICY.automatic_modalities:
        reasons.append("modality_requires_review")
    if not inputs.supported_semantics:
        reasons.append("unsupported_semantics")
    if inputs.ambiguity:
        reasons.append("graph_ambiguity")
    if inputs.duplicate:
        reasons.append("duplicate_proposal")
    if inputs.conflict:
        reasons.append("unresolved_conflict")
    if inputs.rejected_previously:
        reasons.append("operator_rejected_claim")
    quote = claim.quote.casefold()
    if claim.modality == "FACTUAL_ASSERTION" and re.search(
        r"\b(might|maybe|possibly|hypothetically|intend|planning|hope|wish|would|could)\b"
        r"|(?:he|she|they|someone) said",
        quote,
    ):
        reasons.append("source_modality_requires_review")
    if claim.predicate == "hardware.primary_gpu" and not re.search(r"\b(primary|main)\b", quote):
        reasons.append("primary_use_not_established")
    if inputs.source.authority == "assistant":
        reasons.append("assistant_not_personal_authority")
    verdict = ValidationVerdict(
        policy=POLICY.reference(),
        verdict="eligible" if not reasons else "requires_confirmation",
        reasons=tuple(reasons) or ("conservative_inputs_satisfied",),
        evaluated_at=evaluated_at,
    )
    return PolicyDecision(inputs=inputs, verdict=verdict)
