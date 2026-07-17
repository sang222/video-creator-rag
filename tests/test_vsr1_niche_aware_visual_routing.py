from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts.native_renderer import (
    CanvasSpec,
    NativeOverlayPlan,
    NativeRenderPlan,
    NativeRenderScene,
    TextSafeRegion,
)
from app.contracts.visual_direction import SceneVisualIntent
from app.contracts.visual_routing import (
    AIImageEligibilityResult,
    ArchiveReuseResult,
    AuthoritativeOverlayContentKind,
    ExactTextNativeOverlayContract,
    NicheVisualSourceProfile,
    PexelsEligibilityResult,
    SceneVisualRealizationRequirements,
    SourceFallbackClass,
    VisualDecisionStatus,
    VisualSourceDecision,
    VisualSourceRoute,
)
from app.services.visual_source_routing import (
    AIImageEligibilityGate,
    PexelsEligibilityGate,
    VisualRealizationCompletenessGate,
    VisualSourceRouter,
    VisualSourceRoutingPolicyCatalog,
    VisualSourceRoutingPreviewService,
    stable_hash,
)
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import canonical_plan_hash


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = REPO_ROOT / "app" / "services" / "visual_source_routing.py"
POLICY_PATH = REPO_ROOT / "config" / "visual_source_routing_policy_catalog.yaml"


def _requirements_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        # Canonical SceneVisualIntent lineage.
        "scene_id": "scene-001",
        "semantic_intent": "Show a small team reviewing a practical workflow.",
        "target_duration_seconds": 6.0,
        "aspect_ratio": "16:9",
        "crop_safety_required": True,
        "previous_scene_summary": None,
        "next_scene_summary": None,
        "subject_action": "reviewing a plan",
        "camera_angle": "eye-level",
        "shot_size": "medium",
        # VSR1 strict feature snapshot.
        "segment_ids": ["segment-001"],
        "niche_visual_source_profile": NicheVisualSourceProfile.STOCK_ASSISTED,
        "scene_class": "context",
        "narrative_function": "context",
        "scene_meaning": "A small team can make the workflow concrete.",
        "editorial_intent": "Ground the explanation in observable work.",
        "filmability_score": 0.90,
        "stock_searchability_score": 0.90,
        "required_specificity": 0.20,
        "custom_composition_score": 0.10,
        "exact_text_dependency": 0.0,
        "exact_number_dependency": 0.0,
        "named_workflow_nodes_required": False,
        "diagram_clarity_advantage": 0.10,
        "brand_or_product_dependency": 0.0,
        "product_specificity": 0.0,
        "evidence_truth_requirement": 0.0,
        "authorized_asset_available": False,
        "identity_consistency_requirement": 0.0,
        "recurring_identity_required": False,
        "human_action_requirement": 0.80,
        "motion_semantic_value": 0.40,
        "target_aspect_ratio": "16:9",
        "minimum_resolution": "1080p",
        "crop_safety_requirement": "Protect title-safe and caption-safe regions.",
        "previous_scene_intent_ref": None,
        "next_scene_intent_ref": None,
    }
    payload.update(overrides)
    return payload


def make_requirements(**overrides: object) -> SceneVisualRealizationRequirements:
    payload = _requirements_payload(**overrides)
    payload["content_hash"] = stable_hash(payload)
    return SceneVisualRealizationRequirements.model_validate(payload)


def test_policy_catalog_is_complete_inactive_and_fixture_only() -> None:
    catalog = VisualSourceRoutingPolicyCatalog(POLICY_PATH)
    item = catalog.typed_item

    assert len(NicheVisualSourceProfile) == 4
    assert {entry.key for entry in item.niche_visual_source_profiles} == set(
        NicheVisualSourceProfile
    )
    assert len(VisualSourceRoute) == 13
    assert {entry.key for entry in item.source_routes} == set(VisualSourceRoute)
    assert item.lifecycle.state == "INACTIVE"
    assert item.lifecycle.fixture_only is True
    assert item.lifecycle.channel_profile_binding_allowed is False
    assert item.lifecycle.provider_execution_allowed is False
    assert catalog.fixture_profile("small_team_ai_stock_assisted_preview") == (
        NicheVisualSourceProfile.STOCK_ASSISTED
    )
    fixture = item.fixtures[0]
    assert fixture.channel_key == "small-team-ai"
    assert fixture.fixture_only is True
    assert fixture.active is False
    assert fixture.channel_profile_version_binding is None
    assert fixture.provider_execution_allowed is False


def test_requirements_inherit_canonical_scene_intent_and_are_hash_bound() -> None:
    assert issubclass(SceneVisualRealizationRequirements, SceneVisualIntent)
    assert set(SceneVisualIntent.model_fields).issubset(
        SceneVisualRealizationRequirements.model_fields
    )

    requirements = make_requirements()
    assert requirements.semantic_intent
    assert requirements.content_hash == stable_hash(
        requirements.model_dump(mode="json", exclude={"content_hash"})
    )
    assert VisualRealizationCompletenessGate().evaluate(requirements).passed is True


