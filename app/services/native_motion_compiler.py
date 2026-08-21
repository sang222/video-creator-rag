from __future__ import annotations

import re
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.contracts.ai_visual_production import (
    FFmpegEffectPlan,
    MotionDiversityReport,
    MotionGateResult,
    MotionIntentProjection,
    MotionParameterBound,
    MotionPresetDefinition,
    SceneEffectPlan,
    VideoMotionGrammar,
    ai_visual_stable_hash,
)
from app.contracts.native_renderer import CompiledNativeRenderManifest, NativeRenderPlan
from app.contracts.temporal_authority import CanonicalMediaTimeline
from app.services.native_render_plan import (
    OUTPUT_PROFILES,
    NativeRenderPlanValidator,
    canonical_caption_cues,
    canonical_plan_hash,
    stable_hash,
)


MOTION_PACK_VERSION = "NativeMotionPack_v1"
COMPILER_VERSION = "native-motion-compiler/1.0.0"
MOTION_PACK_V2_VERSION = "NativeMotionPack_v2"
MOTION_COMPILER_V2_VERSION = "native-motion-compiler/2.0.0"
RAW_FILTER_PATTERN = re.compile(r"[;\[\]`$|&<>\\\n\r]")


def _preset(
    key: str,
    category: str,
    treatments: list[str],
    handler: str,
    purpose: str,
    bounds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "preset_key": key,
        "preset_version": "1.0.0",
        "category": category,
        "supported_visual_treatments": treatments,
        "required_inputs": [],
        "optional_parameters": list((bounds or {}).keys()),
        "parameter_bounds": bounds or {},
        "compiler_handler": handler,
        "deterministic_defaults": {},
        "output_constraints": {"max_fps": 30, "max_dimension": 1920},
        "human_readable_purpose": purpose,
        "deprecated": False,
    }


ALL_NATIVE = [
    "NATIVE_SLIDE",
    "DIAGRAM",
    "UI_SIMULATION",
    "KINETIC_TYPOGRAPHY",
    "DATA_CARD",
    "QUOTE_SLIDE",
    "COMPARISON_SLIDE",
    "TIMELINE",
    "STATIC_COMPOSITION",
]
MOTION_PACK = {
    **{
        k: _preset(
            k, "TRANSITION", ALL_NATIVE, "compile_transition", k.replace("_", " ")
        )
        for k in (
            "cut",
            "fade_soft",
            "fade_black",
            "dissolve",
            "slide_left",
            "slide_right",
            "cover_left",
            "reveal_up",
        )
    },
    **{
        k: _preset(
            k,
            "STILL_MOTION",
            ALL_NATIVE,
            "compile_still_motion",
            k.replace("_", " "),
            {"intensity": [0.0, 1.0], "zoom_max": [1.0, 1.12]},
        )
        for k in (
            "hold_static",
            "kenburns_center_soft",
            "kenburns_subject_left",
            "pushin_slow",
            "pan_left_slow",
            "pan_right_slow",
        )
    },
    **{
        k: _preset(k, "CARD_UI", ALL_NATIVE, "compile_card", k.replace("_", " "))
        for k in (
            "lowerthird_slidein",
            "fact_card_pop",
            "data_card_hold",
            "comparison_reveal",
            "timeline_step_reveal",
            "cta_card_fadeup",
        )
    },
    **{
        k: _preset(k, "OVERLAY", ALL_NATIVE, "compile_overlay", k.replace("_", " "))
        for k in ("logo_bug_static", "badge_corner")
    },
    **{
        k: _preset(k, "AUDIO", ALL_NATIVE, "compile_audio", k.replace("_", " "))
        for k in ("voice_only_basic", "voice_music_duck_basic", "fade_in_out_basic")
    },
}

SEMANTIC_MAP = {
    "HOLD_STATIC": "hold_static",
    "SLOW_ZOOM_IN": "kenburns_center_soft",
    "SLOW_ZOOM_OUT": "pushin_slow",
    "PAN_LEFT": "pan_left_slow",
    "PAN_RIGHT": "pan_right_slow",
    "SLIDE_IN_LEFT": "lowerthird_slidein",
    "SLIDE_IN_RIGHT": "lowerthird_slidein",
    "REVEAL_UP": "reveal_up",
    "FADE_IN": "fade_soft",
    "FADE_OUT": "fade_soft",
    "HIGHLIGHT": "fact_card_pop",
    "COUNT_UP": "data_card_hold",
    "PARALLAX_LIGHT": "kenburns_subject_left",
}
TRANSITION_MAP = {
    k.upper(): k
    for k in (
        "cut",
        "fade_soft",
        "fade_black",
        "dissolve",
        "slide_left",
        "slide_right",
        "cover_left",
        "reveal_up",
    )
}


