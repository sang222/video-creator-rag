from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.contracts.ai_visual_production import (
    AIVisualCapabilityProjection,
    AIVisualNarrationUnit,
    AIVisualPlanningPolicy,
    VideoMotionGrammar,
    VideoVisualStyleBible,
)
from app.services.ai_visual_planner import (
    AIImagePromptCompiler,
    AIVideoPromptCompiler,
    MotionIntentPlanner,
    UnifiedAIVisualPlanner,
)
from app.services.native_motion_compiler import NativeMotionCompiler


def _style_bible() -> VideoVisualStyleBible:
    return VideoVisualStyleBible.build(
        style_bible_id="style-001",
        video_project_id="project-001",
        package_id="package-001",
        overall_visual_language="cinematic semantic technical illustration",
        rendering_style="high-end editorial 3D illustration",
        lighting="soft directional studio lighting",
        contrast="restrained high clarity",
        palette_guidance=["deep blue", "cyan accent", "warm neutral"],
        materials=["matte metal", "translucent glass", "paper texture"],
        camera_language="restrained eye-level and isometric camera language",
        depth="layered foreground, mechanism plane, and environmental background",
        technical_illustration_language="physical metaphors for software boundaries",
        human_depiction_rules=[
            "no identifiable real person",
            "humans only when semantically relevant",
        ],
        technology_depiction_rules=[
            "conceptual systems, never fake product interfaces"
        ],
        negative_aesthetic_constraints=["no generic corporate clip-art"],
    )


def _capability(*, maximum_ai_image_assets: int = 9) -> AIVisualCapabilityProjection:
    return AIVisualCapabilityProjection.build(
        ai_image_production_ready=True,
        ai_video_production_ready=True,
        ai_video_budget_authorized=True,
        maximum_ai_image_assets=maximum_ai_image_assets,
        maximum_ai_video_scenes=3,
        provider_readiness_ref="artifact://provider-readiness/001",
        budget_authority_ref="artifact://budget/001",
    )


def _unit(
    unit_id: str,
    start_ms: int,
    end_ms: int,
    *,
    visual_function: str = "CONCEPT_MODEL",
    motion_need: str = "STATIC_SUFFICIENT",
    importance: str = "STANDARD",
    semantic_group_key: str | None = None,
    transition_semantic_reason: str = "CONTINUATION",
) -> AIVisualNarrationUnit:
    return AIVisualNarrationUnit(
        narration_unit_id=unit_id,
        information_unit_ids=[f"info-{unit_id}"],
        actual_start_ms=start_ms,
        actual_end_ms=end_ms,
        spoken_text="Qualified narration about a controlled information boundary.",
        scene_meaning="Information crosses a controlled boundary and becomes useful context.",
        visual_function=visual_function,
        core_subject="a structured information stream",
        secondary_subjects=["a controlled boundary", "an organized context space"],
        action_or_relation="raw fragments pass through a controlled boundary and organize into context",
        environment="a layered abstract technical environment",
        visual_goal="make the mechanism intuitively understandable without text or fake UI",
        composition_direction="subject on the left third with meaningful environment to the right",
        camera_direction="restrained eye-level camera",
        continuity_constraints=["preserve the same translucent material system"],
        motion_need=motion_need,
        factual_risk="LOW",
        importance=importance,
        transition_semantic_reason=transition_semantic_reason,
        semantic_group_key=semantic_group_key,
    )


def _plan(*units: AIVisualNarrationUnit, maximum_ai_image_assets: int = 9):
    style = _style_bible()
    policy = AIVisualPlanningPolicy.production_default(
        maximum_ai_image_presentation_ms=12_000
    )
    compilation = UnifiedAIVisualPlanner().compile(
        style_bible=style,
        narration_units=list(units),
        capability=_capability(maximum_ai_image_assets=maximum_ai_image_assets),
        policy=policy,
        canonical_duration_ms=units[-1].actual_end_ms,
    )
    return style, policy, compilation