def test_router_returns_one_deterministic_hash_bound_decision() -> None:
    router = VisualSourceRouter()
    requirements = make_requirements()

    first = router.route(requirements)
    second = router.route(requirements)

    assert isinstance(first, VisualSourceDecision)
    assert first == second
    assert first.content_hash == second.content_hash
    assert first.content_hash == stable_hash(
        first.model_dump(mode="json", exclude={"content_hash"})
    )
    assert first.preferred_source_route == VisualSourceRoute.PEXELS_VIDEO
    assert first.preferred_source_route not in first.allowed_fallback_routes
    assert first.preferred_source_route not in first.forbidden_fallback_routes
    assert set(first.allowed_fallback_routes).isdisjoint(first.forbidden_fallback_routes)
    assert first.provider_execution_allowed is False


def test_pexels_eligible_selects_video_or_photo_from_scene_features() -> None:
    router = VisualSourceRouter()
    video = router.route(make_requirements(human_action_requirement=0.80))
    photo = router.route(
        make_requirements(
            scene_id="scene-photo",
            human_action_requirement=0.0,
            motion_semantic_value=0.0,
        )
    )

    assert video.preferred_source_route == VisualSourceRoute.PEXELS_VIDEO
    assert photo.preferred_source_route == VisualSourceRoute.PEXELS_PHOTO
    assert video.fallback_class == SourceFallbackClass.PEXELS_ONLY
    assert photo.fallback_class == SourceFallbackClass.PEXELS_ONLY


def test_pexels_supporting_only_cannot_become_global_primary() -> None:
    requirements = make_requirements(
        filmability_score=0.60,
        stock_searchability_score=0.60,
        custom_composition_score=0.40,
    )
    assessment = PexelsEligibilityGate().evaluate(requirements)
    contextual = VisualSourceRouter().route(requirements)
    primary = VisualSourceRouter().route(
        make_requirements(
            scene_id="scene-primary",
            narrative_function="primary_explanation",
            scene_class="generic",
            filmability_score=0.60,
            stock_searchability_score=0.60,
            custom_composition_score=0.40,
        )
    )

    assert assessment.result == PexelsEligibilityResult.PEXELS_SUPPORTING_ONLY
    assert assessment.supporting_only is True
    assert contextual.preferred_source_route in {
        VisualSourceRoute.PEXELS_VIDEO,
        VisualSourceRoute.PEXELS_PHOTO,
    }
    assert primary.preferred_source_route not in {
        VisualSourceRoute.PEXELS_VIDEO,
        VisualSourceRoute.PEXELS_PHOTO,
    }


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"exact_text_dependency": 0.60}, "PEXELS_EXACT_TEXT_AUTHORITY_PROHIBITED"),
        ({"exact_number_dependency": 0.60}, "PEXELS_EXACT_NUMBER_AUTHORITY_PROHIBITED"),
        ({"evidence_truth_requirement": 0.60}, "PEXELS_EVIDENCE_TRUTH_PROHIBITED"),
        ({"scene_class": "actual_ui"}, "PEXELS_SCENE_TRUTH_OR_MECHANISM_CLASS_PROHIBITED"),
        ({"scene_class": "mechanism"}, "PEXELS_SCENE_TRUTH_OR_MECHANISM_CLASS_PROHIBITED"),
        ({"named_workflow_nodes_required": True}, "PEXELS_NAMED_WORKFLOW_PROHIBITED"),
    ],
)
def test_pexels_hard_prohibitions(
    overrides: dict[str, object], reason_code: str
) -> None:
    assessment = PexelsEligibilityGate().evaluate(make_requirements(**overrides))

    assert assessment.result == PexelsEligibilityResult.PEXELS_PROHIBITED
    assert assessment.eligible_routes == []
    assert reason_code in assessment.reason_codes


def test_evidence_truth_requires_authorized_asset_and_provenance() -> None:
    router = VisualSourceRouter()
    requirements = make_requirements(
        scene_class="evidence",
        evidence_truth_requirement=0.90,
        authorized_asset_available=True,
    )

    authorized = router.route(
        requirements,
        authorization_evidence_refs=["rights-receipt://asset-001"],
    )
    missing_provenance = router.route(requirements)

    assert authorized.preferred_source_route == (
        VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET
    )
    assert authorized.fallback_class == SourceFallbackClass.AUTHORIZED_ASSET_ONLY
    assert missing_provenance.preferred_source_route == VisualSourceRoute.UNRESOLVED_BLOCK
    assert missing_provenance.decision_status == VisualDecisionStatus.BLOCKED
    assert "EVIDENCE_TRUTH_SOURCE_UNRESOLVED" in missing_provenance.block_reason_codes


def test_diagram_and_native_motion_outrank_generic_sources() -> None:
    router = VisualSourceRouter()
    diagram = router.route(
        make_requirements(
            scene_id="scene-diagram",
            scene_class="mechanism",
            named_workflow_nodes_required=True,
            diagram_clarity_advantage=0.90,
            motion_semantic_value=0.20,
        )
    )
    motion = router.route(
        make_requirements(
            scene_id="scene-native-motion",
            scene_class="mechanism",
            named_workflow_nodes_required=True,
            diagram_clarity_advantage=0.90,
            motion_semantic_value=0.90,
        )
    )

    assert diagram.preferred_source_route == VisualSourceRoute.NATIVE_DIAGRAM
    assert motion.preferred_source_route == VisualSourceRoute.NATIVE_MOTION_GRAPHIC
    assert diagram.fallback_class == SourceFallbackClass.NATIVE_ONLY
    assert motion.fallback_class == SourceFallbackClass.NATIVE_ONLY


