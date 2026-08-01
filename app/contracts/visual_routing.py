from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.m6 import PreferredSource, SceneSourceDecisionContract
from app.contracts.visual_direction import SceneVisualIntent


class NicheVisualSourceProfile(StrEnum):
    STOCK_NATIVE = "STOCK_NATIVE"
    STOCK_ASSISTED = "STOCK_ASSISTED"
    GENERATED_EDITORIAL_FIRST = "GENERATED_EDITORIAL_FIRST"
    AUTHORITY_ASSET_FIRST = "AUTHORITY_ASSET_FIRST"


class VisualSourceRoute(StrEnum):
    ARCHIVED_ASSET_REUSE = "ARCHIVED_ASSET_REUSE"
    PEXELS_VIDEO = "PEXELS_VIDEO"
    PEXELS_PHOTO = "PEXELS_PHOTO"
    AI_GENERATED_IMAGE = "AI_GENERATED_IMAGE"
    AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY = "AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY"
    NATIVE_DIAGRAM = "NATIVE_DIAGRAM"
    NATIVE_MOTION_GRAPHIC = "NATIVE_MOTION_GRAPHIC"
    EDITORIAL_TEXT_GRAPHIC = "EDITORIAL_TEXT_GRAPHIC"
    AUTHORIZED_UI_OR_PRODUCT_ASSET = "AUTHORIZED_UI_OR_PRODUCT_ASSET"
    HUMAN_SUPPLIED_ASSET = "HUMAN_SUPPLIED_ASSET"
    VEO_TEXT_TO_VIDEO = "VEO_TEXT_TO_VIDEO"
    VEO_IMAGE_TO_VIDEO = "VEO_IMAGE_TO_VIDEO"
    UNRESOLVED_BLOCK = "UNRESOLVED_BLOCK"


class SourceFallbackClass(StrEnum):
    PEXELS_ONLY = "PEXELS_ONLY"
    PEXELS_PRIMARY_WITH_AI_ALLOWED = "PEXELS_PRIMARY_WITH_AI_ALLOWED"
    AI_IMAGE_PRIMARY = "AI_IMAGE_PRIMARY"
    NATIVE_ONLY = "NATIVE_ONLY"
    AUTHORIZED_ASSET_ONLY = "AUTHORIZED_ASSET_ONLY"
    NO_FALLBACK = "NO_FALLBACK"


class PexelsEligibilityResult(StrEnum):
    PEXELS_ELIGIBLE = "PEXELS_ELIGIBLE"
    PEXELS_SUPPORTING_ONLY = "PEXELS_SUPPORTING_ONLY"
    PEXELS_LOW_CONFIDENCE = "PEXELS_LOW_CONFIDENCE"
    PEXELS_PROHIBITED = "PEXELS_PROHIBITED"


class AIImageEligibilityResult(StrEnum):
    AI_IMAGE_ALLOWED = "AI_IMAGE_ALLOWED"
    AI_IMAGE_WITH_NATIVE_OVERLAY_REQUIRED = "AI_IMAGE_WITH_NATIVE_OVERLAY_REQUIRED"
    AI_IMAGE_LOW_CONFIDENCE = "AI_IMAGE_LOW_CONFIDENCE"
    AI_IMAGE_PROHIBITED = "AI_IMAGE_PROHIBITED"


class VisualDecisionStatus(StrEnum):
    PLANNED = "PLANNED"
    READY = "READY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


