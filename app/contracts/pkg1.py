from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PKG1Source(BaseModel):
    source_id: str
    source_type: Literal["OPERATOR_APPROVAL", "DETERMINISTIC_CALCULATION", "ACTIVE_POLICY", "EXTERNAL_PRIMARY"]
    title: str
    source_ref: str
    freshness: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    rights_state: Literal["INTERNAL_APPROVED", "NOT_APPLICABLE", "VERIFIED", "REVIEW_REQUIRED"]
    allowed_use: str

    model_config = ConfigDict(extra="forbid")


class ScenarioAssumption(BaseModel):
    key: str
    value: float
    unit: str

    model_config = ConfigDict(extra="forbid")


class PKG1ClaimEvidence(BaseModel):
    claim_id: str
    claim_text: str
    claim_type: Literal[
        "ILLUSTRATIVE_SCENARIO",
        "EDITORIAL_INFERENCE",
        "MATERIAL_FACT",
        "MEASURED_RESULT",
        "UNIVERSAL_OUTCOME",
    ]
    source_refs: list[str] = Field(min_length=1)
    freshness: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    allowed_wording: str
    disallowed_wording: str
    verification_state: Literal["VERIFIED", "ILLUSTRATIVE_ONLY", "BLOCKED"]
    assumptions: list[ScenarioAssumption] = Field(default_factory=list)
    calculation: str | None = None
    result: float | None = None
    result_unit: str | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_evidence(self) -> "PKG1ClaimEvidence":
        if self.claim_type == "ILLUSTRATIVE_SCENARIO":
            if len(self.assumptions) < 2 or not self.calculation or self.result is None:
                raise ValueError("illustrative scenario requires explicit assumptions and calculation")
            if self.verification_state != "ILLUSTRATIVE_ONLY":
                raise ValueError("illustrative scenario cannot be represented as measured evidence")
        if self.claim_type in {"MEASURED_RESULT", "UNIVERSAL_OUTCOME"} and self.verification_state != "VERIFIED":
            raise ValueError("measured or universal claims require verified evidence")
        return self


class PKG1CreativeBrief(BaseModel):
    profile_snapshot_ref: str
    profile_snapshot_hash: str
    viewer_problem: str
    audience_promise: str
    video_objective: str
    central_thesis: str
    scenario_assumptions: list[str]
    primary_takeaway: str
    format_structure: list[str] = Field(min_length=3)
    target_runtime_minutes: dict[str, float]
    tone: str
    cta_posture: str
    evidence_requirements: list[str]
    visual_strategy: str
    cost_class: str
    risk_class: str
    destination: str
    success_criteria: list[str]

    model_config = ConfigDict(extra="forbid")


class PKG1ScriptSegment(BaseModel):
    segment_id: str
    section: str
    editorial_span: dict[str, int]
    text: str = Field(min_length=1)
    claim_ids: list[str]
    source_refs: list[str]
    visual_intent_hint: str
    pronunciation_notes: list[str] = Field(default_factory=list)
    section_boundary: Literal["OPEN", "CONTINUE", "CLOSE"]

    model_config = ConfigDict(extra="forbid")


class PKG1EditorialScript(BaseModel):
    language: Literal["en-US"]
    tone: Literal["documentary/explainer"]
    title: str
    segments: list[PKG1ScriptSegment] = Field(min_length=3)
    cta_decision: str
    estimated_word_count: int = Field(gt=0)
    advisory_duration_estimate_minutes: dict[str, float]
    timing_authority: Literal["ADVISORY_ONLY"] = "ADVISORY_ONLY"

    model_config = ConfigDict(extra="forbid")


class SpokenMappingUnit(BaseModel):
    segment_id: str
    source_text: str
    spoken_text: str
    source_hash: str
    spoken_hash: str
    spoken_token_start: int
    spoken_token_end: int
    operations: list[str]

    model_config = ConfigDict(extra="forbid")


class SpokenToken(BaseModel):
    index: int
    segment_id: str
    text: str

    model_config = ConfigDict(extra="forbid")


class PKG1SpokenTextNormalized(BaseModel):
    compiler_version: Literal["pkg1.spoken-normalizer.v1"]
    policy_ref: str
    source_script_hash: str
    normalized_text_hash: str
    normalized_text: str
    mappings: list[SpokenMappingUnit] = Field(min_length=1)
    spoken_tokens: list[SpokenToken] = Field(min_length=1)
    pronunciation_dictionary_refs: list[str]
    ambiguous_transforms: list[str] = Field(default_factory=list, max_length=0)
    provider_timing_created: Literal[False] = False

    model_config = ConfigDict(extra="forbid")


class NarrationPacingPreflightEstimate(BaseModel):
    name: Literal["NarrationPacingPreflightEstimate"]
    advisory_only: Literal[True] = True
    word_count: int = Field(gt=0)
    sentence_count: int = Field(gt=0)
    maximum_sentence_words: int = Field(gt=0)
    approved_delivery_wpm_range: dict[str, float]
    predicted_duration_minutes: dict[str, float]
    target_runtime_minutes: dict[str, float]
    decision: Literal["ADVISORY_PASS", "BLOCK"]
    canonical_timing_authority: Literal[False] = False

    model_config = ConfigDict(extra="forbid")


class SceneVisualIntent(BaseModel):
    scene_id: str
    segment_refs: list[str] = Field(min_length=1)
    editorial_order: int = Field(ge=1)
    spoken_token_span_intent: dict[str, int]
    target_duration_range_seconds: dict[str, float]
    source_role: Literal["NATIVE_VISUAL", "PEXELS_SUPPORTING", "AI_HERO", "SCREENSHOT"]
    semantic_intent: str
    evidence_role: Literal["EXPLANATORY", "CONTEXT_ONLY", "NONE"]
    source_justification: str
    canonical_timestamps: Literal[None] = None

    model_config = ConfigDict(extra="forbid")


class PKG1VisualDirection(BaseModel):
    contract_ref: str
    contract_hash: str
    native_backbone_required: Literal[True] = True
    stock_is_factual_evidence: Literal[False] = False
    ai_hero_is_filler: Literal[False] = False
    scenes: list[SceneVisualIntent] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class PKG1CostEstimate(BaseModel):
    catalog_refs: list[str] = Field(min_length=1)
    currency: Literal["USD"]
    line_items: list[dict[str, Any]]
    estimated_cost: float = Field(ge=0)
    hard_cap: float = Field(ge=0)
    actual_cost: Literal[None] = None
    estimate_state: Literal["PLANNING_ONLY"] = "PLANNING_ONLY"
    decision: Literal["PASS", "BLOCK"]

    model_config = ConfigDict(extra="forbid")


class PKG1GateResult(BaseModel):
    gate_key: str
    result: Literal["PASS", "BLOCK", "NOT_RUN"]
    reason_codes: list[str] = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    revision_cycle: int = Field(ge=0, le=2)

    model_config = ConfigDict(extra="forbid")


class PKG1BuildResult(BaseModel):
    package_id: str
    video_project_id: str
    selected_topic: str
    used_fallback_topic: bool
    technical_status: Literal["PASS", "BLOCKED"]
    human_review_state: Literal["PENDING"]
    provider_execution: Literal["DISABLED"]
    exact_next_action: str

    model_config = ConfigDict(extra="forbid")