@pytest.mark.parametrize(
    ("field", "route"),
    [
        ("exact_text_dependency", VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC),
        ("exact_number_dependency", VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC),
    ],
)
def test_exact_text_and_numbers_route_to_native_authority(
    field: str, route: VisualSourceRoute
) -> None:
    decision = VisualSourceRouter().route(make_requirements(**{field: 0.60}))

    assert decision.preferred_source_route == route
    assert decision.provider_execution_required is False
    assert decision.provider_execution_allowed is False
    assert "EXACT_CONTENT_REQUIRES_NATIVE_AUTHORITY" in decision.routing_reason_codes


def test_exact_text_native_overlay_contract_and_safe_region_are_route_bound() -> None:
    decision = VisualSourceRouter().route(make_requirements(exact_text_dependency=0.60))
    exact_payload = {
        "scene_id": decision.scene_id,
        "source_decision_ref": "visual-source-decision://scene-001",
        "source_decision_hash": decision.content_hash,
        "preferred_source_route": decision.preferred_source_route,
        "exact_text_required": True,
        "exact_number_required": True,
        "forbidden_generated_text": True,
        "forbidden_generated_logo": True,
        "forbidden_generated_fake_ui": True,
        "native_overlay_required": True,
        "authoritative_content_kinds": [
            AuthoritativeOverlayContentKind.HEADLINE,
            AuthoritativeOverlayContentKind.NUMBER,
        ],
        "authoritative_content_refs": ["script://headline", "claim://number"],
    }
    exact = ExactTextNativeOverlayContract(
        **exact_payload,
        content_hash=stable_hash(exact_payload),
    )
    safe_region = TextSafeRegion(
        id="headline-safe",
        x=0.10,
        y=0.10,
        width=0.80,
        height=0.30,
        purpose="Exact headline and number",
        minimum_contrast_requirement=4.5,
        alignment="center",
    )
    overlay_payload = {
        "plan_id": "native-overlay://scene-001",
        "scene_id": decision.scene_id,
        "source_decision_ref": exact.source_decision_ref,
        "source_decision_hash": decision.content_hash,
        "preferred_source_route": decision.preferred_source_route,
        "exact_text_contract": exact,
        "text_safe_regions": [safe_region],
        "reserved_overlay_regions": [],
        "overlay_content_refs": exact.authoritative_content_refs,
        "native_overlay_required": True,
    }
    overlay = NativeOverlayPlan(
        **overlay_payload,
        content_hash=stable_hash(overlay_payload),
    )

    assert overlay.exact_text_contract.forbidden_generated_text is True
    assert overlay.exact_text_contract.forbidden_generated_logo is True
    assert overlay.exact_text_contract.forbidden_generated_fake_ui is True
    assert overlay.native_overlay_required is True
    assert overlay.text_safe_regions == [safe_region]


def test_ai_image_route_is_planning_only_and_provider_inactive() -> None:
    requirements = make_requirements(
        niche_visual_source_profile=NicheVisualSourceProfile.GENERATED_EDITORIAL_FIRST,
        filmability_score=0.20,
        stock_searchability_score=0.20,
        custom_composition_score=0.90,
        human_action_requirement=0.0,
        motion_semantic_value=0.0,
    )
    decision = VisualSourceRouter().route(
        requirements,
        rights_policy_allows_generation=True,
    )
    preview = VisualSourceRoutingPreviewService().preview(
        requirements,
        rights_policy_allows_generation=True,
    )

    assert decision.preferred_source_route == VisualSourceRoute.AI_GENERATED_IMAGE
    assert decision.decision_status == VisualDecisionStatus.PLANNED
    assert decision.provider_execution_required is True
    assert decision.provider_execution_allowed is False
    assert decision.human_approval_required is True
    assert "IMAGE_PROVIDER_ROUTE_NOT_YET_ACTIVE" in decision.routing_reason_codes
    assert preview["preview_only"] is True
    assert preview["provider_execution_allowed"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"identity_consistency_requirement": 0.80},
        {"recurring_identity_required": True},
        {"scene_class": "actual_ui", "product_specificity": 0.80},
    ],
)
def test_ai_image_gate_fails_closed_for_rights_truth_and_identity(
    overrides: dict[str, object]
) -> None:
    requirements = make_requirements(
        filmability_score=0.10,
        stock_searchability_score=0.10,
        custom_composition_score=0.90,
        **overrides,
    )
    rights_allowed = bool(overrides)
    assessment = AIImageEligibilityGate().evaluate(
        requirements,
        rights_policy_allows_generation=rights_allowed,
    )

    assert assessment.result == AIImageEligibilityResult.AI_IMAGE_PROHIBITED
    assert assessment.planning_routes == []
    assert assessment.provider_execution_allowed is False


def test_pexels_result_or_failure_is_not_router_input_and_cannot_auto_open_ai() -> None:
    route_parameters = set(inspect.signature(VisualSourceRouter.route).parameters)
    forbidden_observations = {
        "pexels_result",
        "pexels_search_result",
        "pexels_failure",
        "provider_response",
        "provider_error",
    }
    assert route_parameters.isdisjoint(forbidden_observations)

    decision = VisualSourceRouter().route(
        make_requirements(custom_composition_score=0.10),
        rights_policy_allows_generation=True,
    )
    assert decision.preferred_source_route == VisualSourceRoute.PEXELS_VIDEO
    assert not set(decision.allowed_fallback_routes) & {
        VisualSourceRoute.AI_GENERATED_IMAGE,
        VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
    }
    snapshot = decision.input_feature_snapshot
    assert snapshot["provider_observations_consumed"] is False
    assert snapshot["pexels_search_result_consumed"] is False
    assert snapshot["auto_pexels_to_ai_failover"] is False