class EvidenceTruthResult(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    AUTHORIZED_SOURCE_REQUIRED = "AUTHORIZED_SOURCE_REQUIRED"
    AUTHORIZED_SOURCE_AVAILABLE = "AUTHORIZED_SOURCE_AVAILABLE"
    BLOCKED = "BLOCKED"


class DiagramSuitabilityResult(StrEnum):
    NOT_PREFERRED = "NOT_PREFERRED"
    NATIVE_DIAGRAM = "NATIVE_DIAGRAM"
    NATIVE_MOTION_GRAPHIC = "NATIVE_MOTION_GRAPHIC"


class ArchiveReuseResult(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"


class AuthoritativeOverlayContentKind(StrEnum):
    HEADLINE = "HEADLINE"
    NUMBER = "NUMBER"
    PERCENTAGE = "PERCENTAGE"
    WORKFLOW_LABEL = "WORKFLOW_LABEL"
    TOOL_NAME = "TOOL_NAME"
    PRODUCT_NAME = "PRODUCT_NAME"
    QUOTE = "QUOTE"
    CITATION = "CITATION"
    CTA = "CTA"
    DATA_VALUE = "DATA_VALUE"
    UI_TEXT = "UI_TEXT"


class VisualSourcePolicyInvariants(BaseModel):
    minimum_output_resolution: Literal["1080p"]
    allow_resolution_downgrade: Literal[False]
    final_composition_authority: Literal["native_ffmpeg"]
    exact_text_authority: Literal["native_only"]
    exact_number_authority: Literal["native_only"]
    generated_evidence_authority: Literal[False]
    one_source_decision_per_scene: Literal[True]
    auto_pexels_to_ai_failover: Literal[False]
    new_vendor_requires_operator_approval: Literal[True]
    existing_vendor_new_provider_route_requires_operator_approval: Literal[True]
    maximum_automated_attempts_per_scene: Literal[1]

    model_config = ConfigDict(extra="forbid")


class VisualSourcePolicyLifecycle(BaseModel):
    state: Literal["INACTIVE"]
    fixture_only: Literal[True]
    channel_profile_binding_allowed: Literal[False]
    provider_execution_allowed: Literal[False]
    activation_milestone: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class NicheVisualSourceProfilePolicy(BaseModel):
    key: NicheVisualSourceProfile
    semantics: dict[str, Any] = Field(min_length=1)
    default_source_families: list[VisualSourceRoute] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class VisualSourceRoutePolicy(BaseModel):
    key: VisualSourceRoute
    fallback_class: SourceFallbackClass
    route_state: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class ForbiddenVisualFallbackPair(BaseModel):
    from_route: VisualSourceRoute
    to_route: VisualSourceRoute
    scope: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class VisualSourceRoutingFixturePolicy(BaseModel):
    key: str = Field(min_length=1)
    channel_key: str = Field(min_length=1)
    niche_visual_source_profile: NicheVisualSourceProfile
    fixture_only: Literal[True]
    active: Literal[False]
    channel_profile_version_binding: Literal[None]
    provider_execution_allowed: Literal[False]

    model_config = ConfigDict(extra="forbid")


class VisualScoreRangePolicy(BaseModel):
    minimum: float = Field(ge=0.0, le=1.0)
    maximum: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_unit_interval(self) -> "VisualScoreRangePolicy":
        if self.minimum != 0.0 or self.maximum != 1.0:
            raise ValueError("VSR1_POLICY_SCORE_RANGE_MUST_BE_UNIT_INTERVAL")
        return self


class PexelsEligibleThresholdPolicy(BaseModel):
    filmability_score_min: float = Field(ge=0.0, le=1.0)
    stock_searchability_score_min: float = Field(ge=0.0, le=1.0)
    custom_composition_score_max: float = Field(ge=0.0, le=1.0)
    exact_text_dependency_max: float = Field(ge=0.0, le=1.0)
    exact_number_dependency_max: float = Field(ge=0.0, le=1.0)
    evidence_truth_requirement_max: float = Field(ge=0.0, le=1.0)
    identity_consistency_requirement_max: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid")


class PexelsSupportingThresholdPolicy(BaseModel):
    filmability_score_min: float = Field(ge=0.0, le=1.0)
    stock_searchability_score_min: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid")


class PexelsProhibitedThresholdPolicy(BaseModel):
    exact_text_dependency_min: float = Field(ge=0.0, le=1.0)
    exact_number_dependency_min: float = Field(ge=0.0, le=1.0)
    evidence_truth_requirement_min: float = Field(ge=0.0, le=1.0)
    custom_composition_score_min: float = Field(ge=0.0, le=1.0)
    product_specificity_min: float = Field(ge=0.0, le=1.0)
    recurring_identity_required: Literal[True]
    named_workflow_nodes_required: Literal[True]

    model_config = ConfigDict(extra="forbid")


class EvidenceTruthSourceThresholdPolicy(BaseModel):
    evidence_truth_requirement_min: float = Field(ge=0.0, le=1.0)
    authorized_asset_required_at_or_above_threshold: Literal[True]
    unresolved_without_authorized_asset: Literal[True]

    model_config = ConfigDict(extra="forbid")


class DiagramSuitabilityThresholdPolicy(BaseModel):
    diagram_clarity_advantage_min: float = Field(ge=0.0, le=1.0)
    native_motion_graphic_motion_semantic_value_min: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid")


class AIImageEligibilityThresholdPolicy(BaseModel):
    custom_composition_score_min: float = Field(ge=0.0, le=1.0)
    evidence_truth_requirement_must_be_below: float = Field(ge=0.0, le=1.0)
    actual_ui_product_document_truth_allowed: Literal[False]
    rights_policy_must_allow_generation: Literal[True]
    planning_allowed: Literal[True]
    provider_execution_enabled: Literal[False]
    activation_milestone: Literal["IMG1"]

    model_config = ConfigDict(extra="forbid")


class VeoRoutingRejectPolicy(BaseModel):
    motion_semantic_value_below: float = Field(ge=0.0, le=1.0)
    still_or_native_motion_sufficient: Literal[True]
    evidence_source_required: Literal[True]
    diagram_clearer: Literal[True]

    model_config = ConfigDict(extra="forbid")


class VeoRoutingBoundaryPolicy(BaseModel):
    motion_semantic_value_min: float = Field(ge=0.0, le=1.0)
    allowed_scene_classes: list[Literal["hero", "metaphor", "transition"]] = Field(
        min_length=3
    )
    still_plus_native_motion_must_be_insufficient: Literal[True]
    evidence_truth_requirement_must_be_below: float = Field(ge=0.0, le=1.0)
    future_cost_class_must_allow_veo: Literal[True]
    reject_when: VeoRoutingRejectPolicy

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_scene_classes(self) -> "VeoRoutingBoundaryPolicy":
        if set(self.allowed_scene_classes) != {"hero", "metaphor", "transition"}:
            raise ValueError("VSR1_VEO_SCENE_CLASS_POLICY_INCOMPLETE")
        return self


class VisualSourceRoutingThresholdPolicy(BaseModel):
    score_range: VisualScoreRangePolicy
    pexels_eligible_if: PexelsEligibleThresholdPolicy
    pexels_supporting_only_if: PexelsSupportingThresholdPolicy
    pexels_prohibited_if: PexelsProhibitedThresholdPolicy
    evidence_truth_source_gate: EvidenceTruthSourceThresholdPolicy
    diagram_suitability_gate: DiagramSuitabilityThresholdPolicy
    ai_image_eligibility_gate: AIImageEligibilityThresholdPolicy
    veo_routing_boundary: VeoRoutingBoundaryPolicy

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_ordered_thresholds(self) -> "VisualSourceRoutingThresholdPolicy":
        eligible = self.pexels_eligible_if
        supporting = self.pexels_supporting_only_if
        prohibited = self.pexels_prohibited_if
        if supporting.filmability_score_min > eligible.filmability_score_min:
            raise ValueError("VSR1_PEXELS_FILMABILITY_THRESHOLDS_INVALID")
        if (
            supporting.stock_searchability_score_min
            > eligible.stock_searchability_score_min
        ):
            raise ValueError("VSR1_PEXELS_SEARCHABILITY_THRESHOLDS_INVALID")
        if prohibited.exact_text_dependency_min <= eligible.exact_text_dependency_max:
            raise ValueError("VSR1_PEXELS_EXACT_TEXT_THRESHOLDS_INVALID")
        if (
            prohibited.exact_number_dependency_min
            <= eligible.exact_number_dependency_max
        ):
            raise ValueError("VSR1_PEXELS_EXACT_NUMBER_THRESHOLDS_INVALID")
        if (
            prohibited.evidence_truth_requirement_min
            <= eligible.evidence_truth_requirement_max
        ):
            raise ValueError("VSR1_PEXELS_EVIDENCE_THRESHOLDS_INVALID")
        return self


class VisualSourceRoutingPolicyCatalogItem(BaseModel):
    """Typed inactive repository-policy item; activation remains a later milestone."""

    key: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    lifecycle: VisualSourcePolicyLifecycle
    visual_source_policy: VisualSourcePolicyInvariants
    niche_visual_source_profiles: list[NicheVisualSourceProfilePolicy]
    source_routes: list[VisualSourceRoutePolicy]
    routing_thresholds: VisualSourceRoutingThresholdPolicy
    fallback_classes: dict[SourceFallbackClass, dict[str, Any]] = Field(min_length=1)
    forbidden_fallback_pairs: list[ForbiddenVisualFallbackPair] = Field(min_length=1)
    archive_reuse_policy: dict[str, Any] = Field(min_length=1)
    exact_text_and_native_overlay_policy: dict[str, Any] = Field(min_length=1)
    fixtures: list[VisualSourceRoutingFixturePolicy] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_complete_taxonomy(self) -> "VisualSourceRoutingPolicyCatalogItem":
        profiles = [item.key for item in self.niche_visual_source_profiles]
        routes = [item.key for item in self.source_routes]
        if _duplicates(profiles) or set(profiles) != set(NicheVisualSourceProfile):
            raise ValueError("VSR1_POLICY_PROFILE_TAXONOMY_INCOMPLETE")
        if _duplicates(routes) or set(routes) != set(VisualSourceRoute):
            raise ValueError("VSR1_POLICY_ROUTE_TAXONOMY_INCOMPLETE")
        if set(self.fallback_classes) != set(SourceFallbackClass):
            raise ValueError("VSR1_POLICY_FALLBACK_TAXONOMY_INCOMPLETE")
        if _duplicates(
            [
                (item.from_route, item.to_route, item.scope)
                for item in self.forbidden_fallback_pairs
            ]
        ):
            raise ValueError("VSR1_POLICY_DUPLICATE_FORBIDDEN_FALLBACK")
        fixture_profiles = {item.niche_visual_source_profile for item in self.fixtures}
        if NicheVisualSourceProfile.STOCK_ASSISTED not in fixture_profiles:
            raise ValueError("VSR1_STOCK_ASSISTED_FIXTURE_REQUIRED")
        return self


MinimumOutputResolution = Literal["1080p", "1440p", "2160p"]
TargetAspectRatio = Literal["16:9"]
EstimatedCostClass = Literal["NONE", "FREE", "LOW", "MEDIUM", "HIGH", "UNKNOWN"]


_AI_IMAGE_ROUTES = frozenset(
    {
        VisualSourceRoute.AI_GENERATED_IMAGE,
        VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
    }
)
_PEXELS_ROUTES = frozenset(
    {VisualSourceRoute.PEXELS_VIDEO, VisualSourceRoute.PEXELS_PHOTO}
)
_VEO_ROUTES = frozenset(
    {VisualSourceRoute.VEO_TEXT_TO_VIDEO, VisualSourceRoute.VEO_IMAGE_TO_VIDEO}
)
_PROVIDER_ROUTES = _PEXELS_ROUTES | _AI_IMAGE_ROUTES | _VEO_ROUTES
_NATIVE_ROUTES = frozenset(
    {
        VisualSourceRoute.NATIVE_DIAGRAM,
        VisualSourceRoute.NATIVE_MOTION_GRAPHIC,
        VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC,
    }
)


def _duplicates(values: list[Any]) -> bool:
    return len(values) != len(set(values))


class SceneVisualRealizationRequirements(SceneVisualIntent):
    """Strict VSR1 feature snapshot extending the canonical visual-direction scene intent."""

    segment_ids: list[str] = Field(min_length=1)
    niche_visual_source_profile: NicheVisualSourceProfile

    scene_class: str = Field(min_length=1)
    narrative_function: str = Field(min_length=1)
    scene_meaning: str = Field(min_length=1)
    editorial_intent: str = Field(min_length=1)

    filmability_score: float = Field(ge=0.0, le=1.0)
    stock_searchability_score: float = Field(ge=0.0, le=1.0)
    required_specificity: float = Field(ge=0.0, le=1.0)
    custom_composition_score: float = Field(ge=0.0, le=1.0)

    exact_text_dependency: float = Field(ge=0.0, le=1.0)
    exact_number_dependency: float = Field(ge=0.0, le=1.0)
    named_workflow_nodes_required: bool
    diagram_clarity_advantage: float = Field(ge=0.0, le=1.0)

    brand_or_product_dependency: float = Field(ge=0.0, le=1.0)
    product_specificity: float = Field(ge=0.0, le=1.0)
    evidence_truth_requirement: float = Field(ge=0.0, le=1.0)
    authorized_asset_available: bool

    identity_consistency_requirement: float = Field(ge=0.0, le=1.0)
    recurring_identity_required: bool
    human_action_requirement: float = Field(ge=0.0, le=1.0)
    motion_semantic_value: float = Field(ge=0.0, le=1.0)

    target_aspect_ratio: TargetAspectRatio
    minimum_resolution: MinimumOutputResolution
    crop_safety_requirement: str = Field(min_length=1)

    previous_scene_intent_ref: str | None
    next_scene_intent_ref: str | None

    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_strict_requirements(self) -> "SceneVisualRealizationRequirements":
        text_fields = {
            "scene_class": self.scene_class,
            "narrative_function": self.narrative_function,
            "scene_meaning": self.scene_meaning,
            "editorial_intent": self.editorial_intent,
            "crop_safety_requirement": self.crop_safety_requirement,
        }
        missing = [name for name, value in text_fields.items() if not value.strip()]
        if missing:
            raise ValueError(
                f"VSR1_ROUTING_CRITICAL_TEXT_MISSING:{','.join(sorted(missing))}"
            )
        if len(set(self.segment_ids)) != len(self.segment_ids):
            raise ValueError("VSR1_DUPLICATE_SEGMENT_ID")
        if self.target_aspect_ratio != self.aspect_ratio:
            raise ValueError("VSR1_TARGET_ASPECT_RATIO_MISMATCH")
        return self


class _ReasonBearingAssessment(BaseModel):
    scene_id: str = Field(min_length=1)
    requirements_hash: str = Field(min_length=1)
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)
    reason_codes: list[str] = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_reason_codes(self) -> "_ReasonBearingAssessment":
        if _duplicates(self.reason_codes):
            raise ValueError("VSR1_DUPLICATE_ASSESSMENT_REASON_CODE")
        if any(not code.strip() for code in self.reason_codes):
            raise ValueError("VSR1_EMPTY_ASSESSMENT_REASON_CODE")
        return self


class PexelsEligibilityAssessment(_ReasonBearingAssessment):
    result: PexelsEligibilityResult
    eligible_routes: list[VisualSourceRoute] = Field(default_factory=list)
    supporting_only: bool
    provider_execution_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_pexels_assessment(self) -> "PexelsEligibilityAssessment":
        routes = set(self.eligible_routes)
        if _duplicates(self.eligible_routes) or routes - _PEXELS_ROUTES:
            raise ValueError("VSR1_PEXELS_ASSESSMENT_ROUTE_INVALID")
        if self.result == PexelsEligibilityResult.PEXELS_PROHIBITED and routes:
            raise ValueError("VSR1_PEXELS_PROHIBITED_WITH_ROUTE")
        if self.result == PexelsEligibilityResult.PEXELS_ELIGIBLE and not routes:
            raise ValueError("VSR1_PEXELS_ELIGIBLE_ROUTE_REQUIRED")
        if self.result == PexelsEligibilityResult.PEXELS_SUPPORTING_ONLY:
            if not routes or not self.supporting_only:
                raise ValueError("VSR1_PEXELS_SUPPORTING_ONLY_INCONSISTENT")
        elif self.supporting_only:
            raise ValueError("VSR1_PEXELS_SUPPORTING_FLAG_INCONSISTENT")
        if self.result == PexelsEligibilityResult.PEXELS_LOW_CONFIDENCE and routes:
            raise ValueError("VSR1_PEXELS_LOW_CONFIDENCE_WITH_ROUTE")
        return self


class AIImageEligibilityAssessment(_ReasonBearingAssessment):
    result: AIImageEligibilityResult
    planning_routes: list[VisualSourceRoute] = Field(default_factory=list)
    native_overlay_required: bool
    provider_execution_allowed: Literal[False] = False
    future_provider_approval_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_ai_image_assessment(self) -> "AIImageEligibilityAssessment":
        routes = set(self.planning_routes)
        if _duplicates(self.planning_routes) or routes - _AI_IMAGE_ROUTES:
            raise ValueError("VSR1_AI_IMAGE_ASSESSMENT_ROUTE_INVALID")
        if self.result == AIImageEligibilityResult.AI_IMAGE_PROHIBITED and routes:
            raise ValueError("VSR1_AI_IMAGE_PROHIBITED_WITH_ROUTE")
        if (
            self.result
            in {
                AIImageEligibilityResult.AI_IMAGE_ALLOWED,
                AIImageEligibilityResult.AI_IMAGE_WITH_NATIVE_OVERLAY_REQUIRED,
            }
            and not routes
        ):
            raise ValueError("VSR1_AI_IMAGE_PLANNING_ROUTE_REQUIRED")
        if (
            self.result
            == AIImageEligibilityResult.AI_IMAGE_WITH_NATIVE_OVERLAY_REQUIRED
        ):
            if (
                routes != {VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY}
                or not self.native_overlay_required
            ):
                raise ValueError("VSR1_AI_IMAGE_NATIVE_OVERLAY_INCONSISTENT")
        elif self.native_overlay_required:
            raise ValueError("VSR1_AI_IMAGE_UNEXPECTED_NATIVE_OVERLAY")
        if self.result == AIImageEligibilityResult.AI_IMAGE_ALLOWED:
            if routes != {VisualSourceRoute.AI_GENERATED_IMAGE}:
                raise ValueError("VSR1_AI_IMAGE_ALLOWED_ROUTE_INVALID")
        if (
            self.result
            in {
                AIImageEligibilityResult.AI_IMAGE_LOW_CONFIDENCE,
                AIImageEligibilityResult.AI_IMAGE_PROHIBITED,
            }
            and routes
        ):
            raise ValueError("VSR1_AI_IMAGE_NON_ELIGIBLE_WITH_ROUTE")
        return self


class EvidenceTruthAssessment(_ReasonBearingAssessment):
    result: EvidenceTruthResult
    evidence_truth_required: bool
    authorized_asset_required: bool
    authorized_asset_available: bool
    authorization_evidence_refs: list[str] = Field(default_factory=list)
    selected_route: VisualSourceRoute | None = None

    @model_validator(mode="after")
    def validate_evidence_truth(self) -> "EvidenceTruthAssessment":
        if _duplicates(self.authorization_evidence_refs) or any(
            not ref.strip() for ref in self.authorization_evidence_refs
        ):
            raise ValueError("VSR1_AUTHORIZATION_EVIDENCE_REF_INVALID")
        if self.evidence_truth_required and not self.authorized_asset_required:
            raise ValueError("VSR1_EVIDENCE_REQUIRES_AUTHORIZED_SOURCE")
        if self.authorized_asset_available and not self.authorization_evidence_refs:
            raise ValueError("VSR1_AUTHORIZED_ASSET_PROVENANCE_REQUIRED")
        if self.result == EvidenceTruthResult.AUTHORIZED_SOURCE_AVAILABLE:
            if (
                not self.evidence_truth_required
                or not self.authorized_asset_required
                or not self.authorized_asset_available
                or self.selected_route
                != VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET
            ):
                raise ValueError("VSR1_AUTHORIZED_SOURCE_RESULT_INCONSISTENT")
        if self.result == EvidenceTruthResult.BLOCKED:
            if (
                not self.evidence_truth_required
                or not self.authorized_asset_required
                or self.authorized_asset_available
                or self.selected_route != VisualSourceRoute.UNRESOLVED_BLOCK
            ):
                raise ValueError("VSR1_EVIDENCE_BLOCK_ROUTE_REQUIRED")
        if self.result == EvidenceTruthResult.NOT_REQUIRED:
            if (
                self.evidence_truth_required
                or self.authorized_asset_required
                or self.selected_route is not None
            ):
                raise ValueError("VSR1_EVIDENCE_NOT_REQUIRED_INCONSISTENT")
        if self.result == EvidenceTruthResult.AUTHORIZED_SOURCE_REQUIRED:
            if (
                not self.evidence_truth_required
                or not self.authorized_asset_required
                or self.authorized_asset_available
                or self.selected_route is not None
            ):
                raise ValueError("VSR1_AUTHORIZED_SOURCE_REQUIRED_INCONSISTENT")
        if self.evidence_truth_required and self.selected_route in (
            _PEXELS_ROUTES | _AI_IMAGE_ROUTES | _VEO_ROUTES
        ):
            raise ValueError("VSR1_GENERATED_OR_STOCK_EVIDENCE_PROHIBITED")
        return self


class DiagramSuitabilityAssessment(_ReasonBearingAssessment):
    result: DiagramSuitabilityResult
    diagram_clarity_advantage: float = Field(ge=0.0, le=1.0)
    motion_semantic_value: float = Field(ge=0.0, le=1.0)
    selected_route: VisualSourceRoute | None = None

    @model_validator(mode="after")
    def validate_diagram_result(self) -> "DiagramSuitabilityAssessment":
        expected = {
            DiagramSuitabilityResult.NOT_PREFERRED: None,
            DiagramSuitabilityResult.NATIVE_DIAGRAM: VisualSourceRoute.NATIVE_DIAGRAM,
            DiagramSuitabilityResult.NATIVE_MOTION_GRAPHIC: VisualSourceRoute.NATIVE_MOTION_GRAPHIC,
        }[self.result]
        if self.selected_route != expected:
            raise ValueError("VSR1_DIAGRAM_RESULT_ROUTE_MISMATCH")
        return self


class ArchiveReuseAssessment(_ReasonBearingAssessment):
    result: ArchiveReuseResult
    matched_asset_ref: str | None
    reuse_count: int = Field(ge=0)
    authorization_evidence_refs: list[str] = Field(default_factory=list)
    semantic_fit_passed: bool
    rights_scope_permits_reuse: bool
    reuse_cooldown_permits: bool
    originality_policy_passed: bool
    asset_truth_current: bool

    @model_validator(mode="after")
    def validate_archive_reuse(self) -> "ArchiveReuseAssessment":
        if _duplicates(self.authorization_evidence_refs) or any(
            not ref.strip() for ref in self.authorization_evidence_refs
        ):
            raise ValueError("VSR1_ARCHIVE_AUTHORIZATION_EVIDENCE_INVALID")
        gates = (
            self.semantic_fit_passed,
            self.rights_scope_permits_reuse,
            self.reuse_cooldown_permits,
            self.originality_policy_passed,
            self.asset_truth_current,
        )
        if self.result == ArchiveReuseResult.ELIGIBLE:
            if not self.matched_asset_ref or not all(gates):
                raise ValueError("VSR1_ARCHIVE_REUSE_ELIGIBILITY_INCONSISTENT")
        if (
            self.result == ArchiveReuseResult.INELIGIBLE
            and all(gates)
            and self.matched_asset_ref
        ):
            raise ValueError("VSR1_ARCHIVE_REUSE_INELIGIBILITY_INCONSISTENT")
        return self


class ExactTextNativeOverlayContract(BaseModel):
    scene_id: str = Field(min_length=1)
    source_decision_ref: str = Field(min_length=1)
    source_decision_hash: str = Field(min_length=1)
    preferred_source_route: VisualSourceRoute
    exact_text_required: bool
    exact_number_required: bool
    forbidden_generated_text: Literal[True]
    forbidden_generated_logo: Literal[True]
    forbidden_generated_fake_ui: Literal[True]
    native_overlay_required: bool
    authoritative_content_kinds: list[AuthoritativeOverlayContentKind] = Field(
        default_factory=list
    )
    authoritative_content_refs: list[str] = Field(min_length=1)
    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_native_authority(self) -> "ExactTextNativeOverlayContract":
        if _duplicates(self.authoritative_content_kinds):
            raise ValueError("VSR1_DUPLICATE_AUTHORITATIVE_CONTENT_KIND")
        if _duplicates(self.authoritative_content_refs) or any(
            not ref.strip() for ref in self.authoritative_content_refs
        ):
            raise ValueError("VSR1_AUTHORITATIVE_CONTENT_REF_INVALID")
        if (
            self.exact_text_required or self.exact_number_required
        ) and not self.native_overlay_required:
            raise ValueError("VSR1_EXACT_CONTENT_REQUIRES_NATIVE_OVERLAY")
        if self.exact_text_required and not self.authoritative_content_kinds:
            raise ValueError("VSR1_EXACT_TEXT_CONTENT_KIND_REQUIRED")
        number_kinds = {
            AuthoritativeOverlayContentKind.NUMBER,
            AuthoritativeOverlayContentKind.PERCENTAGE,
            AuthoritativeOverlayContentKind.DATA_VALUE,
        }
        if self.exact_number_required and not (
            set(self.authoritative_content_kinds) & number_kinds
        ):
            raise ValueError("VSR1_EXACT_NUMBER_CONTENT_KIND_REQUIRED")
        if (
            self.preferred_source_route == VisualSourceRoute.AI_GENERATED_IMAGE
            and self.native_overlay_required
        ):
            raise ValueError("VSR1_AI_IMAGE_NATIVE_OVERLAY_ROUTE_REQUIRED")
        return self


class VisualSourceDecision(SceneSourceDecisionContract):
    """VSR1 decision while retaining the historical M6 decision projection."""

    fallback_order: list[PreferredSource] = Field(default_factory=list)
    reason_codes: list[str] = Field(min_length=1)

    decision_version: str = Field(min_length=1)
    niche_visual_source_profile: NicheVisualSourceProfile

    preferred_source_route: VisualSourceRoute
    allowed_fallback_routes: list[VisualSourceRoute]
    forbidden_fallback_routes: list[VisualSourceRoute]
    fallback_class: SourceFallbackClass

    routing_confidence: float = Field(ge=0.0, le=1.0)
    routing_reason_codes: list[str] = Field(min_length=1)
    input_feature_snapshot: dict[str, Any] = Field(min_length=1)
    policy_ref: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_hash: str = Field(min_length=1)

    estimated_cost_class: EstimatedCostClass
    provider_execution_required: bool
    provider_execution_allowed: Literal[False]
    human_approval_required: Literal[True]

    decision_status: VisualDecisionStatus
    block_reason_codes: list[str]

    content_hash: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_vsr1_decision(self) -> "VisualSourceDecision":
        allowed = set(self.allowed_fallback_routes)
        forbidden = set(self.forbidden_fallback_routes)
        if _duplicates(self.allowed_fallback_routes) or _duplicates(
            self.forbidden_fallback_routes
        ):
            raise ValueError("VSR1_DUPLICATE_FALLBACK_ROUTE")
        if allowed & forbidden:
            raise ValueError("VSR1_ALLOWED_FORBIDDEN_FALLBACK_OVERLAP")
        if self.preferred_source_route in allowed | forbidden:
            raise ValueError("VSR1_PREFERRED_ROUTE_CANNOT_BE_FALLBACK")
        if _duplicates(self.routing_reason_codes) or _duplicates(self.reason_codes):
            raise ValueError("VSR1_DUPLICATE_ROUTING_REASON_CODE")
        if self.reason_codes != self.routing_reason_codes:
            raise ValueError("VSR1_HISTORICAL_ROUTING_REASON_MISMATCH")

        blocked = self.decision_status == VisualDecisionStatus.BLOCKED
        unresolved = self.preferred_source_route == VisualSourceRoute.UNRESOLVED_BLOCK
        if blocked != unresolved:
            raise ValueError("VSR1_BLOCK_STATUS_ROUTE_MISMATCH")
        if blocked and not self.block_reason_codes:
            raise ValueError("VSR1_BLOCK_REASON_REQUIRED")
        if not blocked and self.block_reason_codes:
            raise ValueError("VSR1_NON_BLOCKED_DECISION_HAS_BLOCK_REASON")
        if _duplicates(self.block_reason_codes):
            raise ValueError("VSR1_DUPLICATE_BLOCK_REASON_CODE")

        requires_provider = self.preferred_source_route in _PROVIDER_ROUTES
        if self.provider_execution_required != requires_provider:
            raise ValueError("VSR1_PROVIDER_EXECUTION_REQUIREMENT_MISMATCH")
        if requires_provider and self.decision_status not in {
            VisualDecisionStatus.PLANNED,
            VisualDecisionStatus.REVIEW_REQUIRED,
        }:
            raise ValueError("VSR1_PROVIDER_ROUTE_CANNOT_BE_READY")

        if self.fallback_class == SourceFallbackClass.NO_FALLBACK and allowed:
            raise ValueError("VSR1_NO_FALLBACK_CLASS_HAS_ALLOWED_ROUTE")
        if self.fallback_class == SourceFallbackClass.PEXELS_ONLY:
            if self.preferred_source_route not in _PEXELS_ROUTES or allowed & (
                _AI_IMAGE_ROUTES | _VEO_ROUTES
            ):
                raise ValueError("VSR1_PEXELS_ONLY_FALLBACK_INVALID")
        if self.fallback_class == SourceFallbackClass.PEXELS_PRIMARY_WITH_AI_ALLOWED:
            if self.preferred_source_route not in _PEXELS_ROUTES or not (
                allowed & _AI_IMAGE_ROUTES
            ):
                raise ValueError("VSR1_PEXELS_AI_PREAPPROVAL_FALLBACK_INVALID")
        if self.fallback_class == SourceFallbackClass.AI_IMAGE_PRIMARY:
            if self.preferred_source_route not in _AI_IMAGE_ROUTES:
                raise ValueError("VSR1_AI_IMAGE_PRIMARY_ROUTE_INVALID")
            if allowed - _NATIVE_ROUTES:
                raise ValueError("VSR1_AI_IMAGE_FALLBACK_MUST_BE_NATIVE")
        if self.fallback_class == SourceFallbackClass.NATIVE_ONLY:
            if (
                self.preferred_source_route not in _NATIVE_ROUTES
                or allowed & _PROVIDER_ROUTES
            ):
                raise ValueError("VSR1_NATIVE_ONLY_FALLBACK_INVALID")
        if self.fallback_class == SourceFallbackClass.AUTHORIZED_ASSET_ONLY:
            authorized_routes = {
                VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET,
                VisualSourceRoute.HUMAN_SUPPLIED_ASSET,
                VisualSourceRoute.UNRESOLVED_BLOCK,
            }
            if (
                self.preferred_source_route not in authorized_routes
                or allowed - authorized_routes
            ):
                raise ValueError("VSR1_AUTHORIZED_ASSET_ONLY_FALLBACK_INVALID")

        if self.preferred_source_route in _PEXELS_ROUTES and allowed & _AI_IMAGE_ROUTES:
            if (
                self.fallback_class
                != SourceFallbackClass.PEXELS_PRIMARY_WITH_AI_ALLOWED
            ):
                raise ValueError("VSR1_AUTO_PEXELS_TO_AI_FAILOVER_PROHIBITED")
        if self.preferred_source_route in _AI_IMAGE_ROUTES:
            if "IMAGE_PROVIDER_ROUTE_NOT_YET_ACTIVE" not in self.routing_reason_codes:
                raise ValueError("VSR1_AI_IMAGE_INACTIVE_REASON_REQUIRED")

        expected_legacy_projection: tuple[str, str]
        if self.preferred_source_route == VisualSourceRoute.ARCHIVED_ASSET_REUSE:
            expected_legacy_projection = ("APPROVED_ASSET_POOL", "APPROVED_ASSET_POOL")
        elif self.preferred_source_route in _PEXELS_ROUTES:
            expected_legacy_projection = ("API_NATIVE_PROVIDER", "STOCK_PLACEHOLDER")
        elif self.preferred_source_route in (_AI_IMAGE_ROUTES | _VEO_ROUTES):
            expected_legacy_projection = ("API_NATIVE_PROVIDER", "AI_PLACEHOLDER")
        elif self.preferred_source_route in _NATIVE_ROUTES:
            expected_legacy_projection = ("LOCAL_RENDERER", "DIAGRAM_PLACEHOLDER")
        elif (
            self.preferred_source_route
            == VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET
        ):
            expected_legacy_projection = (
                "APPROVED_ASSET_POOL",
                "SCREENSHOT_PLACEHOLDER",
            )
        elif self.preferred_source_route == VisualSourceRoute.HUMAN_SUPPLIED_ASSET:
            expected_legacy_projection = (
                "MANUAL_ASSET_LIBRARY",
                "MANUAL_PREMIUM_PLACEHOLDER",
            )
        else:
            expected_legacy_projection = ("APPROVED_ASSET_POOL", "APPROVED_ASSET_POOL")
        if (self.source_class, self.preferred_source) != expected_legacy_projection:
            raise ValueError("VSR1_HISTORICAL_PROJECTION_MISMATCH")
        return self


__all__ = [
    "AIImageEligibilityAssessment",
    "AIImageEligibilityResult",
    "ArchiveReuseAssessment",
    "ArchiveReuseResult",
    "AuthoritativeOverlayContentKind",
    "DiagramSuitabilityAssessment",
    "DiagramSuitabilityResult",
    "EvidenceTruthAssessment",
    "EvidenceTruthResult",
    "ExactTextNativeOverlayContract",
    "ForbiddenVisualFallbackPair",
    "NicheVisualSourceProfile",
    "NicheVisualSourceProfilePolicy",
    "PexelsEligibilityAssessment",
    "PexelsEligibilityResult",
    "SceneVisualRealizationRequirements",
    "SourceFallbackClass",
    "VisualDecisionStatus",
    "VisualSourcePolicyInvariants",
    "VisualSourcePolicyLifecycle",
    "VisualSourceDecision",
    "VisualSourceRoute",
    "VisualSourceRoutePolicy",
    "VisualSourceRoutingFixturePolicy",
    "VisualSourceRoutingPolicyCatalogItem",
]