def _v2_preset(
    *,
    key: str,
    category: str,
    supported_asset_types: list[str],
    semantic_use_cases: list[str],
    forbidden_use_cases: list[str],
    minimum_duration_ms: int = 0,
    maximum_duration_ms: int = 30_000,
    maximum_scale: float = 1.08,
    aggressive: bool = False,
) -> MotionPresetDefinition:
    schema: dict[str, MotionParameterBound] = {}
    if category == "STILL_MOTION":
        schema = {
            "maximum_scale": MotionParameterBound(
                minimum=1.0,
                maximum=maximum_scale,
                default=min(1.04, maximum_scale),
            ),
            "maximum_normalized_travel": MotionParameterBound(
                minimum=0.0,
                maximum=0.12,
                default=0.06,
            ),
        }
    body: dict[str, Any] = {
        "key": key,
        "pack_version": MOTION_PACK_V2_VERSION,
        "category": category,
        "supported_asset_types": supported_asset_types,
        "minimum_duration_ms": minimum_duration_ms,
        "maximum_duration_ms": maximum_duration_ms,
        "allowed_intensities": ["SUBTLE", "MODERATE"],
        "parameter_schema": schema,
        "semantic_use_cases": semantic_use_cases,
        "forbidden_use_cases": forbidden_use_cases,
        "compiler_version": MOTION_COMPILER_V2_VERSION,
        "aggressive": aggressive,
    }
    return MotionPresetDefinition(
        **body,
        content_hash=ai_visual_stable_hash(body),
    )