def test_veo_requires_high_semantic_motion_allowed_class_insufficiency_and_cost() -> None:
    router = VisualSourceRouter()
    baseline = make_requirements(
        scene_class="hero",
        narrative_function="hero",
        filmability_score=0.10,
        stock_searchability_score=0.10,
        custom_composition_score=0.40,
        human_action_requirement=0.0,
        motion_semantic_value=0.90,
    )
    eligible = router.route(
        baseline,
        still_or_native_motion_sufficient=False,
        future_cost_class_allows_veo=True,
    )
    image_to_video = router.route(
        baseline,
        still_or_native_motion_sufficient=False,
        future_cost_class_allows_veo=True,
        veo_reference_image_available=True,
    )
    insufficient_motion = router.route(
        make_requirements(
            scene_id="scene-low-motion",
            scene_class="hero",
            narrative_function="hero",
            filmability_score=0.10,
            stock_searchability_score=0.10,
            custom_composition_score=0.40,
            human_action_requirement=0.0,
            motion_semantic_value=0.60,
        ),
        still_or_native_motion_sufficient=False,
        future_cost_class_allows_veo=True,
    )
    still_sufficient = router.route(
        baseline,
        still_or_native_motion_sufficient=True,
        future_cost_class_allows_veo=True,
    )
    cost_not_allowed = router.route(
        baseline,
        still_or_native_motion_sufficient=False,
        future_cost_class_allows_veo=False,
    )

    assert eligible.preferred_source_route == VisualSourceRoute.VEO_TEXT_TO_VIDEO
    assert image_to_video.preferred_source_route == VisualSourceRoute.VEO_IMAGE_TO_VIDEO
    assert eligible.estimated_cost_class == "HIGH"
    assert eligible.provider_execution_allowed is False
    assert insufficient_motion.preferred_source_route not in {
        VisualSourceRoute.VEO_TEXT_TO_VIDEO,
        VisualSourceRoute.VEO_IMAGE_TO_VIDEO,
    }
    assert still_sufficient.preferred_source_route not in {
        VisualSourceRoute.VEO_TEXT_TO_VIDEO,
        VisualSourceRoute.VEO_IMAGE_TO_VIDEO,
    }
    assert cost_not_allowed.preferred_source_route not in {
        VisualSourceRoute.VEO_TEXT_TO_VIDEO,
        VisualSourceRoute.VEO_IMAGE_TO_VIDEO,
    }


def test_archive_reuse_requires_rights_cooldown_originality_and_current_truth() -> None:
    router = VisualSourceRouter()
    requirements = make_requirements()
    eligible = router.assess_archive(
        requirements,
        matched_asset_ref="archive://asset-001",
        reuse_count=1,
        authorization_evidence_refs=["rights-receipt://archive-asset-001"],
        semantic_fit_passed=True,
        rights_scope_permits_reuse=True,
        reuse_cooldown_permits=True,
        originality_policy_passed=True,
        asset_truth_current=True,
    )
    rights_failed = router.assess_archive(
        requirements,
        matched_asset_ref="archive://asset-001",
        reuse_count=1,
        authorization_evidence_refs=["rights-receipt://archive-asset-001"],
        semantic_fit_passed=True,
        rights_scope_permits_reuse=False,
        reuse_cooldown_permits=True,
        originality_policy_passed=True,
        asset_truth_current=True,
    )
    originality_failed = router.assess_archive(
        requirements,
        matched_asset_ref="archive://asset-001",
        reuse_count=1,
        authorization_evidence_refs=["rights-receipt://archive-asset-001"],
        semantic_fit_passed=True,
        rights_scope_permits_reuse=True,
        reuse_cooldown_permits=True,
        originality_policy_passed=False,
        asset_truth_current=True,
    )

    assert eligible.result == ArchiveReuseResult.ELIGIBLE
    assert router.route(
        requirements, archive_assessment=eligible
    ).preferred_source_route == VisualSourceRoute.ARCHIVED_ASSET_REUSE
    assert rights_failed.result == ArchiveReuseResult.INELIGIBLE
    assert originality_failed.result == ArchiveReuseResult.INELIGIBLE
    assert "ARCHIVE_RIGHTS_SCOPE_FAIL" in rights_failed.reason_codes
    assert "ARCHIVE_ORIGINALITY_POLICY_FAIL" in originality_failed.reason_codes
    assert router.route(
        requirements, archive_assessment=rights_failed
    ).preferred_source_route != VisualSourceRoute.ARCHIVED_ASSET_REUSE


