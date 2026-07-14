from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from app.contracts.asset_acquisition import AssetRequest, ParsedStockCandidate
from app.contracts.visual_direction import (
    SceneVisualIntent,
    VeoDurationFitThresholds,
    VisualAssetEvidence,
    VisualDirectionContract,
    VisualRankingWeights,
    VisualRiskPenalties,
    VisualScoreThresholds,
)
from app.services.creative_quality_policy import CreativeQualityPolicyCatalog
from app.services.native_render_plan import stable_hash
from app.services.pexels_query_planner import PexelsQueryPlanner
from app.services.stock_candidate_ranker import StockCandidateRanker
from app.services.veo_prompt_compiler import VeoFixedDurationPlanner, VeoPromptCompiler
from app.services.visual_direction import (
    AssetAdjacencyGate,
    SceneSemanticMatchGate,
    VisualContinuityGate,
    VisualDirectionCompiler,
    VisualEvaluationService,
    detect_hard_visual_conflicts,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def policy_snapshot() -> dict:
    return CreativeQualityPolicyCatalog(ROOT / "config/creative_quality_policy_catalog.yaml").approved_snapshot(
        "small-team-ai"
    )


@pytest.fixture
def visual_direction(policy_snapshot: dict) -> VisualDirectionContract:
    return _compile_direction(policy_snapshot)


def _compile_direction(policy: dict) -> VisualDirectionContract:
    return VisualDirectionCompiler().compile(
        channel_id="fixture-channel",
        project_id="fixture-project",
        format_identity_ref="artifact://format/fixture",
        format_identity_hash=stable_hash("format-fixture"),
        visual_strategy_profile_ref="artifact://visual-strategy/fixture",
        visual_strategy_profile_hash=stable_hash("visual-strategy-fixture"),
        policy=policy,
        adjacent_scene_constraints=["avoid abrupt provider-source cuts", "retain neutral-warm palette"],
    )


def _ranking_policy(policy: dict) -> dict:
    return {
        "weights": VisualRankingWeights.from_policy(policy),
        "risk_penalties": VisualRiskPenalties.from_policy(policy),
        "thresholds": VisualScoreThresholds.from_policy(policy),
    }


def _asset_request(**changes) -> AssetRequest:
    payload = {
        "request_id": "asset-stock-cqr1c",
        "scene_id": "scene-stock-cqr1c",
        "source_segment_ids": ["segment-1"],
        "purpose": "GROUNDED_DOCUMENTARY_CONTEXT",
        "requested_role": "SUPPORTING_STOCK",
        "semantic_visual_intent": "editor reviews a media production workflow at a workstation",
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1920x1080",
        "minimum_duration_seconds": 4,
        "maximum_duration_seconds": 8,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_RECURRING_HOST",
        "logo_text_policy": "REJECT_VISIBLE_LOGO_OR_EMBEDDED_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["NATIVE_VISUAL", "SUPPORTING_STOCK"],
        "projected_cost_class": "LOW",
        "human_review_required": True,
    }
    payload.update(changes)
    return AssetRequest(**payload, request_hash=stable_hash(payload))


def _candidate(candidate_id: str, **changes) -> ParsedStockCandidate:
    payload = {
        "candidate_id": candidate_id,
        "provider_asset_id": candidate_id.removeprefix("pexels-"),
        "source_page_url": f"https://www.pexels.com/video/{candidate_id}",
        "creator_name": "Fixture Creator",
        "creator_url": "https://www.pexels.com/@fixture",
        "width": 1920,
        "height": 1080,
        "duration_seconds": 8,
        "tags": ["editor", "media", "production", "workflow", "workstation"],
        "description": "editor reviews a media production workflow at a workstation",
        "composition": "CENTER_SAFE",
        "logo_or_text_present": False,
        "identifiable_person_present": False,
        "brand_or_trademark_present": False,
        "motion_suitability": 0.70,
        "channel_identity_fit": 0.70,
        "environment_type": "modern-real-world-workspace",
        "industry_context": "media-production-operations",
        "lighting_direction": "natural_or_soft_motivated",
        "lighting_temperature": "neutral_warm",
        "palette": ["neutral_warm", "charcoal", "soft_blue_accent"],
        "shot_scale": "medium_and_detail",
        "camera_movement": "restrained",
        "motion_intensity": "low_to_medium",
        "semantic_relevance_score": 0.80,
        "visual_direction_fit_score": 0.70,
        "previous_scene_continuity_score": 0.70,
        "next_scene_continuity_score": 0.70,
        "crop_safety_score": 0.70,
        "technical_quality_score": 0.70,
        "originality_score": 0.70,
    }
    payload.update(changes)
    return ParsedStockCandidate(**payload)


def test_visual_direction_requires_injected_policy_and_is_deterministic_provider_neutral(policy_snapshot):
    with pytest.raises(ValueError, match="VISUAL_DIRECTION_POLICY_REQUIRED"):
        _compile_direction({})

    first = _compile_direction(policy_snapshot)
    second = _compile_direction(deepcopy(policy_snapshot))

    assert first == second
    assert first.content_hash == second.content_hash
    assert first.treatment_mode == "grounded-modern-documentary"
    assert first.palette == ["neutral_warm", "charcoal", "soft_blue_accent"]
    provider_tokens = ("pexels", "veo", "google", "provider")
    assert not any(token in field.lower() for field in VisualDirectionContract.model_fields for token in provider_tokens)


def test_visual_thresholds_are_loaded_from_injected_catalog_snapshot(policy_snapshot):
    thresholds = VisualScoreThresholds.from_policy(policy_snapshot)
    weights = VisualRankingWeights.from_policy(policy_snapshot)
    risk_penalties = VisualRiskPenalties.from_policy(policy_snapshot)
    duration_fit = VeoDurationFitThresholds.from_policy(policy_snapshot)

    assert thresholds == VisualScoreThresholds(
        semantic_pass_min=0.78,
        semantic_review_min=0.68,
        adjacency_pass_min=0.70,
        adjacency_review_min=0.58,
        hard_conflicts_block=True,
        cross_provider_cut_requires_both_scores_pass=True,
    )
    assert sum(weights.model_dump().values()) == pytest.approx(0.97)
    assert risk_penalties.total_cap == 0.30
    assert duration_fit.approved_output_duration_seconds == 8.0
    assert duration_fit.speed_change_allowed is False and duration_fit.loop_allowed is False


def test_contextual_pexels_plan_consumes_direction_timing_crop_adjacency_and_reuse(visual_direction):
    intent = SceneVisualIntent(
        scene_id="scene-stock-cqr1c",
        semantic_intent="editor reviews a media production workflow at a workstation",
        subject_action="editor calmly reviews an editing timeline",
        target_duration_seconds=7.25,
        aspect_ratio="16:9",
        crop_safety_required=True,
        previous_scene_summary="restrained native workflow diagram with neutral warm palette",
        next_scene_summary="calm documentary detail of the media pipeline",
    )
    planner = PexelsQueryPlanner()

    first = planner.plan(
        _asset_request(),
        visual_direction=visual_direction,
        visual_direction_ref="artifact://visual-direction/fixture",
        scene_intent=intent,
        locale="en-US",
        per_page=24,
        asset_reuse_history=["pexels-9", "pexels-2", "pexels-9"],
    )
    second = planner.plan(
        _asset_request(),
        visual_direction=visual_direction,
        visual_direction_ref="artifact://visual-direction/fixture",
        scene_intent=intent,
        locale="en-US",
        per_page=24,
        asset_reuse_history=["pexels-9", "pexels-2", "pexels-9"],
    )

    assert first == second
    assert first.planner_version == "pexels-query-planner/v2.0.0"
    assert first.visual_direction_hash == visual_direction.content_hash
    assert first.visual_direction_ref == "artifact://visual-direction/fixture"
    assert (first.target_duration_seconds, first.aspect_ratio, first.crop_safety_required) == (7.25, "16:9", True)
    assert first.previous_scene_summary == intent.previous_scene_summary
    assert first.next_scene_summary == intent.next_scene_summary
    assert first.asset_reuse_history == ["pexels-2", "pexels-9"]
    assert first.orientation == "landscape" and first.locale == "en-US" and first.per_page == 24
    assert all("workplace b roll" not in query for query in first.queries)
    assert all(query.isascii() and len(query) <= 80 for query in first.queries)


def test_contextual_pexels_plan_blocks_cliches_and_invalid_locale(visual_direction):
    with pytest.raises(ValueError, match="PEXELS_PROHIBITED_CLICHE"):
        PexelsQueryPlanner().plan(
            _asset_request(semantic_visual_intent="meaningless office laptop shot"),
            visual_direction=visual_direction,
        )
    with pytest.raises(ValueError, match="PEXELS_LOCALE_UNSUPPORTED"):
        PexelsQueryPlanner().plan(_asset_request(), visual_direction=visual_direction, locale="english")


def test_contextual_stock_ranking_uses_exact_weights_and_independent_semantic_gate(
    visual_direction, policy_snapshot
):
    relevant = _candidate("pexels-relevant")
    attractive_but_wrong = _candidate(
        "pexels-attractive-wrong",
        tags=["abstract", "neon", "city"],
        description="visually attractive but unrelated abstract city",
        semantic_relevance_score=0.67,
        visual_direction_fit_score=1.0,
        previous_scene_continuity_score=1.0,
        next_scene_continuity_score=1.0,
        crop_safety_score=1.0,
        motion_suitability=1.0,
        technical_quality_score=1.0,
        originality_score=1.0,
    )

    result = StockCandidateRanker().rank(
        _asset_request(),
        [attractive_but_wrong, relevant],
        visual_direction=visual_direction,
        previous_scene="restrained neutral warm native workflow graphic",
        next_scene="calm media operations detail",
        **_ranking_policy(policy_snapshot),
    )
    repeated = StockCandidateRanker().rank(
        _asset_request(),
        [attractive_but_wrong, relevant],
        visual_direction=visual_direction,
        previous_scene="restrained neutral warm native workflow graphic",
        next_scene="calm media operations detail",
        **_ranking_policy(policy_snapshot),
    )

    assert result == repeated
    assert result.ranking_weights == VisualRankingWeights.from_policy(policy_snapshot).model_dump()
    assert result.ranking_risk_penalties == VisualRiskPenalties.from_policy(policy_snapshot).model_dump()
    score_by_id = {item.candidate_id: item for item in result.candidate_scores}
    expected_relevant = sum(
        score_by_id["pexels-relevant"].dimensions[key] * weight
        for key, weight in result.ranking_weights.items()
    )
    assert score_by_id["pexels-relevant"].total_score == pytest.approx(expected_relevant)
    assert score_by_id["pexels-attractive-wrong"].total_score > score_by_id["pexels-relevant"].total_score
    assert result.selected_candidate_id == "pexels-relevant"
    assert result.ranking_verdict == "PASS"
    assert "INDEPENDENT_VISUAL_GATE_BLOCKED_CANDIDATE" in result.ranking_reason_codes


def test_stock_borderline_routes_review_and_semantic_mismatch_blocks(visual_direction, policy_snapshot):
    borderline = _candidate(
        "pexels-borderline",
        semantic_relevance_score=0.72,
        visual_direction_fit_score=0.65,
        previous_scene_continuity_score=0.65,
        next_scene_continuity_score=0.65,
    )
    review = StockCandidateRanker().rank(
        _asset_request(),
        [borderline],
        visual_direction=visual_direction,
        **_ranking_policy(policy_snapshot),
    )

    assert review.selected_candidate_id == "pexels-borderline"
    assert review.ranking_verdict == "REVIEW_REQUIRED"
    assert review.selection_requires_human_review is True

    mismatch = _candidate("pexels-mismatch", semantic_relevance_score=0.67)
    blocked = StockCandidateRanker().rank(
        _asset_request(),
        [mismatch],
        visual_direction=visual_direction,
        **_ranking_policy(policy_snapshot),
    )

    assert blocked.selected_candidate_id is None
    assert blocked.ranking_verdict == "BLOCK"
    assert "SCENE_SEMANTIC_MISMATCH" in blocked.candidate_scores[0].reason_codes


def test_stock_hard_risks_reject_independently_including_no_character(policy_snapshot):
    no_character_policy = deepcopy(policy_snapshot)
    no_character_policy["visual_language_policy"]["human_presence_policy"] = "NO_CHARACTER"
    direction = _compile_direction(no_character_policy)
    logo = _candidate("pexels-logo", logo_or_text_present=True)
    person = _candidate("pexels-person", identifiable_person_present=True)

    result = StockCandidateRanker().rank(
        _asset_request(),
        [logo, person],
        visual_direction=direction,
        **_ranking_policy(no_character_policy),
    )
    rejected = {item.candidate_id: item.reason_codes for item in result.rejected_candidates}

    assert "UNWANTED_LOGO_OR_TEXT" in rejected["pexels-logo"]
    assert "NO_CHARACTER_CONFLICT" in rejected["pexels-person"]
    assert result.candidate_scores == [] and result.selected_candidate_id is None


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0.78, "PASS"), (0.70, "REVIEW_REQUIRED"), (0.67, "BLOCK")],
)
def test_scene_semantic_gate_thresholds(score, expected, policy_snapshot):
    thresholds = VisualScoreThresholds.from_policy(policy_snapshot)
    assert SceneSemanticMatchGate(thresholds).evaluate(score).verdict == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [(0.70, "PASS"), (0.60, "REVIEW_REQUIRED"), (0.57, "BLOCK")],
)
def test_visual_continuity_gate_thresholds(score, expected, policy_snapshot):
    thresholds = VisualScoreThresholds.from_policy(policy_snapshot)
    assert VisualContinuityGate(thresholds).evaluate(score).verdict == expected


