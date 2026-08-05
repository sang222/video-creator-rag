"""Strict wire contracts for the pre-admission script authority chain."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScriptSpan(_Strict):
    text: str = Field(min_length=1)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    span_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    section_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def valid_range(self) -> "ScriptSpan":
        if self.end_byte <= self.start_byte:
            raise ValueError("SCRIPT_SPAN_EMPTY")
        return self


class WriterClaim(_Strict):
    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=3)
    evidence_span_ids: list[str] = Field(min_length=1)


class ScriptSection(_Strict):
    section_id: str = Field(min_length=1)
    heading: str = Field(min_length=1)
    narration: str = Field(min_length=1)


class QualifiedScriptOutput(_Strict):
    canonical_script: str = Field(min_length=1)
    language: str = Field(min_length=2)
    sections: list[ScriptSection] = Field(min_length=1)
    claims: list[WriterClaim] = Field(default_factory=list)


class MaterialClaimObservation(_Strict):
    observed_claim_id: str = Field(min_length=1)
    span: ScriptSpan
    claim_type: Literal[
        "FACTUAL_ASSERTION", "NUMERIC_QUANTITATIVE_CLAIM", "DATE_TIME_CLAIM",
        "CAPABILITY_CLAIM", "LIMITATION_CLAIM", "COMPARISON", "CAUSAL_CLAIM",
        "ATTRIBUTION", "POLICY_STATEMENT", "PREDICTIVE_STATEMENT",
        "CONSEQUENTIAL_FACTUAL_CONCLUSION", "NON_FACTUAL_OPINION_OR_FRAMING",
        "STRUCTURAL_TRANSITION",
    ]
    materiality_state: Literal["MATERIAL", "NON_MATERIAL"]
    writer_declared_claim_id: str | None = None
    factual_evidence_span_ids: list[str] = Field(default_factory=list)
    semantic_relation: Literal[
        "ENTAILED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED",
        "SCOPE_EXCEEDED", "AMBIGUOUS", "NOT_APPLICABLE",
    ] = "NOT_APPLICABLE"
    assignment_requirement_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class AssignmentObservation(_Strict):
    requirement_id: str = Field(min_length=1)
    status: Literal["SUFFICIENT", "PARTIAL", "MISSING", "CONTRADICTED", "OUT_OF_SCOPE", "AMBIGUOUS"]
    spans: list[ScriptSpan] = Field(default_factory=list)
    evidence_span_ids: list[str] = Field(default_factory=list)
    missing_reasoning_step: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class SectionPurposeObservation(_Strict):
    section_id: str = Field(min_length=1)
    observed_primary_role: str = Field(min_length=1)
    fulfilled_requirement_ids: list[str] = Field(default_factory=list)
    editorial_delta: str = Field(min_length=1)
    genericity_state: Literal["SPECIFIC", "GENERIC", "BOILERPLATE"]
    # Roles are intentionally reusable when the verifier can explain why a
    # second instance advances a distinct editorial purpose.  This must be a
    # durable observation, rather than an implicit exception in the gate.
    role_reuse_justification: str | None = None


class SemanticVerificationOutput(_Strict):
    material_claim_inventory: list[MaterialClaimObservation] = Field(min_length=1)
    assignment_fulfillment_observations: list[AssignmentObservation] = Field(default_factory=list)
    section_purpose_observations: list[SectionPurposeObservation] = Field(default_factory=list)
    memory_application_observations: list[dict[str, Any]] = Field(default_factory=list)