def test_fallback_classes_are_explicit_forbidden_pairs_are_enforced() -> None:
    catalog = VisualSourceRoutingPolicyCatalog()
    decision = VisualSourceRouter(catalog).route(make_requirements())
    ai_routes = {
        VisualSourceRoute.AI_GENERATED_IMAGE,
        VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
    }

    assert set(catalog.typed_item.fallback_classes) == set(SourceFallbackClass)
    assert decision.fallback_class == SourceFallbackClass.PEXELS_ONLY
    assert not set(decision.allowed_fallback_routes) & ai_routes
    assert ai_routes.issubset(set(decision.forbidden_fallback_routes))
    automatic_blocks = {
        (pair.from_route, pair.to_route)
        for pair in catalog.typed_item.forbidden_fallback_pairs
        if pair.scope == "AUTOMATIC_AFTER_SEARCH_FAILURE"
    }
    assert {
        (VisualSourceRoute.PEXELS_VIDEO, VisualSourceRoute.AI_GENERATED_IMAGE),
        (
            VisualSourceRoute.PEXELS_VIDEO,
            VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
        ),
        (VisualSourceRoute.PEXELS_PHOTO, VisualSourceRoute.AI_GENERATED_IMAGE),
        (
            VisualSourceRoute.PEXELS_PHOTO,
            VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
        ),
    }.issubset(automatic_blocks)

    invalid = decision.model_dump(mode="json")
    invalid["allowed_fallback_routes"] = [VisualSourceRoute.AI_GENERATED_IMAGE.value]
    invalid["forbidden_fallback_routes"] = [
        route.value
        for route in decision.forbidden_fallback_routes
        if route != VisualSourceRoute.AI_GENERATED_IMAGE
    ]
    with pytest.raises(ValidationError, match="VSR1_PEXELS_ONLY_FALLBACK_INVALID"):
        VisualSourceDecision.model_validate(invalid)


def test_resolution_below_1080_and_missing_routing_inputs_fail_closed() -> None:
    gate = VisualRealizationCompletenessGate()
    below_minimum = _requirements_payload(minimum_resolution="720p")
    below_minimum["content_hash"] = stable_hash(below_minimum)
    missing = _requirements_payload()
    del missing["editorial_intent"]
    missing["content_hash"] = stable_hash(missing)

    below_result = gate.evaluate(below_minimum)
    missing_result = gate.evaluate(missing)

    assert below_result.passed is False
    assert missing_result.passed is False
    assert "VISUAL_REALIZATION_REQUIREMENTS_INVALID" in below_result.reason_codes
    assert "VISUAL_REALIZATION_REQUIREMENTS_INVALID" in missing_result.reason_codes
    with pytest.raises(ValidationError):
        SceneVisualRealizationRequirements.model_validate(below_minimum)
    with pytest.raises(ValidationError):
        SceneVisualRealizationRequirements.model_validate(missing)


@pytest.mark.parametrize(
    ("example", "overrides", "expected_route"),
    [
        pytest.param(
            "travel_street",
            {
                "scene_class": "observable_reality",
                "narrative_function": "establishing",
                "semantic_intent": "Show travelers walking through a real city street.",
                "scene_meaning": "A real travel street establishes place and movement.",
                "human_action_requirement": 0.90,
                "motion_semantic_value": 0.50,
            },
            VisualSourceRoute.PEXELS_VIDEO,
            id="travel-street-to-pexels-video",
        ),
        pytest.param(
            "static_real_object",
            {
                "scene_class": "observable_reality",
                "narrative_function": "object_detail",
                "semantic_intent": "Show a static real notebook on a desk.",
                "scene_meaning": "The real object is observable without semantic motion.",
                "human_action_requirement": 0.0,
                "motion_semantic_value": 0.0,
            },
            VisualSourceRoute.PEXELS_PHOTO,
            id="static-real-object-to-pexels-photo",
        ),
    ],
)
def test_stock_native_fixture_matrix(
    example: str,
    overrides: dict[str, object],
    expected_route: VisualSourceRoute,
) -> None:
    requirements = make_requirements(
        scene_id=f"stock-native-{example}",
        niche_visual_source_profile=NicheVisualSourceProfile.STOCK_NATIVE,
        **overrides,
    )
    decision = VisualSourceRouter().route(requirements)

    assert decision.niche_visual_source_profile == NicheVisualSourceProfile.STOCK_NATIVE
    assert decision.preferred_source_route == expected_route
    assert decision.provider_execution_allowed is False


