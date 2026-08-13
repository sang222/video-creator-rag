from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts.ai_visual_production import (
    AIVisualCapabilityProjection,
    AIVisualNarrationUnit,
    AIVisualPlanningPolicy,
    MotionIntentProjection,
    VideoMotionGrammar,
    VideoVisualStyleBible,
)
from app.services.ai_visual_planner import MotionIntentPlanner, UnifiedAIVisualPlanner
from app.services.native_motion_compiler import (
    MOTION_PACK,
    MOTION_PACK_VERSION,
    NATIVE_MOTION_PACK_V2,
    NativeMotionCompiler,
)


def _authorities():
    style = VideoVisualStyleBible.build(
        style_bible_id="style-motion",
        video_project_id="project-motion",
        package_id="package-motion",
        overall_visual_language="cinematic technical illustration",
        rendering_style="editorial 3D illustration",
        lighting="directional soft light",
        contrast="restrained",
        palette_guidance=["blue", "cyan"],
        materials=["glass", "metal"],
        camera_language="restrained",
        depth="layered",
        technical_illustration_language="physical semantic metaphor",
        human_depiction_rules=["no identity"],
        technology_depiction_rules=["no fake UI"],
        negative_aesthetic_constraints=["no clip-art"],
    )
    policy = AIVisualPlanningPolicy.production_default()
    capability = AIVisualCapabilityProjection.build(
        ai_image_production_ready=True,
        ai_video_production_ready=True,
        ai_video_budget_authorized=True,
        maximum_ai_image_assets=9,
        maximum_ai_video_scenes=2,
        provider_readiness_ref="artifact://providers/motion",
        budget_authority_ref="artifact://budget/motion",
    )
    units = [
        AIVisualNarrationUnit(
            narration_unit_id=f"unit-{index}",
            information_unit_ids=[f"info-{index}"],
            actual_start_ms=(index - 1) * 5_000,
            actual_end_ms=index * 5_000,
            spoken_text="Narration authority.",
            scene_meaning=f"Meaning {index}",
            visual_function=function,
            core_subject=f"subject {index}",
            secondary_subjects=[],
            action_or_relation="crosses a controlled boundary",
            environment="layered technical space",
            visual_goal="make the mechanism legible",
            composition_direction=composition,
            camera_direction="restrained camera",
            continuity_constraints=["same world"],
            motion_need=motion_need,
            factual_risk="LOW",
            importance="HIGH" if motion_need == "MOTION_REQUIRED" else "STANDARD",
            transition_semantic_reason=reason,
        )
        for index, (function, composition, motion_need, reason) in enumerate(
            (
                ("FOCUS", "subject centered", "STATIC_SUFFICIENT", "CONTINUATION"),
                ("COMPARE", "subject left third", "STATIC_SUFFICIENT", "CONTRAST"),
                ("PROCESS", "subject right third", "MOTION_REQUIRED", "NEW_STEP"),
            ),
            start=1,
        )
    ]
    compilation = UnifiedAIVisualPlanner().compile(
        style_bible=style,
        narration_units=units,
        capability=capability,
        policy=policy,
        canonical_duration_ms=15_000,
    )
    grammar = VideoMotionGrammar.production_default(
        grammar_id="grammar-motion",
        style_bible_hash=style.content_hash,
        maximum_aggressive_transition_rate=0.5,
    )
    planner = MotionIntentPlanner()
    projections = []
    for index, scene in enumerate(compilation.scenes):
        projections.append(
            planner.project(
                scene_plan=scene,
                style_bible=style,
                motion_grammar=grammar,
                primary_asset_ref=f"artifact://ai-asset/{scene.primary_asset_slot_id}",
                primary_asset_hash=f"{index + 1:x}" * 64,
                previous_projection=projections[-1] if projections else None,
                next_scene_plan=(
                    compilation.scenes[index + 1]
                    if index + 1 < len(compilation.scenes)
                    else None
                ),
            )
        )
    return grammar, projections