def test_adjacency_gate_blocks_hard_conflict_and_cross_provider_requires_semantic_pass(policy_snapshot):
    thresholds = VisualScoreThresholds.from_policy(policy_snapshot)
    hard = AssetAdjacencyGate(thresholds).evaluate(0.95, hard_conflicts=["CAMERA_LANGUAGE_JOLT"])
    cross_provider = AssetAdjacencyGate(thresholds).evaluate(
        0.90,
        cross_provider_cut=True,
        semantic_verdict="REVIEW_REQUIRED",
    )

    assert hard.verdict == "BLOCK"
    assert hard.hard_conflict_reasons == ["CAMERA_LANGUAGE_JOLT"]
    assert cross_provider.verdict == "REVIEW_REQUIRED"
    assert "CROSS_PROVIDER_CUT_REQUIRES_SEMANTIC_PASS" in cross_provider.reason_codes


def test_hard_conflict_detection_and_visual_evaluation_persist_evidence(policy_snapshot):
    no_character_policy = deepcopy(policy_snapshot)
    no_character_policy["visual_language_policy"]["human_presence_policy"] = "NO_CHARACTER"
    direction = _compile_direction(no_character_policy)
    evidence = VisualAssetEvidence(
        scene_id="scene-1",
        asset_ref="asset-1",
        source_class="SUPPORTING_STOCK",
        semantic_description="a person endorses a fake software interface",
        lighting_temperature="cool",
        camera_movement="aggressive shaky whip pan",
        motion_intensity="high",
        logo_or_text_present=True,
        identifiable_person_present=True,
        fake_ui_used_as_evidence=True,
        implies_endorsement=True,
        representative_still_refs=["artifact://still/asset-1/001"],
    )
    conflicts = detect_hard_visual_conflicts(asset=evidence, contract=direction)

    assert {
        "BRAND_OR_LOGO_RISK",
        "NO_CHARACTER_CONFLICT",
        "FAKE_UI_USED_AS_FACTUAL_EVIDENCE",
        "STOCK_IMPLIES_ENDORSEMENT",
        "STRONG_COLOR_TEMPERATURE_CONFLICT",
        "CAMERA_LANGUAGE_JOLT",
        "MOTION_INTENSITY_CONFLICT",
    } <= set(conflicts)

    evaluation = VisualEvaluationService(VisualScoreThresholds.from_policy(no_character_policy)).evaluate_scene(
        scene_id=evidence.scene_id,
        asset_ref=evidence.asset_ref,
        semantic_score=0.90,
        visual_direction_score=0.90,
        previous_adjacency_score=0.90,
        next_adjacency_score=0.90,
        current_source_class="SUPPORTING_STOCK",
        previous_source_class="NATIVE_VISUAL",
        hard_conflict_reasons=conflicts,
        top_candidate_ranking=[{"candidate_id": "asset-1", "score": 0.91}],
        selected_rationale="highest eligible deterministic candidate",
        representative_still_refs=evidence.representative_still_refs,
    )

    assert evaluation.result == "BLOCK"
    assert len(evaluation.gate_results) == 3
    assert evaluation.top_candidate_ranking[0]["candidate_id"] == "asset-1"
    assert evaluation.representative_still_refs == ["artifact://still/asset-1/001"]
    assert evaluation.content_hash