@pytest.mark.parametrize(
    ("example", "profile", "overrides", "expected_route"),
    [
        pytest.param(
            "approval_bottleneck",
            NicheVisualSourceProfile.STOCK_ASSISTED,
            {
                "scene_class": "mechanism",
                "narrative_function": "explain_bottleneck",
                "semantic_intent": "Explain where an approval bottleneck forms.",
                "scene_meaning": "A decision node blocks downstream work.",
                "diagram_clarity_advantage": 0.90,
                "human_action_requirement": 0.0,
                "motion_semantic_value": 0.20,
            },
            VisualSourceRoute.NATIVE_DIAGRAM,
            id="approval-bottleneck",
        ),
        pytest.param(
            "manual_handoff",
            NicheVisualSourceProfile.STOCK_ASSISTED,
            {
                "scene_class": "process",
                "narrative_function": "explain_handoff",
                "semantic_intent": "Explain a manual handoff between named roles.",
                "scene_meaning": "Ownership moves from operator to reviewer.",
                "named_workflow_nodes_required": True,
                "diagram_clarity_advantage": 0.90,
                "human_action_requirement": 0.0,
                "motion_semantic_value": 0.20,
            },
            VisualSourceRoute.NATIVE_DIAGRAM,
            id="manual-handoff",
        ),
        pytest.param(
            "context_switching",
            NicheVisualSourceProfile.STOCK_ASSISTED,
            {
                "scene_class": "context",
                "narrative_function": "context",
                "semantic_intent": "Show a real worker switching between tasks.",
                "scene_meaning": "Observable human actions establish context switching.",
                "human_action_requirement": 0.90,
                "motion_semantic_value": 0.50,
            },
            VisualSourceRoute.PEXELS_VIDEO,
            id="context-switching",
        ),
        pytest.param(
            "named_system_flow",
            NicheVisualSourceProfile.STOCK_ASSISTED,
            {
                "scene_class": "mechanism",
                "narrative_function": "system_flow",
                "semantic_intent": "Explain a named intake, approval, and delivery flow.",
                "scene_meaning": "Named nodes and arrows carry the system meaning.",
                "named_workflow_nodes_required": True,
                "diagram_clarity_advantage": 0.95,
                "human_action_requirement": 0.0,
                "motion_semantic_value": 0.20,
            },
            VisualSourceRoute.NATIVE_DIAGRAM,
            id="named-system-flow",
        ),
        pytest.param(
            "before_after",
            NicheVisualSourceProfile.STOCK_ASSISTED,
            {
                "scene_class": "comparison",
                "narrative_function": "before_after",
                "semantic_intent": "Compare the workflow before and after automation.",
                "scene_meaning": "Exact before and after labels own the comparison.",
                "filmability_score": 0.20,
                "stock_searchability_score": 0.20,
                "exact_text_dependency": 0.80,
                "human_action_requirement": 0.0,
                "motion_semantic_value": 0.0,
            },
            VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC,
            id="before-after",
        ),
        pytest.param(
            "hours_saved",
            NicheVisualSourceProfile.STOCK_ASSISTED,
            {
                "scene_class": "data",
                "narrative_function": "illustrative_result",
                "semantic_intent": "Display an illustrative hours-saved calculation.",
                "scene_meaning": "The exact number must remain native and qualified.",
                "filmability_score": 0.20,
                "stock_searchability_score": 0.20,
                "exact_number_dependency": 0.95,
                "human_action_requirement": 0.0,
                "motion_semantic_value": 0.0,
            },
            VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC,
            id="hours-saved",
        ),
        pytest.param(
            "knowledge_silos",
            NicheVisualSourceProfile.GENERATED_EDITORIAL_FIRST,
            {
                "scene_class": "metaphor",
                "narrative_function": "conceptual_metaphor",
                "semantic_intent": "Show labeled knowledge silos as an editorial metaphor.",
                "scene_meaning": "A custom composition plus native labels explains isolation.",
                "filmability_score": 0.20,
                "stock_searchability_score": 0.20,
                "custom_composition_score": 0.90,
                "exact_text_dependency": 0.30,
                "human_action_requirement": 0.0,
                "motion_semantic_value": 0.0,
            },
            VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
            id="knowledge-silos",
        ),
        pytest.param(
            "automation_leverage",
            NicheVisualSourceProfile.GENERATED_EDITORIAL_FIRST,
            {
                "scene_class": "metaphor",
                "narrative_function": "conceptual_metaphor",
                "semantic_intent": "Show automation leverage as an editorial metaphor.",
                "scene_meaning": "A custom authored composition communicates leverage.",
                "filmability_score": 0.20,
                "stock_searchability_score": 0.20,
                "custom_composition_score": 0.90,
                "human_action_requirement": 0.0,
                "motion_semantic_value": 0.0,
            },
            VisualSourceRoute.AI_GENERATED_IMAGE,
            id="automation-leverage",
        ),
    ],
)
def test_small_team_named_example_matrix(
    example: str,
    profile: NicheVisualSourceProfile,
    overrides: dict[str, object],
    expected_route: VisualSourceRoute,
) -> None:
    """Cover all eight named small-team fixture meanings explicitly."""

    requirements = make_requirements(
        scene_id=f"small-team-{example}",
        niche_visual_source_profile=profile,
        **overrides,
    )
    decision = VisualSourceRouter().route(
        requirements,
        rights_policy_allows_generation=True,
    )

    assert decision.niche_visual_source_profile == profile
    assert decision.preferred_source_route == expected_route
    assert decision.provider_execution_allowed is False
    if expected_route in {
        VisualSourceRoute.AI_GENERATED_IMAGE,
        VisualSourceRoute.AI_GENERATED_IMAGE_WITH_NATIVE_OVERLAY,
    }:
        assert decision.decision_status == VisualDecisionStatus.PLANNED
        assert "IMAGE_PROVIDER_ROUTE_NOT_YET_ACTIVE" in decision.routing_reason_codes