def test_long_semantic_block_splits_presentation_windows_and_reuses_one_asset_slot():
    style, policy, compilation = _plan(
        _unit(
            "unit-001",
            0,
            30_000,
            semantic_group_key="controlled-boundary",
        )
    )

    assert [
        scene.presentation_end_ms - scene.presentation_start_ms
        for scene in compilation.scenes
    ] == [10_000, 10_000, 10_000]
    assert compilation.unique_asset_slot_count == 1
    assert compilation.unique_ai_image_asset_slot_count == 1
    assert compilation.reused_presentation_window_count == 2
    owner, *reused = compilation.scenes
    assert owner.reuses_primary_asset_from_scene_id is None
    assert all(
        scene.reuses_primary_asset_from_scene_id == owner.scene_id for scene in reused
    )
    assert all(
        scene.primary_asset_slot_id == owner.primary_asset_slot_id
        for scene in compilation.scenes
    )
    assert compilation.scenes[0].presentation_start_ms == 0
    assert compilation.scenes[-1].presentation_end_ms == 30_000
    assert style.content_hash
    assert policy.content_hash
    grammar = VideoMotionGrammar.production_default(
        grammar_id="reuse-grammar",
        style_bible_hash=style.content_hash,
    )
    with pytest.raises(
        ValueError, match="AI_IMAGE_REUSED_WINDOW_HAS_NO_GENERATION_PROMPT"
    ):
        AIImagePromptCompiler().compile(
            scene_plan=reused[0],
            style_bible=style,
            motion_grammar=grammar,
        )


def test_group_first_split_preserves_ordered_source_partition_and_unit_bindings():
    _, policy, compilation = _plan(
        _unit("unit-001", 0, 7_000, semantic_group_key="one-world"),
        _unit("unit-002", 7_000, 14_000, semantic_group_key="one-world"),
        _unit("unit-003", 14_000, 21_000, semantic_group_key="one-world"),
    )

    assert [
        (scene.actual_start_ms, scene.actual_end_ms) for scene in compilation.scenes
    ] == [(0, 10_500), (10_500, 21_000)]
    assert [scene.narration_unit_ids for scene in compilation.scenes] == [
        ["unit-001", "unit-002"],
        ["unit-002", "unit-003"],
    ]
    assert compilation.unique_ai_image_asset_slot_count == 1
    assert compilation.reused_presentation_window_count == 1
    assert all(
        scene.presentation_end_ms - scene.presentation_start_ms
        <= policy.maximum_ai_image_presentation_ms
        for scene in compilation.scenes
    )


def test_repeated_contrast_uses_grammar_safe_transition_diversity():
    style, _, compilation = _plan(
        *(
            _unit(
                f"contrast-{index}",
                (index - 1) * 3_000,
                index * 3_000,
                visual_function="COMPARISON",
                semantic_group_key=f"contrast-group-{index}",
                transition_semantic_reason="CONTRAST",
            )
            for index in range(1, 9)
        )
    )
    grammar = VideoMotionGrammar.production_default(
        grammar_id="contrast-grammar",
        style_bible_hash=style.content_hash,
    )
    planner = MotionIntentPlanner()
    projections = []
    for index, scene in enumerate(compilation.scenes):
        projections.append(
            planner.project(
                scene_plan=scene,
                style_bible=style,
                motion_grammar=grammar,
                primary_asset_ref=f"artifact://ai-image/{index}",
                primary_asset_hash=f"{index + 1:064x}",
                previous_projection=projections[-1] if projections else None,
                next_scene_plan=(
                    compilation.scenes[index + 1]
                    if index + 1 < len(compilation.scenes)
                    else None
                ),
            )
        )

    effect_plan = NativeMotionCompiler().compile_effect_plan(
        projections,
        motion_grammar=grammar,
    )
    assert effect_plan.production_eligible is True
    assert effect_plan.diversity_report.gate == "PASS"
    assert (
        effect_plan.diversity_report.maximum_consecutive_same_transition
        <= grammar.maximum_consecutive_same_transition
    )


def test_route_selection_is_semantic_and_motion_required_never_downgrades():
    _, _, compilation = _plan(
        _unit("static", 0, 6_000, motion_need="STATIC_SUFFICIENT"),
        _unit(
            "motion",
            6_000,
            14_000,
            visual_function="PROCESS",
            motion_need="MOTION_REQUIRED",
            importance="HIGH",
        ),
    )
    assert [scene.production_route for scene in compilation.scenes] == [
        "AI_IMAGE",
        "AI_VIDEO",
    ]
    assert compilation.unique_ai_image_asset_slot_count == 1
    assert compilation.unique_ai_video_asset_slot_count == 1