def test_veo_prompt_compiler_has_stable_anatomy_contract_negatives_and_no_provider_call(visual_direction):
    intent = SceneVisualIntent(
        scene_id="scene-hero",
        semantic_intent="a production bottleneck transforms into a clear coordinated workflow",
        subject_action="interlocking production stages settle into one clear coordinated flow",
        target_duration_seconds=8,
        previous_scene_summary="restrained native diagram of disconnected steps",
        next_scene_summary="grounded documentary view of an editor at work",
        camera_angle="slightly elevated",
        shot_size="medium-wide",
    )
    compiler = VeoPromptCompiler()
    first = compiler.compile(
        intent,
        visual_direction,
        channel_provider_policy={
            "character_policy_mode": "NO_CHARACTER",
            "negative_constraints": ["legible screen text"],
        },
    )
    second = compiler.compile(
        intent,
        visual_direction,
        channel_provider_policy={
            "character_policy_mode": "NO_CHARACTER",
            "negative_constraints": ["legible screen text"],
        },
    )

    assert first == second
    labels = [
        "Subject/action:",
        "Environment/industry context:",
        "Realism/treatment:",
        "Lighting/time of day:",
        "Camera angle and shot size:",
        "Camera movement:",
        "Framing/focal style:",
        "Motion intensity:",
        "Continuity hint:",
        "Negative constraints:",
    ]
    assert [first.prompt.index(label) for label in labels] == sorted(first.prompt.index(label) for label in labels)
    assert first.visual_direction_hash == visual_direction.content_hash
    assert first.prompt_hash == hashlib.sha256(first.prompt.encode("utf-8")).hexdigest()
    assert first.provider_call_made is False
    assert {"people", "person", "face", "logo", "legible screen text"} <= set(first.negative_constraints)
    assert set(visual_direction.prohibited_cliches) <= set(first.negative_constraints)