@pytest.mark.parametrize(
    ("authorization_refs", "expected_route"),
    [
        pytest.param(
            ["rights-receipt://crm-ui-001"],
            VisualSourceRoute.AUTHORIZED_UI_OR_PRODUCT_ASSET,
            id="actual-crm-ui-authorized",
        ),
        pytest.param(
            [],
            VisualSourceRoute.UNRESOLVED_BLOCK,
            id="actual-crm-ui-evidence-missing",
        ),
    ],
)
def test_authority_asset_first_crm_ui_fixture(
    authorization_refs: list[str], expected_route: VisualSourceRoute
) -> None:
    requirements = make_requirements(
        scene_id="authority-actual-crm-ui",
        niche_visual_source_profile=NicheVisualSourceProfile.AUTHORITY_ASSET_FIRST,
        scene_class="actual_ui",
        narrative_function="product_evidence",
        semantic_intent="Show the actual CRM approval screen.",
        scene_meaning="Only authorized CRM pixels may substantiate this UI claim.",
        filmability_score=0.0,
        stock_searchability_score=0.0,
        product_specificity=0.95,
        brand_or_product_dependency=0.95,
        evidence_truth_requirement=0.95,
        authorized_asset_available=True,
        human_action_requirement=0.0,
        motion_semantic_value=0.0,
    )
    decision = VisualSourceRouter().route(
        requirements,
        authorization_evidence_refs=authorization_refs,
    )

    assert decision.niche_visual_source_profile == (
        NicheVisualSourceProfile.AUTHORITY_ASSET_FIRST
    )
    assert decision.preferred_source_route == expected_route
    assert decision.provider_execution_allowed is False
    if not authorization_refs:
        assert decision.decision_status == VisualDecisionStatus.BLOCKED
        assert "EVIDENCE_TRUTH_SOURCE_UNRESOLVED" in decision.block_reason_codes


@pytest.mark.parametrize(
    ("motion_value", "still_sufficient", "cost_allowed", "expected_route"),
    [
        pytest.param(
            0.90,
            False,
            True,
            VisualSourceRoute.VEO_TEXT_TO_VIDEO,
            id="veo-high-semantic-motion",
        ),
        pytest.param(
            0.60,
            False,
            True,
            VisualSourceRoute.UNRESOLVED_BLOCK,
            id="veo-low-semantic-motion",
        ),
    ],
)
def test_veo_high_low_fixture_matrix(
    motion_value: float,
    still_sufficient: bool,
    cost_allowed: bool,
    expected_route: VisualSourceRoute,
) -> None:
    requirements = make_requirements(
        scene_id=f"veo-boundary-{motion_value}",
        niche_visual_source_profile=NicheVisualSourceProfile.GENERATED_EDITORIAL_FIRST,
        scene_class="hero",
        narrative_function="hero",
        semantic_intent="Use motion only when motion carries the scene meaning.",
        scene_meaning="Semantic motion is evaluated before a future video route.",
        filmability_score=0.10,
        stock_searchability_score=0.10,
        custom_composition_score=0.40,
        human_action_requirement=0.0,
        motion_semantic_value=motion_value,
    )
    decision = VisualSourceRouter().route(
        requirements,
        still_or_native_motion_sufficient=still_sufficient,
        future_cost_class_allows_veo=cost_allowed,
    )

    assert decision.preferred_source_route == expected_route
    assert decision.provider_execution_allowed is False


def test_small_team_fixture_is_stock_assisted_and_routes_representative_scenes() -> None:
    catalog = VisualSourceRoutingPolicyCatalog()
    router = VisualSourceRouter(catalog)
    assert catalog.fixture_profile("small_team_ai_stock_assisted_preview") == (
        NicheVisualSourceProfile.STOCK_ASSISTED
    )

    stock = router.route(make_requirements())
    mechanism = router.route(
        make_requirements(
            scene_id="scene-workflow",
            scene_class="mechanism",
            narrative_function="explain_mechanism",
            named_workflow_nodes_required=True,
            diagram_clarity_advantage=0.90,
        )
    )
    exact = router.route(
        make_requirements(
            scene_id="scene-number",
            scene_class="data",
            exact_number_dependency=0.90,
        )
    )

    assert stock.niche_visual_source_profile == NicheVisualSourceProfile.STOCK_ASSISTED
    assert stock.preferred_source_route == VisualSourceRoute.PEXELS_VIDEO
    assert mechanism.preferred_source_route == VisualSourceRoute.NATIVE_DIAGRAM
    assert exact.preferred_source_route == VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC


def test_service_has_no_fixture_hardcode_or_network_provider_platform_calls() -> None:
    source = SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    assert "small-team-ai" not in source
    assert imported_roots.isdisjoint(
        {
            "aiohttp",
            "boto3",
            "google",
            "googleapiclient",
            "httpx",
            "requests",
            "urllib",
        }
    )
    lowered = source.lower()
    assert "drive.files" not in lowered
    assert "youtube.upload" not in lowered
    assert "api.pexels.com" not in lowered
    assert "generativeai" not in lowered
    assert "gemini" not in lowered


