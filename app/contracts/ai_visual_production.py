from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AIVisualRoute = Literal["AI_IMAGE", "AI_VIDEO"]
MotionNeed = Literal[
    "STATIC_SUFFICIENT",
    "MOTION_BENEFICIAL",
    "MOTION_REQUIRED",
]
VisualImportance = Literal["SUPPORTING", "STANDARD", "HIGH", "HERO"]
FactualRisk = Literal["LOW", "MEDIUM", "HIGH"]
TransitionSemanticReason = Literal[
    "CONTINUATION",
    "NEW_STEP",
    "CONTRAST",
    "TOPIC_SHIFT",
    "EXAMPLE_SHIFT",
    "CONCLUSION",
]
MotionFunction = Literal[
    "REVEAL",
    "FOCUS",
    "FOLLOW",
    "COMPARE",
    "PROGRESS",
    "TRANSITION",
    "EMPHASIZE",
    "ESTABLISH",
    "HOLD",
]
CameraMotion = Literal[
    "STATIC",
    "PUSH_IN",
    "PULL_OUT",
    "PAN_LEFT",
    "PAN_RIGHT",
    "DRIFT_UP",
    "DRIFT_DOWN",
    "CONTROLLED_CUSTOM",
]
MotionIntensity = Literal["SUBTLE", "MODERATE"]
SubjectAnchor = Literal[
    "CENTER",
    "LEFT",
    "RIGHT",
    "TOP",
    "BOTTOM",
    "CUSTOM_NORMALIZED_POINT",
]
TransitionPreset = Literal[
    "cut",
    "fade_soft",
    "fade_black",
    "dissolve",
    "slide_left",
    "slide_right",
    "cover_left",
    "cover_right",
    "reveal_up",
    "reveal_down",
]


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"AI_VISUAL_NON_CANONICAL_HASH_VALUE:{type(value).__name__}")