@pytest.mark.parametrize(
    ("target", "decision", "head", "tail", "bridge", "execution_allowed"),
    [
        (8.0, "USE_ONE_ASSET", 0.0, 0.0, 0.0, True),
        (6.0, "TRIM_TO_TARGET", 1.0, 1.0, 0.0, True),
        (9.0, "USE_NATIVE_OR_SUPPORTING_BRIDGE", 0.0, 0.0, 1.0, True),
        (12.0, "REPLAN_BEFORE_PROVIDER_EXECUTION", 0.0, 0.0, 0.0, False),
    ],
)
def test_fixed_duration_fit_preserves_narration_and_forbids_speed_or_loop(
    target, decision, head, tail, bridge, execution_allowed, policy_snapshot
):
    result = VeoFixedDurationPlanner(VeoDurationFitThresholds.from_policy(policy_snapshot)).decide(target)

    assert result.decision == decision
    assert (result.trim_head_seconds, result.trim_tail_seconds, result.bridge_duration_seconds) == (
        head,
        tail,
        bridge,
    )
    assert result.provider_execution_allowed is execution_allowed
    assert result.duration_fit_thresholds == VeoDurationFitThresholds.from_policy(policy_snapshot).model_dump()
    assert result.narration_timing_changed is False
    assert result.speed_change_allowed is False
    assert result.loop_allowed is False


def test_contextual_services_reject_missing_injected_policy(visual_direction):
    with pytest.raises(ValueError, match="CONTEXTUAL_VISUAL_POLICY_REQUIRED"):
        StockCandidateRanker().rank(_asset_request(), [_candidate("pexels-policy-missing")], visual_direction=visual_direction)
    for gate in (SceneSemanticMatchGate, VisualContinuityGate, AssetAdjacencyGate, VisualEvaluationService):
        with pytest.raises(ValueError, match="VISUAL_SCORE_THRESHOLDS_REQUIRED"):
            gate(None)
    with pytest.raises(ValueError, match="VEO_DURATION_FIT_POLICY_REQUIRED"):
        VeoFixedDurationPlanner(None)