def test_native_motion_compiler_propagates_vsr1_bindings_without_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirements = make_requirements(
        scene_id="native-compiler-hours-saved",
        scene_class="data",
        narrative_function="illustrative_result",
        exact_number_dependency=0.95,
        filmability_score=0.20,
        stock_searchability_score=0.20,
        human_action_requirement=0.0,
        motion_semantic_value=0.0,
    )
    decision = VisualSourceRouter().route(requirements)
    assert decision.preferred_source_route == VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC
    assert decision.provider_execution_allowed is False

    decision_ref = "visual-source-decision://native-compiler-hours-saved"
    authoritative_refs = ["claim://hours-saved-illustrative"]
    exact_payload = {
        "scene_id": requirements.scene_id,
        "source_decision_ref": decision_ref,
        "source_decision_hash": decision.content_hash,
        "preferred_source_route": decision.preferred_source_route,
        "exact_text_required": False,
        "exact_number_required": True,
        "forbidden_generated_text": True,
        "forbidden_generated_logo": True,
        "forbidden_generated_fake_ui": True,
        "native_overlay_required": True,
        "authoritative_content_kinds": [AuthoritativeOverlayContentKind.NUMBER],
        "authoritative_content_refs": authoritative_refs,
    }
    exact = ExactTextNativeOverlayContract(
        **exact_payload,
        content_hash=stable_hash(exact_payload),
    )
    safe_region = TextSafeRegion(
        id="data-safe",
        x=0.10,
        y=0.12,
        width=0.80,
        height=0.32,
        purpose="Authoritative illustrative number",
        minimum_contrast_requirement=4.5,
        alignment="center",
    )
    overlay_payload = {
        "plan_id": "native-overlay://native-compiler-hours-saved",
        "scene_id": requirements.scene_id,
        "source_decision_ref": decision_ref,
        "source_decision_hash": decision.content_hash,
        "preferred_source_route": decision.preferred_source_route,
        "exact_text_contract": exact,
        "text_safe_regions": [safe_region],
        "reserved_overlay_regions": [],
        "overlay_content_refs": authoritative_refs,
        "native_overlay_required": True,
    }
    overlay = NativeOverlayPlan(
        **overlay_payload,
        content_hash=stable_hash(overlay_payload),
    )
    eligibility_refs = [
        "visual-routing-gate://exact-content-native-authority",
        "visual-routing-gate://pexels-prohibited",
    ]
    scene = NativeRenderScene(
        scene_id=requirements.scene_id,
        source_segment_ids=requirements.segment_ids,
        narration_start_ms=0,
        narration_end_ms=4000,
        duration_ms=4000,
        visual_treatment="DATA_CARD",
        layout_type="DATA",
        animation_type="HOLD_STATIC",
        originality_role="ILLUSTRATIVE_RESULT",
        visual_routing_mode="VSR1_STRICT",
        source_decision_ref=decision_ref,
        source_decision_hash=decision.content_hash,
        preferred_source_route=decision.preferred_source_route,
        exact_text_required=False,
        exact_number_required=True,
        forbidden_generated_text=True,
        forbidden_generated_logo=True,
        forbidden_generated_fake_ui=True,
        text_safe_regions=[safe_region],
        reserved_overlay_regions=[],
        eligibility_gate_refs=eligibility_refs,
        native_overlay_required=True,
        native_overlay_plan=overlay,
    )
    plan = NativeRenderPlan(
        plan_id="vsr1-native-compiler-propagation",
        plan_version=1,
        package_id="vsr1-offline-fixture",
        video_project_id="offline-project",
        company_id="offline-company",
        channel_id="small-team-ai",
        channel_profile_version_id="fixture-profile-unbound",
        effective_context_snapshot_id="offline-context",
        effective_context_hash="offline-context-hash",
        format_identity_contract_ref="format://approved",
        format_identity_contract_hash="format-hash",
        episode_originality_manifest_ref="originality://pass",
        episode_originality_manifest_hash="originality-hash",
        script_ref="script://offline",
        script_hash="script-hash",
        srt_ref="fixture://captions.srt",
        srt_hash="srt-hash",
        visual_plan_ref="visual-plan://offline",
        visual_plan_hash="visual-plan-hash",
        canvas_spec=CanvasSpec(width=1920, height=1080),
        scenes=[scene],
        global_motion_policy={"motion_pack": "NativeMotionPack_v1"},
        caption_policy={"preset": "caption_burn_ass_v1"},
        audio_policy={"preset": "voice_only_basic"},
        output_profiles=["YT_LONG_1080P30_SDR_H264_VT"],
        purpose="VSR1_OFFLINE_COMPILER_PROPAGATION",
        production_eligible=False,
        status="APPROVED",
        created_at=datetime.now(UTC),
        created_by="vsr1-offline-test",
    )
    plan.content_hash = canonical_plan_hash(plan)

    compiler = NativeMotionCompiler()
    monkeypatch.setattr(
        compiler.validator,
        "validate",
        lambda *_args, **_kwargs: [],
    )
    manifest = compiler.compile(plan)

    compiled_routing = manifest.compiled_scenes[0]["visual_routing"]
    assert compiled_routing["mode"] == "VSR1_STRICT"
    assert compiled_routing["source_decision_ref"] == decision_ref
    assert compiled_routing["source_decision_hash"] == decision.content_hash
    assert compiled_routing["preferred_source_route"] == (
        VisualSourceRoute.EDITORIAL_TEXT_GRAPHIC.value
    )
    assert compiled_routing["eligibility_gate_refs"] == eligibility_refs
    assert compiled_routing["native_overlay_required"] is True
    assert manifest.overlay_schedule == [overlay.model_dump(mode="json")]
    assert manifest.overlay_schedule[0]["overlay_content_refs"] == authoritative_refs
    assert manifest.overlay_schedule[0]["exact_text_contract"][
        "authoritative_content_refs"
    ] == authoritative_refs


def test_legacy_native_render_scene_remains_valid_without_vsr_fields() -> None:
    scene = NativeRenderScene(
        scene_id="legacy-scene-001",
        source_segment_ids=["segment-001"],
        narration_start_ms=0,
        narration_end_ms=3000,
        duration_ms=3000,
        visual_treatment="NATIVE_SLIDE",
        layout_type="TITLE_AND_BODY",
        originality_role="PRIMARY_EXPLANATION",
    )

    assert scene.visual_routing_mode is None
    assert scene.source_decision_ref is None
    assert scene.preferred_source_route is None
    assert scene.native_overlay_plan is None