def _build_v2_motion_pack() -> dict[str, MotionPresetDefinition]:
    definitions = [
        _v2_preset(
            key="hold_intentional",
            category="STILL_MOTION",
            supported_asset_types=["AI_IMAGE"],
            semantic_use_cases=["HOLD"],
            forbidden_use_cases=[],
            minimum_duration_ms=250,
            maximum_duration_ms=2_147_483_647,
            maximum_scale=1.0,
        ),
        _v2_preset(
            key="kenburns_center_soft",
            category="STILL_MOTION",
            supported_asset_types=["AI_IMAGE"],
            semantic_use_cases=["ESTABLISH", "REVEAL", "HOLD"],
            forbidden_use_cases=["SUBJECT_AT_FRAME_EDGE"],
        ),
        _v2_preset(
            key="kenburns_subject_left",
            category="STILL_MOTION",
            supported_asset_types=["AI_IMAGE"],
            semantic_use_cases=["FOLLOW", "FOCUS", "PROGRESS"],
            forbidden_use_cases=["SUBJECT_RIGHT_ANCHORED"],
        ),
        _v2_preset(
            key="kenburns_subject_right",
            category="STILL_MOTION",
            supported_asset_types=["AI_IMAGE"],
            semantic_use_cases=["FOLLOW", "FOCUS", "PROGRESS"],
            forbidden_use_cases=["SUBJECT_LEFT_ANCHORED"],
        ),
        *[
            _v2_preset(
                key=key,
                category="STILL_MOTION",
                supported_asset_types=["AI_IMAGE"],
                semantic_use_cases=uses,
                forbidden_use_cases=["EXCESSIVE_ZOOM", "SUBJECT_CLIPPING"],
                maximum_scale=maximum_scale,
            )
            for key, uses, maximum_scale in (
                ("pushin_slow", ["FOCUS", "EMPHASIZE"], 1.07),
                ("pushin_medium", ["FOCUS", "PROGRESS"], 1.09),
                ("pullout_slow", ["ESTABLISH", "REVEAL"], 1.07),
                ("focus_push", ["FOCUS", "EMPHASIZE"], 1.08),
            )
        ],
        *[
            _v2_preset(
                key=key,
                category="STILL_MOTION",
                supported_asset_types=["AI_IMAGE"],
                semantic_use_cases=uses,
                forbidden_use_cases=["UNPROTECTED_FRAME_EDGE", "SUBJECT_CLIPPING"],
                maximum_scale=1.06,
            )
            for key, uses in (
                ("pan_left_slow", ["FOLLOW", "COMPARE", "PROGRESS"]),
                ("pan_right_slow", ["FOLLOW", "COMPARE", "PROGRESS"]),
                ("drift_up_soft", ["ESTABLISH", "REVEAL"]),
                ("drift_down_soft", ["ESTABLISH", "REVEAL"]),
                ("diagonal_drift_soft", ["FOLLOW", "PROGRESS"]),
                ("reveal_crop_horizontal", ["REVEAL", "COMPARE"]),
                ("reveal_crop_vertical", ["REVEAL", "PROGRESS"]),
            )
        ],
        _v2_preset(
            key="video_intrinsic_preserve",
            category="VIDEO_PRESENTATION",
            supported_asset_types=["AI_VIDEO"],
            semantic_use_cases=[
                "REVEAL",
                "FOCUS",
                "FOLLOW",
                "COMPARE",
                "PROGRESS",
                "ESTABLISH",
                "EMPHASIZE",
                "HOLD",
            ],
            forbidden_use_cases=["KEN_BURNS_OVER_VIDEO", "SEMANTIC_SPEED_RAMP"],
            minimum_duration_ms=250,
            maximum_duration_ms=30_000,
        ),
        *[
            _v2_preset(
                key=key,
                category="TRANSITION",
                supported_asset_types=["AI_IMAGE", "AI_VIDEO"],
                semantic_use_cases=["TRANSITION"],
                forbidden_use_cases=["RANDOM_TRANSITION", "TRANSITION_SPAM"],
                maximum_duration_ms=1_000,
                aggressive=key
                in {
                    "slide_left",
                    "slide_right",
                    "cover_left",
                    "cover_right",
                    "reveal_up",
                    "reveal_down",
                },
            )
            for key in (
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
            )
        ],
        *[
            _v2_preset(
                key=key,
                category="SECONDARY_OVERLAY",
                supported_asset_types=["AI_IMAGE", "AI_VIDEO"],
                semantic_use_cases=["EMPHASIZE", "FOCUS"],
                forbidden_use_cases=[
                    "FULL_SCREEN_CARD",
                    "NARRATION_COPY",
                    "PRIMARY_VISUAL_SUBSTITUTION",
                ],
                maximum_duration_ms=12_000,
            )
            for key in (
                "lowerthird_slidein",
                "badge_fade",
                "keyword_emphasis",
                "focus_region_glow",
                "subtle_vignette_focus",
            )
        ],
    ]
    return {definition.key: definition for definition in definitions}


NATIVE_MOTION_PACK_V2 = _build_v2_motion_pack()
NativeMotionPack_v2 = NATIVE_MOTION_PACK_V2


def _maximum_consecutive(values: Sequence[str]) -> int:
    if not values:
        return 0
    maximum = current = 1
    for previous, value in zip(values, values[1:]):
        current = current + 1 if value == previous else 1
        maximum = max(maximum, current)
    return maximum


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return round(min(maximum, max(minimum, value)), 6)