def test_motion_required_blocks_when_video_authority_is_unavailable():
    style = _style_bible()
    capability = AIVisualCapabilityProjection.build(
        ai_image_production_ready=True,
        ai_video_production_ready=False,
        ai_video_budget_authorized=False,
        maximum_ai_image_assets=9,
        maximum_ai_video_scenes=0,
        provider_readiness_ref="artifact://provider-readiness/blocked",
        budget_authority_ref="artifact://budget/blocked",
    )
    with pytest.raises(
        ValueError, match="AI_VIDEO_MOTION_REQUIRED_AUTHORITY_UNAVAILABLE"
    ):
        UnifiedAIVisualPlanner().compile(
            style_bible=style,
            narration_units=[
                _unit(
                    "required",
                    0,
                    8_000,
                    visual_function="PROCESS",
                    motion_need="MOTION_REQUIRED",
                )
            ],
            capability=capability,
            policy=AIVisualPlanningPolicy.production_default(),
            canonical_duration_ms=8_000,
        )


def test_unique_image_asset_budget_blocks_incompatible_scene_slots():
    with pytest.raises(ValueError, match="AI_IMAGE_UNIQUE_ASSET_SLOT_BUDGET_EXCEEDED"):
        _plan(
            _unit("one", 0, 4_000),
            _unit("two", 4_000, 8_000, visual_function="DATA"),
            maximum_ai_image_assets=1,
        )


def test_prompt_compilers_are_deterministic_negative_bound_and_motion_codesigned():
    style, _, compilation = _plan(
        _unit("image", 0, 6_000),
        _unit(
            "video",
            6_000,
            14_000,
            visual_function="PROCESS",
            motion_need="MOTION_REQUIRED",
            importance="HERO",
        ),
    )
    grammar = VideoMotionGrammar.production_default(
        grammar_id="grammar-001",
        style_bible_hash=style.content_hash,
    )
    image_scene, video_scene = compilation.scenes
    motion = MotionIntentPlanner().project(
        scene_plan=image_scene,
        style_bible=style,
        motion_grammar=grammar,
        primary_asset_ref="artifact://ai-image/001",
        primary_asset_hash="1" * 64,
        next_scene_plan=video_scene,
    )
    compiler = AIImagePromptCompiler()
    prompt_a = compiler.compile(
        scene_plan=image_scene,
        style_bible=style,
        motion_grammar=grammar,
        motion_projection=motion,
    )
    prompt_b = compiler.compile(
        scene_plan=image_scene,
        style_bible=style,
        motion_grammar=grammar,
        motion_projection=motion,
    )
    assert prompt_a == prompt_b
    assert "left third" in prompt_a.motion_safe_composition
    assert (
        "slow rightward camera pan" in prompt_a.motion_safe_composition
        or "push-in" in prompt_a.motion_safe_composition
    )
    assert "no PowerPoint" in prompt_a.negative_constraints
    assert prompt_a.provider_call_made is False

    video_prompt = AIVideoPromptCompiler().compile(
        scene_plan=video_scene,
        style_bible=style,
    )
    assert video_prompt.intrinsic_motion_required is True
    assert video_prompt.provider_audio_usage_policy == "DISCARD"
    assert video_prompt.target_duration_ms == 8_000
    assert video_prompt.provider_generation_duration_ms == 8_000
    assert "no Ken Burns effect" in video_prompt.negative_constraints
    assert video_prompt.provider_call_made is False


def test_video_prompt_separates_long_semantic_window_from_fixed_provider_effect():
    style, _, compilation = _plan(
        _unit(
            "long-video",
            0,
            12_620,
            visual_function="PROCESS",
            motion_need="MOTION_REQUIRED",
            importance="HERO",
        )
    )
    assert len(compilation.scenes) == 2
    assert compilation.unique_ai_video_asset_slot_count == 2
    assert all(
        scene.reuses_primary_asset_from_scene_id is None
        and scene.presentation_end_ms - scene.presentation_start_ms <= 8_000
        for scene in compilation.scenes
    )
    prompts = [
        AIVideoPromptCompiler().compile(scene_plan=scene, style_bible=style)
        for scene in compilation.scenes
    ]
    assert [prompt.target_duration_ms for prompt in prompts] == [6_310, 6_310]
    assert all(prompt.provider_generation_duration_ms == 8_000 for prompt in prompts)


def test_scene_plan_rejects_non_ai_route_and_hash_tampering():
    _, _, compilation = _plan(_unit("image", 0, 6_000))
    payload = compilation.scenes[0].model_dump(mode="json")
    payload["production_route"] = "NATIVE_GRAPHIC"
    with pytest.raises(ValidationError):
        type(compilation.scenes[0]).model_validate(payload)

    payload = compilation.scenes[0].model_dump(mode="json")
    payload["scene_meaning"] = "tampered"
    with pytest.raises(ValidationError, match="AI_VISUAL_SCENE_PLAN_HASH_MISMATCH"):
        type(compilation.scenes[0]).model_validate(payload)