def test_native_motion_pack_v2_is_typed_ai_asset_configuration():
    required = {
        "kenburns_center_soft",
        "kenburns_subject_left",
        "kenburns_subject_right",
        "pushin_slow",
        "pushin_medium",
        "pullout_slow",
        "pan_left_slow",
        "pan_right_slow",
        "drift_up_soft",
        "drift_down_soft",
        "diagonal_drift_soft",
        "focus_push",
        "reveal_crop_horizontal",
        "reveal_crop_vertical",
        "video_intrinsic_preserve",
        "fade_soft",
        "dissolve",
        "cover_right",
        "reveal_down",
    }
    assert required <= set(NATIVE_MOTION_PACK_V2)
    assert all(
        item.pack_version == "NativeMotionPack_v2"
        for item in NATIVE_MOTION_PACK_V2.values()
    )
    assert "AI_VIDEO" not in NATIVE_MOTION_PACK_V2["pushin_slow"].supported_asset_types
    assert NATIVE_MOTION_PACK_V2["video_intrinsic_preserve"].supported_asset_types == [
        "AI_VIDEO"
    ]


def test_v2_compiler_produces_bounded_effect_plan_hashes_without_filtergraph():
    grammar, projections = _authorities()
    compiler = NativeMotionCompiler()
    effect = compiler.compile_effect_plan(projections, motion_grammar=grammar)

    assert effect.motion_pack_version == "NativeMotionPack_v2"
    assert effect.canonical_duration_ms == 15_000
    assert effect.contains_raw_filtergraph is False
    assert effect.production_eligible is True
    assert effect.diversity_report.gate == "PASS"
    assert all(
        scene.contains_primary_visual_generation is False
        for scene in effect.scene_effect_plans
    )
    assert all(
        scene.motion_parameters.content_hash for scene in effect.scene_effect_plans
    )
    assert all(
        0 <= scene.motion_parameters.crop_x_start <= 1
        for scene in effect.scene_effect_plans
    )
    assert effect.effect_plan_hash
    serialized = effect.model_dump_json().casefold()
    assert "zoompan=" not in serialized
    assert "xfade=" not in serialized
    assert "filter_complex" not in serialized


def test_ai_video_projection_preserves_intrinsic_motion_and_rejects_ken_burns():
    grammar, projections = _authorities()
    video = next(item for item in projections if item.asset_type == "AI_VIDEO")
    compiled = NativeMotionCompiler().compile_projection(video)
    assert compiled.motion_preset == "video_intrinsic_preserve"
    assert compiled.motion_parameters.preserve_intrinsic_motion is True
    assert (
        compiled.motion_parameters.start_scale
        == compiled.motion_parameters.end_scale
        == 1.0
    )

    payload = video.model_dump(mode="json")
    payload.update(camera_motion="PUSH_IN", motion_preset="pushin_slow", end_scale=1.05)
    payload["content_hash"] = "0" * 64
    with pytest.raises(
        ValidationError, match="MOTION_AI_VIDEO_INTRINSIC_MOTION_MUST_BE_PRESERVED"
    ):
        MotionIntentProjection.model_validate(payload)


def test_motion_diversity_gate_blocks_excessive_repetition():
    grammar, projections = _authorities()
    strict = VideoMotionGrammar.production_default(
        grammar_id="strict",
        style_bible_hash=grammar.style_bible_hash,
        maximum_consecutive_same_transition=1,
        maximum_consecutive_same_motion_preset=1,
        maximum_consecutive_same_camera_direction=1,
        maximum_aggressive_transition_rate=1.0,
    )
    repeated = []
    for projection in projections[:2]:
        payload = projection.model_dump(mode="json", exclude={"content_hash"})
        payload["motion_grammar_hash"] = strict.content_hash
        payload["motion_preset"] = "pushin_slow"
        payload["camera_motion"] = "PUSH_IN"
        payload["start_scale"] = 1.0
        payload["end_scale"] = 1.04
        payload["transition_out"] = "cut"
        from app.contracts.ai_visual_production import ai_visual_stable_hash

        repeated.append(
            MotionIntentProjection(
                **payload,
                content_hash=ai_visual_stable_hash(payload),
            )
        )
    effect = NativeMotionCompiler().compile_effect_plan(repeated, motion_grammar=strict)
    assert effect.production_eligible is False
    assert effect.diversity_report.gate == "BLOCK"
    assert "MOTION_REPETITION_EXCESSIVE" in effect.diversity_report.reason_codes


def test_legacy_v1_motion_symbols_and_entrypoint_remain_unchanged():
    assert MOTION_PACK_VERSION == "NativeMotionPack_v1"
    assert "kenburns_center_soft" in MOTION_PACK
    assert callable(NativeMotionCompiler().compile)
