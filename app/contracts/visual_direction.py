from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


VisualGateVerdict = Literal["PASS", "REVIEW_REQUIRED", "BLOCK"]
VisualSourceClass = Literal["NATIVE_VISUAL", "SUPPORTING_STOCK", "AI_HERO"]


class VisualDirectionContract(BaseModel):
    """Project-scoped visual language compiled from frozen, provider-neutral policy."""

    contract_version: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    format_identity_ref: str = Field(min_length=1)
    format_identity_hash: str = Field(min_length=1)
    visual_strategy_profile_ref: str = Field(min_length=1)
    visual_strategy_profile_hash: str = Field(min_length=1)

    realism_level: str = Field(min_length=1)
    treatment_mode: str = Field(min_length=1)
    human_presence_policy: str = Field(min_length=1)
    environment_type: str = Field(min_length=1)
    industry_context: str = Field(min_length=1)
    time_of_day: str = Field(min_length=1)

    lighting_direction: str = Field(min_length=1)
    lighting_temperature: str = Field(min_length=1)
    palette: list[str] = Field(min_length=1)
    contrast: str = Field(min_length=1)
    saturation: str = Field(min_length=1)

    camera_distance: str = Field(min_length=1)
    lens_feel: str = Field(min_length=1)
    camera_movement: str = Field(min_length=1)
    motion_intensity: str = Field(min_length=1)
    framing_rule: str = Field(min_length=1)
    depth_of_field_style: str = Field(min_length=1)
    texture_grain: str = Field(min_length=1)
    tone_mode: str = Field(min_length=1)

    prohibited_cliches: list[str] = Field(default_factory=list)
    channel_identity_markers: list[str] = Field(default_factory=list)
    adjacent_scene_constraints: list[str] = Field(default_factory=list)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class SceneVisualIntent(BaseModel):
    scene_id: str = Field(min_length=1)
    semantic_intent: str = Field(min_length=1)
    target_duration_seconds: float = Field(gt=0)
    aspect_ratio: Literal["16:9"] = "16:9"
    crop_safety_required: bool = True
    previous_scene_summary: str | None = None
    next_scene_summary: str | None = None
    subject_action: str | None = None
    camera_angle: str = "eye-level"
    shot_size: str | None = None

    model_config = ConfigDict(extra="forbid")


class VisualAssetEvidence(BaseModel):
    """Lightweight, deterministic metadata/local-frame evidence for one asset."""

    scene_id: str = Field(min_length=1)
    asset_ref: str = Field(min_length=1)
    source_class: VisualSourceClass
    semantic_description: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    environment_type: str | None = None
    industry_context: str | None = None
    lighting_direction: str | None = None
    lighting_temperature: str | None = None
    palette: list[str] = Field(default_factory=list)
    camera_distance: str | None = None
    lens_feel: str | None = None
    camera_movement: str | None = None
    motion_intensity: str | None = None
    framing_rule: str | None = None
    depth_of_field_style: str | None = None
    texture_grain: str | None = None
    tone_mode: str | None = None
    motion_energy: float | None = Field(default=None, ge=0, le=1)
    crop_safety_score: float | None = Field(default=None, ge=0, le=1)
    technical_quality_score: float | None = Field(default=None, ge=0, le=1)
    originality_score: float | None = Field(default=None, ge=0, le=1)
    logo_or_text_present: bool | None = None
    identifiable_person_present: bool | None = None
    brand_or_trademark_present: bool | None = None
    fake_ui_used_as_evidence: bool = False
    implies_endorsement: bool = False
    hard_conflict_reasons: list[str] = Field(default_factory=list)
    representative_still_refs: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class VisualScoreThresholds(BaseModel):
    semantic_pass_min: float = Field(ge=0, le=1)
    semantic_review_min: float = Field(ge=0, le=1)
    adjacency_pass_min: float = Field(ge=0, le=1)
    adjacency_review_min: float = Field(ge=0, le=1)
    hard_conflicts_block: bool
    cross_provider_cut_requires_both_scores_pass: bool

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def ordered(self) -> "VisualScoreThresholds":
        if self.semantic_review_min > self.semantic_pass_min:
            raise ValueError("VISUAL_SEMANTIC_THRESHOLDS_INVALID")
        if self.adjacency_review_min > self.adjacency_pass_min:
            raise ValueError("VISUAL_ADJACENCY_THRESHOLDS_INVALID")
        return self

    @classmethod
    def from_policy(
        cls, policy: Mapping[str, Any] | BaseModel
    ) -> "VisualScoreThresholds":
        """Build thresholds from an injected catalog snapshot or policy family."""

        source = (
            policy.model_dump(mode="python")
            if isinstance(policy, BaseModel)
            else dict(policy)
        )
        selected = dict(source.get("visual_continuity_policy") or source)
        semantic = selected.get("semantic_match_score")
        adjacency = selected.get("adjacency_continuity_score")
        required = {
            "hard_conflicts_block",
            "cross_provider_cut_requires_both_scores_pass",
        }
        if (
            not isinstance(semantic, Mapping)
            or not isinstance(adjacency, Mapping)
            or not required <= set(selected)
        ):
            raise ValueError("VISUAL_SCORE_THRESHOLDS_POLICY_REQUIRED")
        return cls(
            semantic_pass_min=semantic["pass_min"],
            semantic_review_min=semantic["review_min"],
            adjacency_pass_min=adjacency["pass_min"],
            adjacency_review_min=adjacency["review_min"],
            hard_conflicts_block=selected["hard_conflicts_block"],
            cross_provider_cut_requires_both_scores_pass=selected[
                "cross_provider_cut_requires_both_scores_pass"
            ],
        )