def ai_visual_stable_hash(value: Any) -> str:
    """Return the canonical SHA-256 used by AI-visual planning artifacts."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ai_visual_text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _content_hash_matches(model: BaseModel, field: str = "content_hash") -> bool:
    expected = ai_visual_stable_hash(model.model_dump(mode="json", exclude={field}))
    return getattr(model, field) == expected


def _complete_body(
    model_type: type[BaseModel], values: Mapping[str, Any]
) -> dict[str, Any]:
    """Materialize Pydantic defaults before sealing a canonical payload."""

    draft = model_type.model_construct(**dict(values))
    return draft.model_dump(mode="json", exclude={"content_hash"})


class NormalizedPoint(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid", frozen=True)


class NormalizedRegion(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def inside_frame(self) -> "NormalizedRegion":
        if self.x + self.width > 1.0 or self.y + self.height > 1.0:
            raise ValueError("AI_VISUAL_NORMALIZED_REGION_OUT_OF_BOUNDS")
        return self


class AIVisualNarrationUnit(BaseModel):
    """Server-owned timed semantic input to the deterministic planner."""

    narration_unit_id: str = Field(min_length=1)
    information_unit_ids: list[str] = Field(min_length=1)
    actual_start_ms: int = Field(ge=0)
    actual_end_ms: int = Field(gt=0)
    spoken_text: str = Field(min_length=1)
    scene_meaning: str = Field(min_length=1)
    visual_function: str = Field(min_length=1)
    core_subject: str = Field(min_length=1)
    secondary_subjects: list[str] = Field(default_factory=list)
    action_or_relation: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    visual_goal: str = Field(min_length=1)
    composition_direction: str = Field(min_length=1)
    camera_direction: str = Field(min_length=1)
    continuity_constraints: list[str] = Field(default_factory=list)
    motion_need: MotionNeed
    factual_risk: FactualRisk = "LOW"
    importance: VisualImportance = "STANDARD"
    transition_semantic_reason: TransitionSemanticReason = "CONTINUATION"
    semantic_group_key: str | None = Field(default=None, min_length=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_unit(self) -> "AIVisualNarrationUnit":
        if self.actual_end_ms <= self.actual_start_ms:
            raise ValueError("AI_VISUAL_NARRATION_UNIT_TIMING_INVALID")
        if len(self.information_unit_ids) != len(set(self.information_unit_ids)):
            raise ValueError("AI_VISUAL_INFORMATION_UNIT_ID_DUPLICATE")
        return self


class AIVisualCapabilityProjection(BaseModel):
    schema_version: Literal["vcos.ai-visual-capability-projection.v1"] = (
        "vcos.ai-visual-capability-projection.v1"
    )
    ai_image_production_ready: bool
    ai_video_production_ready: bool
    ai_video_budget_authorized: bool
    maximum_ai_image_assets: int = Field(default=9, ge=0)
    maximum_ai_video_scenes: int = Field(ge=0)
    provider_readiness_ref: str = Field(min_length=1)
    budget_authority_ref: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_hash(self) -> "AIVisualCapabilityProjection":
        if not _content_hash_matches(self):
            raise ValueError("AI_VISUAL_CAPABILITY_PROJECTION_HASH_MISMATCH")
        if not self.ai_video_budget_authorized and self.maximum_ai_video_scenes:
            raise ValueError("AI_VIDEO_SCENE_BUDGET_WITHOUT_AUTHORITY")
        if not self.ai_image_production_ready and self.maximum_ai_image_assets:
            raise ValueError("AI_IMAGE_ASSET_BUDGET_WITHOUT_READY_PROVIDER")
        return self

    @classmethod
    def build(cls, **values: Any) -> "AIVisualCapabilityProjection":
        body = _complete_body(
            cls,
            {"schema_version": "vcos.ai-visual-capability-projection.v1", **values},
        )
        return cls(**body, content_hash=ai_visual_stable_hash(body))


_DEFAULT_FUNCTION_MAXIMUMS_MS = {
    "ACTION": 7_000,
    "PROCESS": 12_000,
    "COMPARISON": 10_000,
    "DATA": 12_000,
    "INTERFACE": 10_000,
    "CONCEPT_MODEL": 14_000,
    "EXAMPLE_CONTEXT": 8_000,
    "TRANSITION_HERO": 8_000,
}


class AIVisualPlanningPolicy(BaseModel):
    schema_version: Literal["vcos.ai-visual-planning-policy.v1"] = (
        "vcos.ai-visual-planning-policy.v1"
    )
    maximum_ai_image_presentation_ms: int = Field(default=12_000, ge=2_000)
    maximum_ai_video_presentation_ms: int = Field(default=8_000, ge=1_000)
    maximum_ai_image_asset_exposure_ms: int = Field(default=60_000, ge=2_000)
    minimum_scene_duration_ms: int = Field(default=1_000, ge=250)
    function_maximum_duration_ms: dict[str, int] = Field(
        default_factory=lambda: dict(_DEFAULT_FUNCTION_MAXIMUMS_MS)
    )
    group_adjacent_semantic_units: bool = True
    allow_ai_video_for_static_when_image_unavailable: bool = False
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_policy(self) -> "AIVisualPlanningPolicy":
        if not _content_hash_matches(self):
            raise ValueError("AI_VISUAL_PLANNING_POLICY_HASH_MISMATCH")
        if self.minimum_scene_duration_ms > self.maximum_ai_image_presentation_ms:
            raise ValueError("AI_VISUAL_SCENE_DURATION_POLICY_INVALID")
        if (
            self.minimum_scene_duration_ms > self.maximum_ai_video_presentation_ms
            or self.maximum_ai_image_asset_exposure_ms
            < self.maximum_ai_image_presentation_ms
        ):
            raise ValueError("AI_VISUAL_ASSET_DURATION_POLICY_INVALID")
        if any(
            value < self.minimum_scene_duration_ms
            for value in self.function_maximum_duration_ms.values()
        ):
            raise ValueError("AI_VISUAL_FUNCTION_DURATION_POLICY_INVALID")
        return self

    @classmethod
    def production_default(cls, **overrides: Any) -> "AIVisualPlanningPolicy":
        body: dict[str, Any] = {
            "schema_version": "vcos.ai-visual-planning-policy.v1",
            "maximum_ai_image_presentation_ms": 12_000,
            "maximum_ai_video_presentation_ms": 8_000,
            "maximum_ai_image_asset_exposure_ms": 60_000,
            "minimum_scene_duration_ms": 1_000,
            "function_maximum_duration_ms": dict(_DEFAULT_FUNCTION_MAXIMUMS_MS),
            "group_adjacent_semantic_units": True,
            "allow_ai_video_for_static_when_image_unavailable": False,
        }
        body.update(overrides)
        body = _complete_body(cls, body)
        return cls(**body, content_hash=ai_visual_stable_hash(body))


class VideoVisualStyleBible(BaseModel):
    schema_version: Literal["vcos.video-visual-style-bible.v1"] = (
        "vcos.video-visual-style-bible.v1"
    )
    style_bible_id: str = Field(min_length=1)
    video_project_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    overall_visual_language: str = Field(min_length=1)
    rendering_style: str = Field(min_length=1)
    lighting: str = Field(min_length=1)
    contrast: str = Field(min_length=1)
    palette_guidance: list[str] = Field(min_length=1)
    materials: list[str] = Field(min_length=1)
    camera_language: str = Field(min_length=1)
    depth: str = Field(min_length=1)
    technical_illustration_language: str = Field(min_length=1)
    human_depiction_rules: list[str] = Field(min_length=1)
    technology_depiction_rules: list[str] = Field(min_length=1)
    negative_aesthetic_constraints: list[str] = Field(min_length=1)
    aspect_ratio: Literal["16:9"] = "16:9"
    visible_generated_text: Literal[False] = False
    fake_product_ui_allowed: Literal[False] = False
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_style_bible(self) -> "VideoVisualStyleBible":
        if not _content_hash_matches(self):
            raise ValueError("VIDEO_VISUAL_STYLE_BIBLE_HASH_MISMATCH")
        return self

    @classmethod
    def build(cls, **values: Any) -> "VideoVisualStyleBible":
        body = _complete_body(
            cls,
            {"schema_version": "vcos.video-visual-style-bible.v1", **values},
        )
        return cls(**body, content_hash=ai_visual_stable_hash(body))


class AIVisualScenePlan(BaseModel):
    schema_version: Literal["vcos.ai-visual-scene-plan.v1"] = (
        "vcos.ai-visual-scene-plan.v1"
    )
    scene_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    narration_unit_ids: list[str] = Field(min_length=1)
    information_unit_ids: list[str] = Field(min_length=1)
    actual_start_ms: int = Field(ge=0)
    actual_end_ms: int = Field(gt=0)
    presentation_start_ms: int = Field(ge=0)
    presentation_end_ms: int = Field(gt=0)
    scene_meaning: str = Field(min_length=1)
    visual_function: str = Field(min_length=1)
    core_subject: str = Field(min_length=1)
    secondary_subjects: list[str] = Field(default_factory=list)
    action_or_relation: str = Field(min_length=1)
    environment: str = Field(min_length=1)
    visual_goal: str = Field(min_length=1)
    visual_style_direction: str = Field(min_length=1)
    composition_direction: str = Field(min_length=1)
    camera_direction: str = Field(min_length=1)
    continuity_constraints: list[str] = Field(default_factory=list)
    motion_need: MotionNeed
    production_route: AIVisualRoute
    primary_asset_slot_id: str = Field(min_length=1)
    reuses_primary_asset_from_scene_id: str | None = Field(default=None, min_length=1)
    asset_reuse_semantic_reason: str | None = Field(default=None, min_length=1)
    prompt_brief: str = Field(min_length=1)
    negative_constraints: list[str] = Field(min_length=1)
    factual_risk: FactualRisk
    importance: VisualImportance
    transition_semantic_reason: TransitionSemanticReason
    style_bible_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    planning_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_scene(self) -> "AIVisualScenePlan":
        if self.actual_end_ms <= self.actual_start_ms:
            raise ValueError("AI_VISUAL_SCENE_ACTUAL_WINDOW_INVALID")
        if self.presentation_end_ms <= self.presentation_start_ms:
            raise ValueError("AI_VISUAL_SCENE_PRESENTATION_WINDOW_INVALID")
        if not (
            self.presentation_start_ms <= self.actual_start_ms
            and self.presentation_end_ms >= self.actual_end_ms
        ):
            raise ValueError("AI_VISUAL_ACTUAL_WINDOW_OUTSIDE_PRESENTATION")
        if (
            self.motion_need == "MOTION_REQUIRED"
            and self.production_route != "AI_VIDEO"
        ):
            raise ValueError("AI_VISUAL_MOTION_REQUIRED_ROUTE_INVALID")
        if (self.reuses_primary_asset_from_scene_id is None) != (
            self.asset_reuse_semantic_reason is None
        ):
            raise ValueError("AI_VISUAL_ASSET_REUSE_REASON_BINDING_INVALID")
        if self.reuses_primary_asset_from_scene_id == self.scene_id:
            raise ValueError("AI_VISUAL_ASSET_REUSE_SELF_REFERENCE")
        if len(self.narration_unit_ids) != len(set(self.narration_unit_ids)):
            raise ValueError("AI_VISUAL_NARRATION_UNIT_ID_DUPLICATE")
        if len(self.information_unit_ids) != len(set(self.information_unit_ids)):
            raise ValueError("AI_VISUAL_INFORMATION_UNIT_ID_DUPLICATE")
        if not _content_hash_matches(self):
            raise ValueError("AI_VISUAL_SCENE_PLAN_HASH_MISMATCH")
        return self


class AIVisualPlanCompilation(BaseModel):
    schema_version: Literal["vcos.ai-visual-plan-compilation.v1"] = (
        "vcos.ai-visual-plan-compilation.v1"
    )
    style_bible_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    planning_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_duration_ms: int = Field(gt=0)
    maximum_ai_image_presentation_ms: int = Field(gt=0)
    maximum_ai_video_presentation_ms: int = Field(gt=0)
    maximum_ai_image_asset_exposure_ms: int = Field(gt=0)
    scenes: list[AIVisualScenePlan] = Field(min_length=1)
    ai_image_scene_count: int = Field(ge=0)
    ai_video_scene_count: int = Field(ge=0)
    unique_asset_slot_count: int = Field(ge=1)
    unique_ai_image_asset_slot_count: int = Field(ge=0)
    unique_ai_video_asset_slot_count: int = Field(ge=0)
    reused_presentation_window_count: int = Field(ge=0)
    coverage_gate: Literal["PASS"] = "PASS"
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_compilation(self) -> "AIVisualPlanCompilation":
        ordered = self.scenes
        if [scene.ordinal for scene in ordered] != list(range(1, len(ordered) + 1)):
            raise ValueError("AI_VISUAL_SCENE_ORDINAL_SEQUENCE_INVALID")
        if len({scene.scene_id for scene in ordered}) != len(ordered):
            raise ValueError("AI_VISUAL_SCENE_ID_DUPLICATE")
        if ordered[0].presentation_start_ms != 0:
            raise ValueError("AI_VISUAL_PRESENTATION_COVERAGE_START_INVALID")
        if ordered[-1].presentation_end_ms != self.canonical_duration_ms:
            raise ValueError("AI_VISUAL_PRESENTATION_COVERAGE_END_INVALID")
        if any(
            left.presentation_end_ms != right.presentation_start_ms
            for left, right in zip(ordered, ordered[1:])
        ):
            raise ValueError("AI_VISUAL_PRESENTATION_COVERAGE_GAP")
        if any(
            scene.production_route == "AI_IMAGE"
            and scene.presentation_end_ms - scene.presentation_start_ms
            > self.maximum_ai_image_presentation_ms
            for scene in ordered
        ):
            raise ValueError("AI_VISUAL_STATIC_PRESENTATION_EXCEEDS_POLICY")
        if any(
            scene.production_route == "AI_VIDEO"
            and scene.presentation_end_ms - scene.presentation_start_ms
            > self.maximum_ai_video_presentation_ms
            for scene in ordered
        ):
            raise ValueError("AI_VISUAL_VIDEO_PRESENTATION_EXCEEDS_POLICY")
        by_id = {scene.scene_id: scene for scene in ordered}
        slot_owners: dict[str, AIVisualScenePlan] = {}
        reused = 0
        for scene in ordered:
            owner_ref = scene.reuses_primary_asset_from_scene_id
            if owner_ref is None:
                if scene.primary_asset_slot_id in slot_owners:
                    raise ValueError("AI_VISUAL_ASSET_SLOT_OWNER_DUPLICATE")
                slot_owners[scene.primary_asset_slot_id] = scene
                continue
            reused += 1
            owner = by_id.get(owner_ref)
            if owner is None or owner.ordinal >= scene.ordinal:
                raise ValueError("AI_VISUAL_ASSET_REUSE_OWNER_INVALID")
            if owner.reuses_primary_asset_from_scene_id is not None:
                raise ValueError("AI_VISUAL_ASSET_REUSE_CHAIN_PROHIBITED")
            if owner.primary_asset_slot_id != scene.primary_asset_slot_id:
                raise ValueError("AI_VISUAL_ASSET_REUSE_SLOT_MISMATCH")
            if owner.production_route != scene.production_route:
                raise ValueError("AI_VISUAL_ASSET_REUSE_ROUTE_MISMATCH")
            if (
                owner.core_subject != scene.core_subject
                or owner.visual_function != scene.visual_function
            ):
                raise ValueError("AI_VISUAL_ASSET_REUSE_SEMANTIC_MISMATCH")
        image_exposure_by_slot: dict[str, int] = {}
        for scene in ordered:
            if scene.production_route != "AI_IMAGE":
                continue
            image_exposure_by_slot[scene.primary_asset_slot_id] = (
                image_exposure_by_slot.get(scene.primary_asset_slot_id, 0)
                + scene.presentation_end_ms
                - scene.presentation_start_ms
            )
        if any(
            exposure > self.maximum_ai_image_asset_exposure_ms
            for exposure in image_exposure_by_slot.values()
        ):
            raise ValueError("AI_VISUAL_IMAGE_ASSET_EXPOSURE_EXCEEDS_POLICY")
        if (
            len(slot_owners) != self.unique_asset_slot_count
            or sum(
                owner.production_route == "AI_IMAGE" for owner in slot_owners.values()
            )
            != self.unique_ai_image_asset_slot_count
            or sum(
                owner.production_route == "AI_VIDEO" for owner in slot_owners.values()
            )
            != self.unique_ai_video_asset_slot_count
            or reused != self.reused_presentation_window_count
        ):
            raise ValueError("AI_VISUAL_ASSET_SLOT_DISTRIBUTION_INVALID")
        image_count = sum(scene.production_route == "AI_IMAGE" for scene in ordered)
        video_count = sum(scene.production_route == "AI_VIDEO" for scene in ordered)
        if (
            image_count != self.ai_image_scene_count
            or video_count != self.ai_video_scene_count
            or image_count + video_count != len(ordered)
        ):
            raise ValueError("AI_VISUAL_ROUTE_DISTRIBUTION_INVALID")
        if not _content_hash_matches(self):
            raise ValueError("AI_VISUAL_PLAN_COMPILATION_HASH_MISMATCH")
        return self


class VideoMotionGrammar(BaseModel):
    schema_version: Literal["vcos.video-motion-grammar.v1"] = (
        "vcos.video-motion-grammar.v1"
    )
    grammar_id: str = Field(min_length=1)
    style_bible_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    default_motion_intensity: MotionIntensity = "SUBTLE"
    preferred_transition_family: list[TransitionPreset] = Field(min_length=1)
    maximum_aggressive_transition_rate: float = Field(default=0.15, ge=0.0, le=1.0)
    maximum_static_presentation_ms: int = Field(default=12_000, ge=2_000)
    maximum_consecutive_same_motion_preset: int = Field(default=2, ge=1, le=8)
    maximum_consecutive_same_transition: int = Field(default=3, ge=1, le=8)
    maximum_consecutive_same_camera_direction: int = Field(default=3, ge=1, le=8)
    still_motion_policy: str = Field(min_length=1)
    hero_motion_policy: str = Field(min_length=1)
    comparison_motion_policy: str = Field(min_length=1)
    ending_motion_policy: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_grammar(self) -> "VideoMotionGrammar":
        if len(self.preferred_transition_family) != len(
            set(self.preferred_transition_family)
        ):
            raise ValueError("VIDEO_MOTION_GRAMMAR_TRANSITION_DUPLICATE")
        if not _content_hash_matches(self):
            raise ValueError("VIDEO_MOTION_GRAMMAR_HASH_MISMATCH")
        return self

    @classmethod
    def production_default(
        cls,
        *,
        grammar_id: str,
        style_bible_hash: str,
        **overrides: Any,
    ) -> "VideoMotionGrammar":
        body: dict[str, Any] = {
            "schema_version": "vcos.video-motion-grammar.v1",
            "grammar_id": grammar_id,
            "style_bible_hash": style_bible_hash,
            "default_motion_intensity": "SUBTLE",
            "preferred_transition_family": [
                "cut",
                "fade_black",
                "fade_soft",
                "dissolve",
                "reveal_up",
            ],
            "maximum_aggressive_transition_rate": 0.15,
            "maximum_static_presentation_ms": 12_000,
            "maximum_consecutive_same_motion_preset": 2,
            "maximum_consecutive_same_transition": 3,
            "maximum_consecutive_same_camera_direction": 3,
            "still_motion_policy": "Meaning-aligned bounded realization; intentional stability may be long.",
            "hero_motion_policy": "Preserve provider-authored motion and use restrained entry, trim, and transition treatment.",
            "comparison_motion_policy": "Use one coherent reveal or lateral comparison movement without mechanical alternation.",
            "ending_motion_policy": "Settle motion and prefer a clean cut or slow fade at the conclusion.",
        }
        body.update(overrides)
        body = _complete_body(cls, body)
        return cls(**body, content_hash=ai_visual_stable_hash(body))


class MotionIntentProjection(BaseModel):
    schema_version: Literal["vcos.motion-intent-projection.v1"] = (
        "vcos.motion-intent-projection.v1"
    )
    scene_id: str = Field(min_length=1)
    scene_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    style_bible_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    motion_grammar_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_asset_ref: str = Field(min_length=1)
    primary_asset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_type: AIVisualRoute
    motion_function: MotionFunction
    camera_motion: CameraMotion
    motion_preset: str = Field(min_length=1)
    subject_anchor: SubjectAnchor
    custom_subject_anchor: NormalizedPoint | None = None
    focal_point: NormalizedPoint
    safe_crop_region: NormalizedRegion
    intensity: MotionIntensity
    start_scale: float = Field(ge=1.0, le=1.12)
    end_scale: float = Field(ge=1.0, le=1.12)
    presentation_start_ms: int = Field(ge=0)
    presentation_end_ms: int = Field(gt=0)
    transition_in: TransitionPreset
    transition_out: TransitionPreset
    transition_semantic_reason: TransitionSemanticReason
    motion_semantic_reason: str = Field(min_length=1)
    safe_area_constraints: list[str] = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_projection(self) -> "MotionIntentProjection":
        if self.presentation_end_ms <= self.presentation_start_ms:
            raise ValueError("MOTION_PRESENTATION_WINDOW_INVALID")
        if (
            self.subject_anchor == "CUSTOM_NORMALIZED_POINT"
            and self.custom_subject_anchor is None
        ):
            raise ValueError("MOTION_CUSTOM_SUBJECT_ANCHOR_REQUIRED")
        if (
            self.subject_anchor != "CUSTOM_NORMALIZED_POINT"
            and self.custom_subject_anchor is not None
        ):
            raise ValueError("MOTION_CUSTOM_SUBJECT_ANCHOR_UNEXPECTED")
        if self.asset_type == "AI_VIDEO" and (
            self.camera_motion != "STATIC"
            or self.start_scale != 1.0
            or self.end_scale != 1.0
        ):
            raise ValueError("MOTION_AI_VIDEO_INTRINSIC_MOTION_MUST_BE_PRESERVED")
        if self.camera_motion == "PUSH_IN" and self.end_scale <= self.start_scale:
            raise ValueError("MOTION_PUSH_IN_SCALE_INVALID")
        if self.camera_motion == "PULL_OUT" and self.end_scale >= self.start_scale:
            raise ValueError("MOTION_PULL_OUT_SCALE_INVALID")
        if "MOVE_BECAUSE_VIDEO_NEEDS_MOVEMENT" in self.motion_semantic_reason.upper():
            raise ValueError("MOTION_SEMANTIC_REASON_INVALID")
        if not _content_hash_matches(self):
            raise ValueError("MOTION_INTENT_PROJECTION_HASH_MISMATCH")
        return self


_MANDATORY_AI_NEGATIVE_CONSTRAINTS = {
    "no presentation slide",
    "no PowerPoint",
    "no three-box flowchart",
    "no generic infographic card",
    "no text-heavy composition",
    "no fake dashboard",
    "no fake product UI",
    "no floating random labels",
    "no visible generated text",
    "no logo",
    "no watermark",
}


class CompiledAIImagePrompt(BaseModel):
    schema_version: Literal["vcos.compiled-ai-image-prompt.v1"] = (
        "vcos.compiled-ai-image-prompt.v1"
    )
    scene_id: str = Field(min_length=1)
    scene_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    style_bible_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_compiler_version: str = Field(min_length=1)
    aspect_ratio: Literal["16:9"] = "16:9"
    expected_motion_preset: str = Field(min_length=1)
    motion_safe_composition: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    negative_constraints: list[str] = Field(min_length=1)
    negative_prompt: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_call_made: Literal[False] = False
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_prompt(self) -> "CompiledAIImagePrompt":
        if not _MANDATORY_AI_NEGATIVE_CONSTRAINTS.issubset(
            set(self.negative_constraints)
        ):
            raise ValueError("AI_IMAGE_PROMPT_MANDATORY_NEGATIVE_CONSTRAINT_MISSING")
        if self.negative_prompt != ", ".join(self.negative_constraints):
            raise ValueError("AI_IMAGE_NEGATIVE_PROMPT_PROJECTION_MISMATCH")
        if self.prompt_hash != ai_visual_text_hash(self.prompt):
            raise ValueError("AI_IMAGE_PROMPT_HASH_MISMATCH")
        if not _content_hash_matches(self):
            raise ValueError("AI_IMAGE_COMPILED_PROMPT_HASH_MISMATCH")
        return self


class CompiledAIVideoPrompt(BaseModel):
    schema_version: Literal["vcos.compiled-ai-video-prompt.v1"] = (
        "vcos.compiled-ai-video-prompt.v1"
    )
    scene_id: str = Field(min_length=1)
    scene_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    style_bible_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_compiler_version: str = Field(min_length=1)
    aspect_ratio: Literal["16:9"] = "16:9"
    target_duration_ms: int = Field(gt=0)
    # The semantic presentation window and the provider generation effect are
    # separate authorities. Veo currently accepts one exact eight-second
    # effect; the assembly layer must explicitly attest any bounded adaptation
    # to a longer or shorter narration window.
    provider_generation_duration_ms: Literal[8000] = 8000
    intrinsic_motion_required: Literal[True] = True
    provider_audio_usage_policy: Literal["DISCARD"] = "DISCARD"
    prompt: str = Field(min_length=1)
    negative_constraints: list[str] = Field(min_length=1)
    negative_prompt: str = Field(min_length=1)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_call_made: Literal[False] = False
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_prompt(self) -> "CompiledAIVideoPrompt":
        if not _MANDATORY_AI_NEGATIVE_CONSTRAINTS.issubset(
            set(self.negative_constraints)
        ):
            raise ValueError("AI_VIDEO_PROMPT_MANDATORY_NEGATIVE_CONSTRAINT_MISSING")
        if self.negative_prompt != ", ".join(self.negative_constraints):
            raise ValueError("AI_VIDEO_NEGATIVE_PROMPT_PROJECTION_MISMATCH")
        if self.prompt_hash != ai_visual_text_hash(self.prompt):
            raise ValueError("AI_VIDEO_PROMPT_HASH_MISMATCH")
        if not _content_hash_matches(self):
            raise ValueError("AI_VIDEO_COMPILED_PROMPT_HASH_MISMATCH")
        return self


class MotionParameterBound(BaseModel):
    minimum: float
    maximum: float
    default: float

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def ordered(self) -> "MotionParameterBound":
        if self.minimum > self.default or self.default > self.maximum:
            raise ValueError("MOTION_PARAMETER_BOUND_INVALID")
        return self


class MotionPresetDefinition(BaseModel):
    key: str = Field(min_length=1)
    pack_version: Literal["NativeMotionPack_v2"] = "NativeMotionPack_v2"
    category: Literal[
        "STILL_MOTION",
        "VIDEO_PRESENTATION",
        "TRANSITION",
        "SECONDARY_OVERLAY",
    ]
    supported_asset_types: list[AIVisualRoute] = Field(min_length=1)
    minimum_duration_ms: int = Field(ge=0)
    maximum_duration_ms: int = Field(gt=0)
    allowed_intensities: list[MotionIntensity] = Field(min_length=1)
    parameter_schema: dict[str, MotionParameterBound] = Field(default_factory=dict)
    semantic_use_cases: list[MotionFunction] = Field(default_factory=list)
    forbidden_use_cases: list[str] = Field(default_factory=list)
    compiler_version: str = Field(min_length=1)
    aggressive: bool = False
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_definition(self) -> "MotionPresetDefinition":
        if self.maximum_duration_ms < self.minimum_duration_ms:
            raise ValueError("MOTION_PRESET_DURATION_BOUNDS_INVALID")
        if len(self.supported_asset_types) != len(set(self.supported_asset_types)):
            raise ValueError("MOTION_PRESET_ASSET_TYPE_DUPLICATE")
        if not _content_hash_matches(self):
            raise ValueError("MOTION_PRESET_DEFINITION_HASH_MISMATCH")
        return self


class CompiledMotionParameters(BaseModel):
    start_scale: float = Field(ge=1.0, le=1.12)
    end_scale: float = Field(ge=1.0, le=1.12)
    crop_x_start: float = Field(ge=0.0, le=1.0)
    crop_x_end: float = Field(ge=0.0, le=1.0)
    crop_y_start: float = Field(ge=0.0, le=1.0)
    crop_y_end: float = Field(ge=0.0, le=1.0)
    focal_x: float = Field(ge=0.0, le=1.0)
    focal_y: float = Field(ge=0.0, le=1.0)
    easing: Literal["LINEAR", "EASE_IN_OUT"] = "EASE_IN_OUT"
    preserve_intrinsic_motion: bool
    transition_duration_ms: int = Field(ge=0, le=1_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_hash(self) -> "CompiledMotionParameters":
        if not _content_hash_matches(self):
            raise ValueError("COMPILED_MOTION_PARAMETERS_HASH_MISMATCH")
        return self


class SceneEffectPlan(BaseModel):
    schema_version: Literal["vcos.scene-effect-plan.v1"] = "vcos.scene-effect-plan.v1"
    scene_id: str = Field(min_length=1)
    scene_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_asset_ref: str = Field(min_length=1)
    primary_asset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_asset_type: AIVisualRoute
    motion_projection_ref: str = Field(min_length=1)
    motion_projection_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    motion_pack_version: Literal["NativeMotionPack_v2"] = "NativeMotionPack_v2"
    motion_preset: str = Field(min_length=1)
    motion_parameters: CompiledMotionParameters
    transition_in: TransitionPreset
    transition_out: TransitionPreset
    transition_semantic_reason: TransitionSemanticReason
    presentation_start_ms: int = Field(ge=0)
    presentation_end_ms: int = Field(gt=0)
    contains_primary_visual_generation: Literal[False] = False
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_effect(self) -> "SceneEffectPlan":
        if self.presentation_end_ms <= self.presentation_start_ms:
            raise ValueError("SCENE_EFFECT_PRESENTATION_WINDOW_INVALID")
        if not _content_hash_matches(self):
            raise ValueError("SCENE_EFFECT_PLAN_HASH_MISMATCH")
        return self


class MotionDiversityReport(BaseModel):
    maximum_consecutive_same_motion_preset: int = Field(ge=1)
    maximum_consecutive_same_transition: int = Field(ge=1)
    maximum_consecutive_same_camera_direction: int = Field(ge=1)
    motion_preset_counts: dict[str, int]
    transition_counts: dict[str, int]
    camera_direction_counts: dict[str, int]
    gate: Literal["PASS", "BLOCK"]
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", frozen=True)


class MotionGateResult(BaseModel):
    gate: str = Field(min_length=1)
    verdict: Literal["PASS", "BLOCK"]
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", frozen=True)


class FFmpegEffectPlan(BaseModel):
    """Renderer-neutral, inspectable effect authority; never a raw filtergraph."""

    schema_version: Literal["vcos.ffmpeg-effect-plan.v1"] = "vcos.ffmpeg-effect-plan.v1"
    motion_pack_version: Literal["NativeMotionPack_v2"] = "NativeMotionPack_v2"
    motion_compiler_version: str = Field(min_length=1)
    motion_grammar_ref: str = Field(min_length=1)
    motion_grammar_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_duration_ms: int = Field(gt=0)
    scene_effect_plans: list[SceneEffectPlan] = Field(min_length=1)
    motion_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    diversity_report: MotionDiversityReport
    gate_results: list[MotionGateResult] = Field(min_length=1)
    production_eligible: bool
    contains_raw_filtergraph: Literal[False] = False
    effect_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def valid_plan(self) -> "FFmpegEffectPlan":
        ordered = self.scene_effect_plans
        if ordered[0].presentation_start_ms != 0:
            raise ValueError("EFFECT_PLAN_PRESENTATION_COVERAGE_START_INVALID")
        if ordered[-1].presentation_end_ms != self.canonical_duration_ms:
            raise ValueError("EFFECT_PLAN_PRESENTATION_COVERAGE_END_INVALID")
        if any(
            left.presentation_end_ms != right.presentation_start_ms
            for left, right in zip(ordered, ordered[1:])
        ):
            raise ValueError("EFFECT_PLAN_PRESENTATION_COVERAGE_GAP")
        expected = ai_visual_stable_hash(
            self.model_dump(mode="json", exclude={"effect_plan_hash"})
        )
        if self.effect_plan_hash != expected:
            raise ValueError("FFMPEG_EFFECT_PLAN_HASH_MISMATCH")
        passed = all(result.verdict == "PASS" for result in self.gate_results)
        if self.production_eligible != passed:
            raise ValueError("FFMPEG_EFFECT_PLAN_ELIGIBILITY_MISMATCH")
        return self


def seal_content_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a content-hashed payload for deterministic service constructors."""

    body = dict(payload)
    return {**body, "content_hash": ai_visual_stable_hash(body)}
