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


class ForbiddenScopeObservation(_Strict):
    """One independent verifier observation for every frozen exclusion."""

    forbidden_scope_id: str = Field(min_length=1)
    state: Literal["ABSENT", "VIOLATED", "AMBIGUOUS"]
    script_spans: list[ScriptSpan] = Field(default_factory=list)
    observed_relation: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class ScriptRuntimeContract(_Strict):
    """The small, immutable production-script contract frozen before writing."""

    schema_version: Literal["script-runtime-contract.v1"] = "script-runtime-contract.v1"
    expected_language: str = Field(min_length=2)
    duration_contract: dict[str, Any]
    duration_estimation_method: Literal["WORD_COUNT_WPM"] = "WORD_COUNT_WPM"
    duration_estimation_wpm: int = Field(ge=80, le=450)
    minimum_major_sections: int = Field(ge=1)
    minimum_material_claims: int = Field(ge=1)
    forbidden_claims: list[str] = Field(default_factory=list)
    forbidden_style_terms: list[str] = Field(default_factory=list)
    channel_profile_version_id: str = Field(min_length=1)
    channel_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_policy_snapshot_id: str = Field(min_length=1)
    compiled_policy_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ScriptAssignmentResolution(_Strict):
    """Frozen content-mode decision used by both writer and final admission."""

    schema_version: Literal["script-assignment-resolution.v1"] = (
        "script-assignment-resolution.v1"
    )
    assignment_mode: str = Field(min_length=1)
    content_mode: Literal["STANDALONE", "SERIES_EPISODE"]
    standalone_reason_code: str | None = None
    standalone_self_containment_required: bool
    series_plan_id: str | None = None
    series_run_id: str | None = None
    episode_number: int | None = Field(default=None, gt=0)
    episode_role: str | None = None
    episode_delta: str | None = None
    series_learning_outcome: str | None = None
    authority_refs: dict[str, Any] = Field(default_factory=dict)
    resolution_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_mode_binding(self) -> "ScriptAssignmentResolution":
        if self.content_mode == "STANDALONE":
            if not self.standalone_reason_code or not self.standalone_self_containment_required:
                raise ValueError("SCRIPT_ASSIGNMENT_STANDALONE_BINDING_INVALID")
            if any(value is not None for value in (self.series_plan_id, self.series_run_id, self.episode_number, self.episode_role, self.episode_delta)):
                raise ValueError("SCRIPT_ASSIGNMENT_STANDALONE_SERIES_FIELDS_PRESENT")
        elif not all((self.series_plan_id, self.series_run_id, self.episode_number, self.episode_delta)):
            raise ValueError("SCRIPT_ASSIGNMENT_SERIES_BINDING_INVALID")
        return self


class SemanticVerificationOutput(_Strict):
    material_claim_inventory: list[MaterialClaimObservation] = Field(min_length=1)
    assignment_fulfillment_observations: list[AssignmentObservation] = Field(default_factory=list)
    section_purpose_observations: list[SectionPurposeObservation] = Field(default_factory=list)
    forbidden_scope_observations: list[ForbiddenScopeObservation] = Field(default_factory=list)
    memory_application_observations: list[dict[str, Any]] = Field(default_factory=list)