class VisualRankingWeights(BaseModel):
    semantic_relevance: float = Field(ge=0, le=1)
    visual_direction_fit: float = Field(ge=0, le=1)
    previous_scene_continuity: float = Field(ge=0, le=1)
    next_scene_continuity: float = Field(ge=0, le=1)
    crop_safety: float = Field(ge=0, le=1)
    motion_suitability: float = Field(ge=0, le=1)
    technical_quality: float = Field(ge=0, le=1)
    originality_bonus: float = Field(ge=0, le=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def bounded_total(self) -> "VisualRankingWeights":
        values = self.model_dump().values()
        if any(value < 0 or value > 1 for value in values) or sum(values) > 1:
            raise ValueError("VISUAL_RANKING_WEIGHTS_INVALID")
        return self

    @classmethod
    def from_policy(
        cls, policy: Mapping[str, Any] | BaseModel
    ) -> "VisualRankingWeights":
        source = (
            policy.model_dump(mode="python")
            if isinstance(policy, BaseModel)
            else dict(policy)
        )
        selected = dict(source.get("visual_continuity_policy") or source)
        weights = selected.get("ranking_weights")
        if not isinstance(weights, Mapping):
            raise ValueError("VISUAL_RANKING_WEIGHTS_POLICY_REQUIRED")
        return cls.model_validate(weights)


class VisualRiskPenalties(BaseModel):
    prior_use_per_count: float = Field(ge=0, le=1)
    prior_use_cap: float = Field(ge=0, le=1)
    exact_asset_reuse: float = Field(ge=0, le=1)
    unknown_logo_or_text: float = Field(ge=0, le=1)
    unknown_person_identity: float = Field(ge=0, le=1)
    unknown_brand_or_trademark: float = Field(ge=0, le=1)
    identifiable_person_present: float = Field(ge=0, le=1)
    total_cap: float = Field(ge=0, le=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def caps_are_consistent(self) -> "VisualRiskPenalties":
        if self.prior_use_cap > self.total_cap:
            raise ValueError("VISUAL_RISK_PENALTY_CAP_INVALID")
        return self

    @classmethod
    def from_policy(
        cls, policy: Mapping[str, Any] | BaseModel
    ) -> "VisualRiskPenalties":
        source = (
            policy.model_dump(mode="python")
            if isinstance(policy, BaseModel)
            else dict(policy)
        )
        selected = dict(source.get("visual_continuity_policy") or source)
        penalties = selected.get("explicit_risk_penalties")
        if not isinstance(penalties, Mapping):
            raise ValueError("VISUAL_RISK_PENALTIES_POLICY_REQUIRED")
        return cls.model_validate(penalties)


class VeoDurationFitThresholds(BaseModel):
    approved_output_duration_seconds: float = Field(gt=0)
    exact_tolerance_seconds: float = Field(ge=0)
    small_bridge_max_seconds: float = Field(ge=0)
    minimum_useful_trim_seconds: float = Field(gt=0)
    narration_timing_change_allowed: Literal[False]
    speed_change_allowed: Literal[False]
    loop_allowed: Literal[False]

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def duration_policy_is_safe(self) -> "VeoDurationFitThresholds":
        if self.minimum_useful_trim_seconds > self.approved_output_duration_seconds:
            raise ValueError("VEO_DURATION_FIT_POLICY_INVALID")
        return self

    @classmethod
    def from_policy(
        cls, policy: Mapping[str, Any] | BaseModel
    ) -> "VeoDurationFitThresholds":
        source = (
            policy.model_dump(mode="python")
            if isinstance(policy, BaseModel)
            else dict(policy)
        )
        selected = dict(source.get("visual_continuity_policy") or source)
        duration_fit = selected.get("veo_duration_fit")
        if not isinstance(duration_fit, Mapping):
            raise ValueError("VEO_DURATION_FIT_POLICY_REQUIRED")
        return cls.model_validate(duration_fit)


class VisualGateResult(BaseModel):
    gate: str = Field(min_length=1)
    verdict: VisualGateVerdict
    score: float | None = Field(default=None, ge=0, le=1)
    pass_min: float | None = Field(default=None, ge=0, le=1)
    review_min: float | None = Field(default=None, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    hard_conflict_reasons: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class SceneVisualEvaluation(BaseModel):
    scene_id: str = Field(min_length=1)
    asset_ref: str = Field(min_length=1)
    semantic_score: float = Field(ge=0, le=1)
    visual_direction_score: float = Field(ge=0, le=1)
    previous_adjacency_score: float | None = Field(default=None, ge=0, le=1)
    next_adjacency_score: float | None = Field(default=None, ge=0, le=1)
    hard_conflict_reasons: list[str] = Field(default_factory=list)
    top_candidate_ranking: list[dict[str, Any]] = Field(default_factory=list)
    selected_rationale: str = Field(min_length=1)
    representative_still_refs: list[str] = Field(default_factory=list)
    gate_results: list[VisualGateResult] = Field(min_length=3)
    result: VisualGateVerdict
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class CompiledVeoPrompt(BaseModel):
    compiler_version: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    visual_direction_ref: str = Field(min_length=1)
    visual_direction_hash: str = Field(min_length=1)
    target_duration_seconds: float = Field(gt=0)
    subject_action: str = Field(min_length=1)
    environment_industry_context: str = Field(min_length=1)
    realism_treatment: str = Field(min_length=1)
    lighting_time_of_day: str = Field(min_length=1)
    camera_angle_shot_size: str = Field(min_length=1)
    camera_movement: str = Field(min_length=1)
    framing_focal_style: str = Field(min_length=1)
    motion_intensity: str = Field(min_length=1)
    continuity_hint: str = Field(min_length=1)
    negative_constraints: list[str] = Field(min_length=1)
    prompt: str = Field(min_length=1)
    negative_prompt: str = Field(min_length=1)
    prompt_hash: str = Field(min_length=1)
    provider_call_made: bool = False
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class FixedDurationFitDecision(BaseModel):
    target_duration_seconds: float = Field(gt=0)
    provider_duration_seconds: float = Field(gt=0)
    decision: Literal[
        "USE_ONE_ASSET",
        "TRIM_TO_TARGET",
        "USE_NATIVE_OR_SUPPORTING_BRIDGE",
        "REPLAN_BEFORE_PROVIDER_EXECUTION",
    ]
    trim_head_seconds: float = Field(default=0, ge=0)
    trim_tail_seconds: float = Field(default=0, ge=0)
    bridge_duration_seconds: float = Field(default=0, ge=0)
    provider_execution_allowed: bool
    narration_timing_changed: bool = False
    speed_change_allowed: bool = False
    loop_allowed: bool = False
    duration_fit_thresholds: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")