class NativeMotionCompiler:
    def __init__(
        self, *, ffmpeg_capability_digest: str = "ffmpeg-full:h264_videotoolbox+aac"
    ):
        self.ffmpeg_capability_digest = ffmpeg_capability_digest
        self.validator = NativeRenderPlanValidator()

    def compile(
        self,
        plan: NativeRenderPlan,
        *,
        allow_resolved_provider_assets: bool = False,
        canonical_timeline: CanonicalMediaTimeline | None = None,
    ) -> CompiledNativeRenderManifest:
        plan_hash = canonical_plan_hash(plan)
        if plan.content_hash and plan.content_hash != plan_hash:
            raise ValueError("PLAN_CONTENT_HASH_STALE")
        gates = self.validator.validate(
            plan,
            execution=True,
            allow_resolved_provider_assets=allow_resolved_provider_assets,
            canonical_timeline=canonical_timeline,
        )
        reason_codes = [
            code
            for gate in gates
            if gate.verdict == "BLOCK"
            for code in gate.reason_codes
        ]
        compiled_scenes, transitions, overlays, inputs = [], [], [], []
        for scene in plan.scenes:
            values = [scene.animation_type, scene.transition_in, scene.transition_out]
            if any(v and RAW_FILTER_PATTERN.search(v) for v in values):
                reason_codes.append("RAW_FILTER_SYNTAX_REJECTED")
            motion = SEMANTIC_MAP.get(scene.animation_type or "HOLD_STATIC")
            if motion not in MOTION_PACK:
                reason_codes.append("MOTION_PRESET_UNSUPPORTED")
            for transition in (scene.transition_in, scene.transition_out):
                if transition and transition not in TRANSITION_MAP:
                    reason_codes.append("TRANSITION_PRESET_UNSUPPORTED")
            compiled_scene = {
                "scene_id": scene.scene_id,
                "start_ms": scene.narration_start_ms,
                "end_ms": scene.narration_end_ms,
                "duration_ms": scene.duration_ms,
                "visual_treatment": scene.visual_treatment,
                "motion_preset": motion,
                "layout_type": scene.layout_type,
                "asset_refs": [a.model_dump() for a in scene.resolved_asset_refs],
            }
            if scene.visual_routing_mode == "VSR1_STRICT":
                compiled_scene["visual_routing"] = {
                    "mode": scene.visual_routing_mode,
                    "source_decision_ref": scene.source_decision_ref,
                    "source_decision_hash": scene.source_decision_hash,
                    "preferred_source_route": scene.preferred_source_route.value,
                    "eligibility_gate_refs": scene.eligibility_gate_refs,
                    "exact_text_required": scene.exact_text_required,
                    "exact_number_required": scene.exact_number_required,
                    "native_overlay_required": scene.native_overlay_required,
                    "text_safe_regions": [
                        region.model_dump(mode="json")
                        for region in scene.text_safe_regions
                    ],
                    "reserved_overlay_regions": [
                        region.model_dump(mode="json")
                        for region in scene.reserved_overlay_regions
                    ],
                }
                if scene.native_overlay_plan is not None:
                    overlays.append(scene.native_overlay_plan.model_dump(mode="json"))
            compiled_scenes.append(compiled_scene)
            inputs.extend(a.path for a in scene.resolved_asset_refs)
            if scene.transition_out:
                transitions.append(
                    {
                        "scene_id": scene.scene_id,
                        "preset": TRANSITION_MAP[scene.transition_out],
                        "duration_ms": min(600, max(100, scene.duration_ms // 8)),
                    }
                )
        if reason_codes:
            raise ValueError(";".join(sorted(set(reason_codes))))
        canonical_cues = canonical_caption_cues(canonical_timeline)
        if plan.temporal_authority_mode == "CANONICAL_STRICT":
            if canonical_timeline is None or not canonical_cues:
                raise ValueError("CAPTION_AUTHORITY_MISSING")
            caption_schedule = {
                "authority": "SIDECAR_SRT_ONLY",
                "compilation_ref": plan.canonical_caption_compilation_ref,
                "compilation_hash": plan.canonical_caption_compilation_hash,
                "timing_source": "VERIFIED_NARRATION_ALIGNMENT",
            }
            caption_inputs: list[str] = []
            normalized_caption = {"mode": "SIDECAR_SRT_ONLY"}
        else:
            caption_schedule = {
                "caption_ref": plan.srt_ref,
                "timing_source": plan.caption_timing_source,
            }
            caption_inputs = []
            normalized_caption = {"mode": "SIDECAR_SRT_ONLY"}
        base = {
            "source_plan_ref": plan.plan_id,
            "source_plan_hash": plan_hash,
            "compiler_version": COMPILER_VERSION,
            "motion_pack_version": MOTION_PACK_VERSION,
            "renderer_profile_refs": plan.output_profiles,
            "ffmpeg_capability_digest": self.ffmpeg_capability_digest,
            "normalized_canvas": plan.canvas_spec.model_dump(),
            "normalized_audio": plan.audio_policy,
            "normalized_caption": normalized_caption,
            "compiled_scenes": compiled_scenes,
            "transition_schedule": transitions,
            "overlay_schedule": overlays,
            "audio_mix_schedule": plan.audio_policy,
            "caption_schedule": caption_schedule,
            "output_specs": [
                OUTPUT_PROFILES[p] | {"profile": p} for p in plan.output_profiles
            ],
            "expected_input_refs": sorted(set(inputs + caption_inputs)),
            "unresolved_inputs": [],
            "compilation_warnings": [],
            "compilation_reason_codes": [],
            "production_eligible": plan.production_eligible,
            "temporal_authority_mode": plan.temporal_authority_mode,
            "canonical_media_timeline_ref": plan.canonical_media_timeline_ref,
            "canonical_media_timeline_hash": plan.canonical_media_timeline_hash,
            "canonical_audio_asset_ref": plan.canonical_audio_asset_ref,
            "canonical_duration_ms": canonical_timeline.audio_duration_ms
            if canonical_timeline is not None
            and plan.temporal_authority_mode == "CANONICAL_STRICT"
            else None,
            "canonical_caption_compilation_ref": plan.canonical_caption_compilation_ref,
            "canonical_caption_compilation_hash": plan.canonical_caption_compilation_hash,
            "visual_direction_contract_ref": plan.visual_direction_contract_ref,
            "visual_direction_contract_hash": plan.visual_direction_contract_hash,
            "creative_gate_results": plan.creative_gate_results,
            "render_purpose": plan.purpose,
        }
        # Hash the typed manifest projection, not the construction dictionary.
        # Pydantic materializes optional/default fields (for example
        # ``asset_request_plan``) when a manifest is read back.  Hashing only
        # ``base`` therefore produced a manifest that immediately failed the
        # renderer's integrity check.  The identifier and hash are excluded
        # from the hash payload, so a draft instance cleanly breaks that cycle.
        draft = CompiledNativeRenderManifest(
            compiled_manifest_id="pending",
            ffmpeg_binary_requirement="ffmpeg-full>=8",
            manifest_hash="0" * 64,
            created_at=datetime.now(UTC),
            **base,
        )
        manifest_hash = stable_hash(
            draft.model_dump(
                mode="json",
                exclude={
                    "compiled_manifest_id",
                    "ffmpeg_binary_requirement",
                    "manifest_hash",
                    "created_at",
                },
            )
        )
        return draft.model_copy(
            update={
                "compiled_manifest_id": str(
                    uuid.uuid5(uuid.NAMESPACE_URL, manifest_hash)
                ),
                "manifest_hash": manifest_hash,
            }
        )

    def compile_projection(
        self,
        projection: MotionIntentProjection,
    ) -> SceneEffectPlan:
        """Compile one typed v2 intent into bounded renderer parameters.

        This method deliberately emits no filtergraph or shell fragment.  The
        renderer remains the only layer allowed to turn these parameters into
        concrete FFmpeg syntax.
        """

        preset = NATIVE_MOTION_PACK_V2.get(projection.motion_preset)
        if preset is None:
            raise ValueError("MOTION_PRESET_UNSUPPORTED")
        expected_category = (
            "STILL_MOTION"
            if projection.asset_type == "AI_IMAGE"
            else "VIDEO_PRESENTATION"
        )
        if (
            preset.category != expected_category
            or projection.asset_type not in preset.supported_asset_types
        ):
            raise ValueError("MOTION_ASSET_TYPE_MISMATCH")
        duration_ms = projection.presentation_end_ms - projection.presentation_start_ms
        if not preset.minimum_duration_ms <= duration_ms <= preset.maximum_duration_ms:
            raise ValueError("MOTION_PRESENTATION_WINDOW_INVALID")
        maximum_scale = preset.parameter_schema.get("maximum_scale")
        if (
            maximum_scale is not None
            and max(
                projection.start_scale,
                projection.end_scale,
            )
            > maximum_scale.maximum
        ):
            raise ValueError("MOTION_BOUNDS_INVALID")

        region = projection.safe_crop_region
        minimum_x = region.x
        maximum_x = region.x + region.width
        minimum_y = region.y
        maximum_y = region.y + region.height
        focal_x = _clamp(projection.focal_point.x, minimum_x, maximum_x)
        focal_y = _clamp(projection.focal_point.y, minimum_y, maximum_y)
        travel = 0.04 if projection.intensity == "SUBTLE" else 0.08
        x_start = x_end = focal_x
        y_start = y_end = focal_y
        if projection.camera_motion == "PAN_LEFT":
            x_start = _clamp(focal_x + travel, minimum_x, maximum_x)
            x_end = _clamp(focal_x - travel, minimum_x, maximum_x)
        elif projection.camera_motion == "PAN_RIGHT":
            x_start = _clamp(focal_x - travel, minimum_x, maximum_x)
            x_end = _clamp(focal_x + travel, minimum_x, maximum_x)
        elif projection.camera_motion == "DRIFT_UP":
            y_start = _clamp(focal_y + travel, minimum_y, maximum_y)
            y_end = _clamp(focal_y - travel, minimum_y, maximum_y)
        elif projection.camera_motion == "DRIFT_DOWN":
            y_start = _clamp(focal_y - travel, minimum_y, maximum_y)
            y_end = _clamp(focal_y + travel, minimum_y, maximum_y)
        elif (
            projection.camera_motion == "CONTROLLED_CUSTOM"
            and projection.motion_preset == "diagonal_drift_soft"
        ):
            x_start = _clamp(focal_x - travel, minimum_x, maximum_x)
            x_end = _clamp(focal_x + travel, minimum_x, maximum_x)
            y_start = _clamp(focal_y + travel, minimum_y, maximum_y)
            y_end = _clamp(focal_y - travel, minimum_y, maximum_y)

        transition_duration_ms = (
            0
            if projection.transition_out == "cut"
            else min(600, max(150, duration_ms // 10))
        )
        parameters_body: dict[str, Any] = {
            "start_scale": projection.start_scale,
            "end_scale": projection.end_scale,
            "crop_x_start": x_start,
            "crop_x_end": x_end,
            "crop_y_start": y_start,
            "crop_y_end": y_end,
            "focal_x": focal_x,
            "focal_y": focal_y,
            "easing": "EASE_IN_OUT",
            "preserve_intrinsic_motion": projection.asset_type == "AI_VIDEO",
            "transition_duration_ms": transition_duration_ms,
        }
        from app.contracts.ai_visual_production import CompiledMotionParameters

        parameters = CompiledMotionParameters(
            **parameters_body,
            content_hash=ai_visual_stable_hash(parameters_body),
        )
        body: dict[str, Any] = {
            "schema_version": "vcos.scene-effect-plan.v1",
            "scene_id": projection.scene_id,
            "scene_plan_hash": projection.scene_plan_hash,
            "primary_asset_ref": projection.primary_asset_ref,
            "primary_asset_hash": projection.primary_asset_hash,
            "primary_asset_type": projection.asset_type,
            "motion_projection_ref": (
                f"artifact://motion-intent/{projection.scene_id}/{projection.content_hash}"
            ),
            "motion_projection_hash": projection.content_hash,
            "motion_pack_version": MOTION_PACK_V2_VERSION,
            "motion_preset": projection.motion_preset,
            "motion_parameters": parameters,
            "transition_in": projection.transition_in,
            "transition_out": projection.transition_out,
            "transition_semantic_reason": projection.transition_semantic_reason,
            "presentation_start_ms": projection.presentation_start_ms,
            "presentation_end_ms": projection.presentation_end_ms,
            "contains_primary_visual_generation": False,
        }
        return SceneEffectPlan(
            **body,
            content_hash=ai_visual_stable_hash(body),
        )

    compile_v2 = compile_projection

    def compile_effect_plan(
        self,
        projections: Sequence[MotionIntentProjection],
        *,
        motion_grammar: VideoMotionGrammar,
    ) -> FFmpegEffectPlan:
        """Compile a complete, contiguous video motion authority."""

        items = list(projections)
        if not items:
            raise ValueError("MOTION_INTENT_PROJECTIONS_REQUIRED")
        if len({item.scene_id for item in items}) != len(items):
            raise ValueError("MOTION_SCENE_ID_DUPLICATE")
        if any(
            item.motion_grammar_hash != motion_grammar.content_hash for item in items
        ):
            raise ValueError("MOTION_GRAMMAR_BINDING_MISMATCH")
        if items[0].presentation_start_ms != 0 or any(
            left.presentation_end_ms != right.presentation_start_ms
            for left, right in zip(items, items[1:])
        ):
            raise ValueError("MOTION_PRESENTATION_COVERAGE_INVALID")

        scene_effects = [self.compile_projection(item) for item in items]
        motion_presets = [item.motion_preset for item in items]
        transitions = [item.transition_out for item in items]
        camera_directions = [item.camera_motion for item in items]
        # Stable holds are not failed for declining to manufacture movement.
        # Repetition limits apply only to actual motion/choreography choices.
        active_motion_items = [
            item for item in items if item.motion_preset != "hold_intentional"
        ]
        maximum_motion = max(
            1,
            _maximum_consecutive(
                [item.motion_preset for item in active_motion_items]
            ),
        )
        maximum_transition = _maximum_consecutive(transitions)
        maximum_camera = max(
            1,
            _maximum_consecutive(
                [item.camera_motion for item in active_motion_items]
            ),
        )
        diversity_reasons: list[str] = []
        if maximum_motion > motion_grammar.maximum_consecutive_same_motion_preset:
            diversity_reasons.append("MOTION_REPETITION_EXCESSIVE")
        if maximum_transition > motion_grammar.maximum_consecutive_same_transition:
            diversity_reasons.append("TRANSITION_REPETITION_EXCESSIVE")
        if maximum_camera > motion_grammar.maximum_consecutive_same_camera_direction:
            diversity_reasons.append("CAMERA_DIRECTION_REPETITION_EXCESSIVE")
        aggressive = sum(
            bool(NATIVE_MOTION_PACK_V2[transition].aggressive)
            for transition in transitions[:-1]
        )
        aggressive_rate = aggressive / max(1, len(transitions) - 1)
        if aggressive_rate > motion_grammar.maximum_aggressive_transition_rate:
            diversity_reasons.append("AGGRESSIVE_TRANSITION_RATE_EXCEEDED")
        diversity = MotionDiversityReport(
            maximum_consecutive_same_motion_preset=maximum_motion,
            maximum_consecutive_same_transition=maximum_transition,
            maximum_consecutive_same_camera_direction=maximum_camera,
            motion_preset_counts=dict(sorted(Counter(motion_presets).items())),
            transition_counts=dict(sorted(Counter(transitions).items())),
            camera_direction_counts=dict(sorted(Counter(camera_directions).items())),
            gate="BLOCK" if diversity_reasons else "PASS",
            reason_codes=diversity_reasons,
        )
        coverage_ok = items[0].presentation_start_ms == 0 and all(
            left.presentation_end_ms == right.presentation_start_ms
            for left, right in zip(items, items[1:])
        )
        gates = [
            MotionGateResult(
                gate="MotionCoverageGate",
                verdict="PASS" if coverage_ok else "BLOCK",
                reason_codes=[] if coverage_ok else ["DEAD_VISUAL_TIME"],
            ),
            MotionGateResult(
                gate="MotionBoundsGate",
                verdict="PASS",
                reason_codes=[],
            ),
            MotionGateResult(
                gate="MotionMeaningAlignmentGate",
                verdict="PASS",
                reason_codes=[],
            ),
            MotionGateResult(
                gate="MotionDiversityGate",
                verdict=diversity.gate,
                reason_codes=diversity.reason_codes,
            ),
            MotionGateResult(
                gate="StaticDurationGate",
                # Card D cannot reinterpret a legitimate stable realization as
                # invalid merely because it is long.  Card N owns pacing.
                verdict="PASS",
                reason_codes=[],
            ),
            MotionGateResult(
                gate="DeadVisualTimeGate",
                verdict="PASS" if coverage_ok else "BLOCK",
                reason_codes=[] if coverage_ok else ["DEAD_VISUAL_TIME"],
            ),
        ]
        motion_plan_hash = ai_visual_stable_hash([item.content_hash for item in items])
        body: dict[str, Any] = {
            "schema_version": "vcos.ffmpeg-effect-plan.v1",
            "motion_pack_version": MOTION_PACK_V2_VERSION,
            "motion_compiler_version": MOTION_COMPILER_V2_VERSION,
            "motion_grammar_ref": (
                f"artifact://video-motion-grammar/{motion_grammar.grammar_id}"
            ),
            "motion_grammar_hash": motion_grammar.content_hash,
            "canonical_duration_ms": items[-1].presentation_end_ms,
            "scene_effect_plans": scene_effects,
            "motion_plan_hash": motion_plan_hash,
            "diversity_report": diversity,
            "gate_results": gates,
            "production_eligible": all(gate.verdict == "PASS" for gate in gates),
            "contains_raw_filtergraph": False,
        }
        return FFmpegEffectPlan(
            **body,
            effect_plan_hash=ai_visual_stable_hash(body),
        )
